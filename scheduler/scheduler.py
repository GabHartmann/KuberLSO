"""
scheduler.py — custom Kubernetes scheduler using Producer/Consumer with threads.

Architecture:
  Producer thread  → streams pod events from the Kubernetes watch API.
                      When a new Pending pod with schedulerName: custom-scheduler
                      appears it is placed in a Queue.
  Consumer thread  → takes pods from the Queue, scores all worker nodes using
                      three metrics (CPU free, RAM free, Disk free), then binds
                      the pod to the best node via the Kubernetes Binding API.

Run locally (outside the cluster) with KUBECONFIG pointing to your Kind cluster:
    python scheduler/scheduler.py
"""

import logging
import queue
import sys
import threading
import time

from kubernetes import client, config, watch

from metrics import get_node_metrics
from scoring import score_nodes
from metrics import _parse_cpu_to_millicores, _parse_memory_to_mib

SCHEDULER_NAME = "custom-scheduler"
NAMESPACE = "default"

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(
    fmt="%(asctime)s [%(threadName)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
))
handler.flush = sys.stdout.flush
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
log = logging.getLogger(__name__)


def get_pod_requests(pod) -> tuple[int, float]:
    """Returns (cpu_req_millicores, ram_req_mib) from a pod spec."""
    cpu_m = 0
    ram_mib = 0.0
    for container in pod.spec.containers:
        req = (container.resources.requests or {}) if container.resources else {}
        cpu_m += _parse_cpu_to_millicores(req.get("cpu", "0"))
        ram_mib += _parse_memory_to_mib(req.get("memory", "0Ki"))
    return cpu_m, ram_mib


def bind_pod(v1: client.CoreV1Api, pod_name: str, namespace: str, node_name: str) -> None:
    """Bind a pod to a node by creating a Binding object."""
    body = {
        "apiVersion": "v1",
        "kind": "Binding",
        "metadata": {"name": pod_name, "namespace": namespace},
        "target": {"apiVersion": "v1", "kind": "Node", "name": node_name},
    }
    v1.create_namespaced_pod_binding(
        name=pod_name,
        namespace=namespace,
        body=body,
        _preload_content=False,  # skip parsing the response — Kubernetes omits 'target' in the reply
    )
    log.info("BIND  %-30s  --> %s", pod_name, node_name)


# ── Producer ─────────────────────────────────────────────────────────────────

def producer(v1: client.CoreV1Api, pod_queue: queue.Queue, stop: threading.Event) -> None:
    """Watches the Kubernetes API for new Pending pods that request our scheduler.

    Uses watch.Watch().stream() — a persistent HTTP connection that delivers
    events instantly when a pod is created, with no polling delay.
    """
    log.info("Producer started — streaming pod events for scheduler='%s'", SCHEDULER_NAME)

    w = watch.Watch()
    seen = set()

    try:
        for event in w.stream(v1.list_namespaced_pod, namespace=NAMESPACE):
            if stop.is_set():
                w.stop()
                break

            pod   = event["object"]
            name  = pod.metadata.name
            etype = event["type"]

            # When a pod is deleted, forget it so a re-create is picked up
            if etype == "DELETED":
                seen.discard(name)
                continue

            if etype not in ("ADDED", "MODIFIED"):
                continue

            if name in seen:
                continue

            # ── custom-scheduler pods: enqueue for scheduling ─────────────────
            if pod.spec.scheduler_name == SCHEDULER_NAME and not pod.spec.node_name \
                    and pod.status.phase in (None, "Pending"):
                seen.add(name)
                req = {}
                if pod.spec.containers[0].resources:
                    req = pod.spec.containers[0].resources.requests or {}
                log.info("QUEUE  pod=%-30s  cpu=%s  mem=%s",
                         name, req.get("cpu", "?"), req.get("memory", "?"))
                pod_queue.put(pod)

    except Exception as exc:
        log.error("Producer error: %s", exc)

    log.info("Producer stopped.")


# ── Consumer ─────────────────────────────────────────────────────────────────

def consumer(v1: client.CoreV1Api, pod_queue: queue.Queue, stop: threading.Event) -> None:
    """Takes pods from the queue, scores nodes, and binds."""
    log.info("Consumer started — waiting for pods to schedule.")

    while not stop.is_set():
        try:
            pod = pod_queue.get(timeout=1)
        except queue.Empty:
            continue

        pod_name = pod.metadata.name
        namespace = pod.metadata.namespace or NAMESPACE

        try:
            cpu_req, ram_req = get_pod_requests(pod)
            node_metrics = get_node_metrics(v1)

            if not node_metrics:
                log.warning("No worker nodes found. Requeueing %s.", pod_name)
                pod_queue.put(pod)
                time.sleep(2)
                pod_queue.task_done()
                continue

            ranking = score_nodes(node_metrics, cpu_req, ram_req)

            if not ranking:
                log.warning("No node fits pod=%s. Will retry.", pod_name)
                pod_queue.put(pod)
                time.sleep(3)
                pod_queue.task_done()
                continue

            # ── visual block per pod ──────────────────────────────────────────
            print(f"\n  {'─' * 60}")
            print(f"  POD  {pod_name}   cpu={cpu_req}m  ram={ram_req:.0f}MiB")
            print(f"  {'─' * 60}")

            for rank, (node_name, score) in enumerate(ranking, start=1):
                m = node_metrics[node_name]
                marker = ">>>" if rank == 1 else "   "
                log.info("%s #%d  %-28s  score=%.3f  cpu=%dm  ram=%.0fMiB  disk=%.1fGiB",
                         marker, rank, node_name, score,
                         m["cpu_free_m"], m["ram_free_mib"], m["disk_free_gib"])

            best_node = ranking[0][0]
            bind_pod(v1, pod_name, namespace, best_node)
            print()

        except Exception as exc:
            log.error("Consumer error scheduling %s: %s", pod_name, exc)

        pod_queue.task_done()

    log.info("Consumer stopped.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # Load kubeconfig (works both in-cluster and locally)
    try:
        config.load_incluster_config()
        log.info("Running inside cluster.")
    except config.ConfigException:
        config.load_kube_config()
        log.info("Running with local kubeconfig.")

    v1 = client.CoreV1Api()

    pod_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    t_producer = threading.Thread(target=producer, args=(v1, pod_queue, stop_event), name="Producer", daemon=True)
    t_consumer = threading.Thread(target=consumer, args=(v1, pod_queue, stop_event), name="Consumer", daemon=True)

    t_producer.start()
    t_consumer.start()

    log.info("Custom scheduler running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        stop_event.set()

    t_producer.join(timeout=5)
    t_consumer.join(timeout=5)
    log.info("Done.")


if __name__ == "__main__":
    main()
