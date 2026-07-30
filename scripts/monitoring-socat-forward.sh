#!/bin/bash
# Forward host ports → Kind NodePorts via socat.
# Run as: ./scripts/monitoring-socat-forward.sh [&]
#
# Kind runs K8s nodes as Docker containers, so NodePort only opens inside
# the container. socat bridges host → container without sudo or port-forward.
# All bind on 0.0.0.0 → reachable from LAN, Tailscale, VPN, etc.
set -euo pipefail

KIND_CONTAINER="llm-wiki-control-plane"

# Resolve Kind node IP (use docker inspect with Go template — works in both shell and systemd)
KIND_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${KIND_CONTAINER}" 2>/dev/null)
if [ -z "${KIND_IP}" ]; then
  echo "ERROR: cannot find Kind container '${KIND_CONTAINER}'" >&2
  exit 1
fi

echo "Kind node: ${KIND_CONTAINER} → ${KIND_IP}"
echo "Starting socat forwarders..."

# Kill existing socat forwarders on these ports
for port in 3000 3100 3200 8000 9090 9093 30000 30909 30310 30903; do
  pkill -f "socat TCP-LISTEN:${port}," 2>/dev/null || true
done
sleep 1

# ── Monitoring and App Services ──
# Match original NodePorts so Tailscale/LAN URLs stay consistent.
# host :port  → Kind node :NodePort

declare -A FORWARDS=(
  # Service           Host port  → NodePort
  [3000]="${KIND_IP}:30080"    # Frontend
  [8000]="${KIND_IP}:30081"    # Backend API
  [30000]="${KIND_IP}:30000"   # Grafana (NodePort itself, but reached via socat)
  [30909]="${KIND_IP}:30909"   # Prometheus
  [30903]="${KIND_IP}:30903"   # AlertManager
  [30310]="${KIND_IP}:30310"   # Loki
)

for host_port in 3000 8000 30000 30909 30903 30310; do
  target="${FORWARDS[$host_port]}"
  socat TCP-LISTEN:${host_port},bind=0.0.0.0,fork,reuseaddr TCP:${target} &
  echo "  0.0.0.0:${host_port} → ${target}  PID=$!"
done

sleep 1

# Detect IPs for display
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "")
LAN_IP=$(ip -4 addr show scope global | grep -oP 'inet \K192\.168\.\d+\.\d+' | grep -v 127.0.0.1 | head -1)

echo ""
echo "=== Access URLs (port = original K8s NodePort) ==="
echo ""
echo "  App:"
echo "    Frontend:      http://localhost:30080"
[ -n "${LAN_IP}" ] && echo "    Frontend:      http://${LAN_IP}:30080     (LAN)"
[ -n "${TAILSCALE_IP}" ] && echo "    Frontend:      http://${TAILSCALE_IP}:30080     (Tailscale)"
echo ""
echo "  Monitoring:"
echo "    Grafana:       http://localhost:30000"
[ -n "${TAILSCALE_IP}" ] && echo "    Grafana:       http://${TAILSCALE_IP}:30000     (Tailscale)"
echo "    Prometheus:    http://localhost:30909"
[ -n "${TAILSCALE_IP}" ] && echo "    Prometheus:    http://${TAILSCALE_IP}:30909     (Tailscale)"
echo "    AlertManager:  http://localhost:30903"
[ -n "${TAILSCALE_IP}" ] && echo "    AlertManager:  http://${TAILSCALE_IP}:30903     (Tailscale)"
echo "    Loki:          http://localhost:30310"
[ -n "${TAILSCALE_IP}" ] && echo "    Loki:          http://${TAILSCALE_IP}:30310     (Tailscale)"
echo ""
echo "  Kill: pkill -f 'socat TCP-LISTEN'"
echo ""

wait