#!/usr/bin/env bash
# setup.sh — one-shot setup script for KuberLSO
# Run on Windows Git Bash / WSL2 / Linux / macOS.
#
# What it does:
#   1. Checks required tools (kind, kubectl, python3, pip)
#   2. Creates the Kind cluster (1 control-plane + 3 workers)
#   3. Labels worker nodes with a simulated disk-free-gib value
#   4. Installs Python dependencies
#   5. Applies RBAC for the custom scheduler
#   6. Prints next steps

set -euo pipefail

CLUSTER_NAME="kuberlso"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   KuberLSO — Custom Kubernetes Scheduler     ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── 1. Check prerequisites ───────────────────────────────────────────────────
check_cmd() {
  if ! command -v "$1" &>/dev/null; then
    echo "❌  '$1' not found. Please install it and re-run."
    echo "    kind:    https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
    echo "    kubectl: https://kubernetes.io/docs/tasks/tools/"
    exit 1
  fi
  echo "✅  $1 found"
}

check_cmd kind
check_cmd kubectl
check_cmd python3 || check_cmd python

echo ""

# ── 2. Create cluster (skip if already exists) ───────────────────────────────
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
  echo "ℹ️   Cluster '${CLUSTER_NAME}' already exists — skipping creation."
else
  echo "🔧  Creating Kind cluster '${CLUSTER_NAME}'..."
  kind create cluster --name "${CLUSTER_NAME}" --config cluster/kind-config.yaml
  echo "✅  Cluster created."
fi

# Point kubectl at the new cluster
kubectl cluster-info --context "kind-${CLUSTER_NAME}" >/dev/null

# ── 3. Label workers with simulated disk values ──────────────────────────────
echo ""
echo "🏷️   Labelling worker nodes with disk-free-gib..."

# Assign different disk sizes per worker to make the metric interesting
DISK_SIZES=(40 25 60)
IDX=0
for NODE in $(kubectl get nodes --no-headers -o custom-columns=NAME:.metadata.name | grep worker); do
  DISK=${DISK_SIZES[$IDX]:-20}
  kubectl label node "${NODE}" disk-free-gib="${DISK}" --overwrite
  echo "    ${NODE} → disk-free-gib=${DISK}"
  IDX=$(( IDX + 1 ))
done

# ── 4. Python dependencies ───────────────────────────────────────────────────
echo ""
echo "📦  Installing Python dependencies..."
pip install -r requirements.txt --quiet
echo "✅  Dependencies installed."

# ── 5. Apply RBAC ────────────────────────────────────────────────────────────
echo ""
echo "🔐  Applying RBAC for custom-scheduler..."
kubectl apply -f k8s/rbac.yaml
echo "✅  RBAC applied."

# ── 6. Done — print next steps ───────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Setup complete!  Follow these steps next:"
echo ""
echo "  TERMINAL 1 — run the custom scheduler:"
echo "    cd scheduler"
echo "    python scheduler.py"
echo ""
echo "  TERMINAL 2 — apply pods:"
echo "    kubectl apply -f pods/pods-custom.yaml"
echo "    kubectl apply -f pods/pods-default.yaml"
echo ""
echo "  TERMINAL 3 — live monitor:"
echo "    python monitor/monitor.py"
echo ""
echo "  TERMINAL 3 (after pods scheduled) — statistics:"
echo "    python stats/compare.py"
echo "═══════════════════════════════════════════════════════"
