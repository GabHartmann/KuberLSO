"""
monitor.py — real-time terminal dashboard showing:
  • Pod-to-node assignments for custom-scheduler pods
  • CPU / RAM / Disk free per worker node
  • Summary table of all pods and their status

Run:  python monitor/monitor.py

Press Ctrl+C to exit.
"""

import os
import sys
import time

# Allow running from repo root or from the monitor/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduler"))

from kubernetes import client, config
from metrics import get_node_metrics, _parse_cpu_to_millicores, _parse_memory_to_mib

REFRESH_SECONDS = 3
SCHEDULER_NAME = "custom-scheduler"

# ANSI colours
BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELLOW= "\033[93m"
RED   = "\033[91m"
RESET = "\033[0m"


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def bar(used: float, total: float, width: int = 20) -> str:
    """Simple ASCII progress bar."""
    if total == 0:
        return "[" + "-" * width + "]"
    ratio = min(used / total, 1.0)
    filled = int(ratio * width)
    colour = GREEN if ratio < 0.6 else (YELLOW if ratio < 0.85 else RED)
    return f"[{colour}{'#' * filled}{RESET}{'.' * (width - filled)}] {ratio*100:5.1f}%"


def print_header(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{title}{RESET}")
    print("─" * 80)


def get_pods(v1: client.CoreV1Api) -> list:
    return v1.list_namespaced_pod(namespace="default").items


def render(v1: client.CoreV1Api) -> None:
    clear()
    node_metrics = get_node_metrics(v1)
    pods = get_pods(v1)

    # ── Node resource table ──────────────────────────────────────────────────
    print_header("  WORKER NODE RESOURCES")
    fmt = f"  {{:<35}} {{:>8}} {{:>8}} {{:>8}}   {{}}"
    print(fmt.format("NODE", "CPU free", "RAM free", "DISK free", "UTILISATION"))
    print("  " + "─" * 78)

    for node_name, m in sorted(node_metrics.items()):
        # Reconstruct allocatable totals (approximate from pods + free)
        cpu_str  = f"{m['cpu_free_m']}m"
        ram_str  = f"{m['ram_free_mib']:.0f}MiB"
        disk_str = f"{m['disk_free_gib']:.1f}GiB"

        # Count pods on this node
        n_pods = sum(1 for p in pods if p.spec.node_name == node_name)
        cpu_used = sum(
            _parse_cpu_to_millicores((c.resources.requests or {}).get("cpu", "0"))
            for p in pods if p.spec.node_name == node_name
            for c in p.spec.containers if c.resources
        )
        ram_used = sum(
            _parse_memory_to_mib((c.resources.requests or {}).get("memory", "0Ki"))
            for p in pods if p.spec.node_name == node_name
            for c in p.spec.containers if c.resources
        )
        cpu_total = m["cpu_free_m"] + cpu_used
        ram_total = m["ram_free_mib"] + ram_used
        cpu_bar = bar(cpu_used, cpu_total)

        print(f"  {node_name:<35} {cpu_str:>8} {ram_str:>8} {disk_str:>8}   {cpu_bar}  pods={n_pods}")

    # ── Pod assignment table ─────────────────────────────────────────────────
    print_header("  POD ASSIGNMENTS  (custom-scheduler)")
    fmt2 = "  {:<30} {:<35} {:<10} {:<8} {:<8}"
    print(fmt2.format("POD", "NODE", "STATUS", "CPU req", "RAM req"))
    print("  " + "─" * 78)

    custom_pods = [p for p in pods if p.spec.scheduler_name == SCHEDULER_NAME]
    custom_pods.sort(key=lambda p: p.metadata.name)

    for pod in custom_pods:
        name   = pod.metadata.name
        node   = pod.spec.node_name or f"{YELLOW}(pending){RESET}"
        phase  = pod.status.phase or "Unknown"
        colour = GREEN if phase == "Running" else (YELLOW if phase == "Pending" else RED)
        phase_str = f"{colour}{phase}{RESET}"

        cpu_req = sum(
            _parse_cpu_to_millicores((c.resources.requests or {}).get("cpu", "0"))
            for c in pod.spec.containers if c.resources
        )
        ram_req = sum(
            _parse_memory_to_mib((c.resources.requests or {}).get("memory", "0Ki"))
            for c in pod.spec.containers if c.resources
        )

        print(fmt2.format(name, node, phase_str, f"{cpu_req}m", f"{ram_req:.0f}Mi"))

    # ── Default-scheduler pods ───────────────────────────────────────────────
    print_header("  POD ASSIGNMENTS  (default scheduler — comparison)")
    default_pods = [p for p in pods if p.spec.scheduler_name != SCHEDULER_NAME
                    and p.metadata.labels and p.metadata.labels.get("app") == "demo-default"]
    default_pods.sort(key=lambda p: p.metadata.name)

    for pod in default_pods:
        name   = pod.metadata.name
        node   = pod.spec.node_name or f"{YELLOW}(pending){RESET}"
        phase  = pod.status.phase or "Unknown"
        colour = GREEN if phase == "Running" else (YELLOW if phase == "Pending" else RED)
        phase_str = f"{colour}{phase}{RESET}"
        cpu_req = sum(
            _parse_cpu_to_millicores((c.resources.requests or {}).get("cpu", "0"))
            for c in pod.spec.containers if c.resources
        )
        ram_req = sum(
            _parse_memory_to_mib((c.resources.requests or {}).get("memory", "0Ki"))
            for c in pod.spec.containers if c.resources
        )
        print(fmt2.format(name, node, phase_str, f"{cpu_req}m", f"{ram_req:.0f}Mi"))

    print(f"\n  {BOLD}Refreshing every {REFRESH_SECONDS}s — Ctrl+C to quit{RESET}")


def main() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

    v1 = client.CoreV1Api()

    print("Starting monitor...")
    try:
        while True:
            render(v1)
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
