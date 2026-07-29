#!/bin/bash
# scripts/port-forward-monitoring.sh
# Start all monitoring port-forwards bound to ALL interfaces
# so they're reachable from LAN, Tailscale, etc.
#
# Usage:
#   ./scripts/port-forward-monitoring.sh           # foreground
#   ./scripts/port-forward-monitoring.sh &          # background
#   nohup ./scripts/port-forward-monitoring.sh &    # survives terminal close
set -euo pipefail

NS="llm-wiki"
PIDFILE="/tmp/monitoring-port-forwards.pid"

# Kill existing port-forwards for these ports
pkill -f "port-forward.*svc/grafana" 2>/dev/null || true
pkill -f "port-forward.*svc/prometheus" 2>/dev/null || true
pkill -f "port-forward.*svc/alertmanager" 2>/dev/null || true
sleep 1

echo "Starting monitoring port-forwards on 0.0.0.0..."
kubectl -n "${NS}" port-forward --address 0.0.0.0 svc/grafana 3000:3000 &
echo $! >> "${PIDFILE}"
kubectl -n "${NS}" port-forward --address 0.0.0.0 svc/prometheus 9090:9090 &
echo $! >> "${PIDFILE}"
kubectl -n "${NS}" port-forward --address 0.0.0.0 svc/alertmanager 9093:9093 &
echo $! >> "${PIDFILE}"

sleep 2

# Detect IPs
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "")
LAN_IP=$(ip -4 addr show scope global | grep -oP 'inet \K(192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)' | grep -v 127.0.0.1 | head -1)

echo ""
echo "=== Access URLs ==="
echo "  Grafana:       http://localhost:3000   (admin / admin)"
if [ -n "${LAN_IP}" ]; then
  echo "  Grafana:       http://${LAN_IP}:3000    (LAN)"
fi
if [ -n "${TAILSCALE_IP}" ]; then
  echo "  Grafana:       http://${TAILSCALE_IP}:3000    (Tailscale)"
fi
echo "  Prometheus:    http://localhost:9090"
if [ -n "${LAN_IP}" ]; then
  echo "  Prometheus:    http://${LAN_IP}:9090     (LAN)"
fi
if [ -n "${TAILSCALE_IP}" ]; then
  echo "  Prometheus:    http://${TAILSCALE_IP}:9090     (Tailscale)"
fi
echo "  AlertManager:  http://localhost:9093"
if [ -n "${TAILSCALE_IP}" ]; then
  echo "  AlertManager:  http://${TAILSCALE_IP}:9093     (Tailscale)"
fi
echo ""
echo "  Kill: pkill -f 'port-forward.*svc/(grafana|prometheus|alertmanager)'"
echo "  PIDs saved: ${PIDFILE}"
echo ""
echo "Ctrl+C to stop / use 'kill \$(cat ${PIDFILE})'"

# Wait for any to exit (keep foreground)
wait
