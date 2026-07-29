#!/bin/bash
# Forward host ports → Kind NodePorts via socat.
# Run as: ./scripts/monitoring-socat-forward.sh [&]
#
# Kind runs K8s nodes as Docker containers, so NodePort only opens inside
# the container. socat bridges host → container without sudo or port-forward.
# All bind on 0.0.0.0 → reachable from LAN, Tailscale, VPN, etc.
set -euo pipefail

KIND_CONTAINER="llm-wiki-control-plane"

# Resolve Kind node IP
KIND_IP=$(docker inspect "${KIND_CONTAINER}" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null)
if [ -z "${KIND_IP}" ]; then
  echo "ERROR: cannot find Kind container '${KIND_CONTAINER}'" >&2
  exit 1
fi

echo "Kind node: ${KIND_CONTAINER} → ${KIND_IP}"
echo "Starting socat forwarders..."

# Kill existing socat forwarders
pkill -f "socat TCP-LISTEN:3000" 2>/dev/null || true
pkill -f "socat TCP-LISTEN:3100" 2>/dev/null || true
pkill -f "socat TCP-LISTEN:3200" 2>/dev/null || true
pkill -f "socat TCP-LISTEN:8000" 2>/dev/null || true
pkill -f "socat TCP-LISTEN:9090" 2>/dev/null || true
pkill -f "socat TCP-LISTEN:9093" 2>/dev/null || true
sleep 1

# ── App Services ──
# Frontend: host 3000 → NodePort 30080
socat TCP-LISTEN:3000,bind=0.0.0.0,fork,reuseaddr TCP:${KIND_IP}:30080 &
echo "  Frontend       0.0.0.0:3000 → ${KIND_IP}:30080  PID=$!"

# Backend API: host 8000 → NodePort 30081
socat TCP-LISTEN:8000,bind=0.0.0.0,fork,reuseaddr TCP:${KIND_IP}:30081 &
echo "  Backend API    0.0.0.0:8000 → ${KIND_IP}:30081  PID=$!"

# ── Monitoring Services ──
# Grafana: host 3100 → NodePort 30000
socat TCP-LISTEN:3100,bind=0.0.0.0,fork,reuseaddr TCP:${KIND_IP}:30000 &
echo "  Grafana        0.0.0.0:3100 → ${KIND_IP}:30000  PID=$!"

# Prometheus: host 9090 → NodePort 30909
socat TCP-LISTEN:9090,bind=0.0.0.0,fork,reuseaddr TCP:${KIND_IP}:30909 &
echo "  Prometheus     0.0.0.0:9090 → ${KIND_IP}:30909  PID=$!"

# AlertManager: host 9093 → NodePort 30903
socat TCP-LISTEN:9093,bind=0.0.0.0,fork,reuseaddr TCP:${KIND_IP}:30903 &
echo "  AlertManager   0.0.0.0:9093 → ${KIND_IP}:30903  PID=$!"

# Loki: host 3200 → NodePort 30310
socat TCP-LISTEN:3200,bind=0.0.0.0,fork,reuseaddr TCP:${KIND_IP}:30310 &
echo "  Loki           0.0.0.0:3200 → ${KIND_IP}:30310  PID=$!"

sleep 1

# Detect IPs for display
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "")
LAN_IP=$(ip -4 addr show scope global | grep -oP 'inet \K192\.168\.\d+\.\d+' | grep -v 127.0.0.1 | head -1)

echo ""
echo "=== Access URLs ==="
echo ""
echo "  App:"
echo "    Frontend:      http://localhost:3000"
[ -n "${LAN_IP}" ] && echo "    Frontend:      http://${LAN_IP}:3000     (LAN)"
[ -n "${TAILSCALE_IP}" ] && echo "    Frontend:      http://${TAILSCALE_IP}:3000     (Tailscale)"
echo ""
echo "  Monitoring:"
echo "    Grafana:       http://localhost:3100"
[ -n "${TAILSCALE_IP}" ] && echo "    Grafana:       http://${TAILSCALE_IP}:3100     (Tailscale)"
echo "    Prometheus:    http://localhost:9090"
[ -n "${TAILSCALE_IP}" ] && echo "    Prometheus:    http://${TAILSCALE_IP}:9090     (Tailscale)"
echo "    AlertManager:  http://localhost:9093"
[ -n "${TAILSCALE_IP}" ] && echo "    AlertManager:  http://${TAILSCALE_IP}:9093     (Tailscale)"
echo "    Loki:          http://localhost:3200"
[ -n "${TAILSCALE_IP}" ] && echo "    Loki:          http://${TAILSCALE_IP}:3200     (Tailscale)"
echo ""
echo "  API:"
echo "    Backend API:   http://localhost:8000/api/metrics"
[ -n "${TAILSCALE_IP}" ] && echo "    Backend API:   http://${TAILSCALE_IP}:8000/api/metrics     (Tailscale)"
echo ""
echo "  Kill: pkill -f 'socat TCP-LISTEN'"
echo ""

wait