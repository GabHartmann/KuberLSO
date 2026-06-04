"""
scoring.py — weighted multi-metric scoring to choose the best worker node.

Metrics used:
  1. CPU free (millicores)   — weight 0.40
  2. RAM free (MiB)          — weight 0.35
  3. Disk free (GiB)         — weight 0.25

Each metric is min-max normalised across the candidate nodes so they are
comparable regardless of unit.  The node with the highest combined score wins.
"""


WEIGHTS = {
    "cpu_free_m":    0.40,
    "ram_free_mib":  0.35,
    "disk_free_gib": 0.25,
}


def score_nodes(node_metrics: dict, pod_cpu_req_m: int, pod_ram_req_mib: float) -> list[tuple[str, float]]:
    """
    Given:
      node_metrics  — {node_name: {cpu_free_m, ram_free_mib, disk_free_gib}}
      pod_cpu_req_m — millicores the pod requests
      pod_ram_req_mib — MiB the pod requests

    Returns a list of (node_name, score) sorted best-first.
    Nodes that cannot satisfy the pod's CPU or RAM request are excluded.
    """
    # Filter out nodes that don't have enough CPU or RAM
    candidates = {
        name: m for name, m in node_metrics.items()
        if m["cpu_free_m"] >= pod_cpu_req_m and m["ram_free_mib"] >= pod_ram_req_mib
    }

    if not candidates:
        return []

    # Min-max normalise each metric across candidates
    scores = {name: 0.0 for name in candidates}

    for metric, weight in WEIGHTS.items():
        values = [m[metric] for m in candidates.values()]
        min_v = min(values)
        max_v = max(values)
        rng = max_v - min_v

        for name, m in candidates.items():
            if rng == 0:
                normalised = 1.0
            else:
                normalised = (m[metric] - min_v) / rng
            scores[name] += weight * normalised

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def pick_best_node(node_metrics: dict, pod_cpu_req_m: int, pod_ram_req_mib: float) -> str | None:
    """Returns the name of the best node, or None if no node fits."""
    ranked = score_nodes(node_metrics, pod_cpu_req_m, pod_ram_req_mib)
    if not ranked:
        return None
    return ranked[0][0]
