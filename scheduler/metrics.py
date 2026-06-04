"""
metrics.py — collects CPU, RAM, and Disk free from each worker node.

CPU/RAM: read from Kubernetes node allocatable vs. requested (no metrics-server needed).
Disk:    read from a node label we inject via setup.sh (simulated for kind).
"""

from kubernetes import client


def get_node_metrics(v1: client.CoreV1Api) -> dict:
    """
    Returns a dict keyed by node name with free resources:
      {
        "worker-node-1": {
          "cpu_free_m":   <millicores free>,
          "ram_free_mib": <MiB free>,
          "disk_free_gib":<GiB free>,
        },
        ...
      }
    """
    nodes = v1.list_node().items
    pods = v1.list_pod_for_all_namespaces().items

    node_data = {}
    for node in nodes:
        name = node.metadata.name
        roles = node.metadata.labels or {}

        # Skip the control-plane node
        if "node-role.kubernetes.io/control-plane" in roles:
            continue

        allocatable = node.status.allocatable or {}

        # Parse CPU allocatable (e.g. "4" or "4000m")
        cpu_alloc_str = allocatable.get("cpu", "0")
        cpu_alloc_m = _parse_cpu_to_millicores(cpu_alloc_str)

        # Parse RAM allocatable (e.g. "8Gi", "512Mi")
        ram_alloc_str = allocatable.get("memory", "0Ki")
        ram_alloc_mib = _parse_memory_to_mib(ram_alloc_str)

        # Disk: read from node label (set by setup.sh); default 20 GiB
        disk_label = (node.metadata.labels or {}).get("disk-free-gib", "20")
        try:
            disk_free_gib = float(disk_label)
        except ValueError:
            disk_free_gib = 20.0

        node_data[name] = {
            "cpu_alloc_m": cpu_alloc_m,
            "ram_alloc_mib": ram_alloc_mib,
            "disk_free_gib": disk_free_gib,
            "cpu_req_m": 0,
            "ram_req_mib": 0,
        }

    # Accumulate requested resources from running pods
    for pod in pods:
        node_name = pod.spec.node_name
        if node_name not in node_data:
            continue
        if pod.status.phase not in ("Running", "Pending"):
            continue
        for container in pod.spec.containers:
            req = (container.resources.requests or {}) if container.resources else {}
            node_data[node_name]["cpu_req_m"] += _parse_cpu_to_millicores(req.get("cpu", "0"))
            node_data[node_name]["ram_req_mib"] += _parse_memory_to_mib(req.get("memory", "0Ki"))

    # Calculate free resources
    result = {}
    for name, d in node_data.items():
        result[name] = {
            "cpu_free_m": max(0, d["cpu_alloc_m"] - d["cpu_req_m"]),
            "ram_free_mib": max(0, d["ram_alloc_mib"] - d["ram_req_mib"]),
            "disk_free_gib": d["disk_free_gib"],
        }

    return result


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_cpu_to_millicores(value: str) -> int:
    """Convert Kubernetes CPU string to millicores (int)."""
    value = str(value).strip()
    if value.endswith("m"):
        return int(float(value[:-1]))
    try:
        return int(float(value) * 1000)
    except ValueError:
        return 0


def _parse_memory_to_mib(value: str) -> float:
    """Convert Kubernetes memory string to MiB (float)."""
    value = str(value).strip()
    multipliers = {
        "Ki": 1 / 1024,
        "Mi": 1.0,
        "Gi": 1024.0,
        "Ti": 1024.0 * 1024,
        "K":  1 / 1024,
        "M":  1.0,
        "G":  1024.0,
    }
    for suffix, factor in multipliers.items():
        if value.endswith(suffix):
            try:
                return float(value[: -len(suffix)]) * factor
            except ValueError:
                return 0.0
    try:
        return float(value) / (1024 * 1024)
    except ValueError:
        return 0.0
