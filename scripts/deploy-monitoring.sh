#!/bin/bash
# scripts/deploy-monitoring.sh
# One-command deploy of the entire LLM Wiki monitoring stack.
# Requires: kubectl, a running K3s/Kind cluster, and the llm-wiki namespace.
set -euo pipefail

NS="llm-wiki"
MON_DIR="$(dirname "$0")/../k8s/monitoring"

echo "=== Deploying LLM Wiki Monitoring Stack ==="

# ── Phase 2: Prometheus + Grafana infrastructure ──
echo ""
echo "[1/5] Prometheus RBAC + Config + Deployment..."
kubectl apply -f "${MON_DIR}/prometheus-rbac.yaml"
kubectl apply -f "${MON_DIR}/prometheus-config.yaml"
kubectl apply -f "${MON_DIR}/prometheus-deployment.yaml"

echo "[2/5] Grafana datasources + dashboards + deployment..."
kubectl apply -f "${MON_DIR}/grafana-datasources.yaml"
kubectl apply -f "${MON_DIR}/grafana-dashboards.yaml"
kubectl apply -f "${MON_DIR}/grafana-deployment.yaml"

# ── Phase 3: Loki + Promtail logging stack ──
if [ -f "${MON_DIR}/loki-statefulset.yaml" ]; then
  echo "[3/5] Loki + Promtail (configs inline in manifests)..."
  kubectl apply -f "${MON_DIR}/loki-statefulset.yaml"
  kubectl apply -f "${MON_DIR}/promtail-daemonset.yaml"
  if [ -f "${MON_DIR}/loki-alert-rules.yaml" ]; then
    kubectl apply -f "${MON_DIR}/loki-alert-rules.yaml"
  fi
else
  echo "[3/5] Skipped (Loki manifests not yet created)"
fi

# ── Phase 4: Alerting ──
if [ -f "${MON_DIR}/alertmanager-config.yaml" ]; then
  echo "[4/5] AlertManager + alert rules..."
  kubectl apply -f "${MON_DIR}/prometheus-alert-rules.yaml"
  kubectl apply -f "${MON_DIR}/alertmanager-config.yaml"
  kubectl apply -f "${MON_DIR}/alertmanager-deployment.yaml"
else
  echo "[4/5] Skipped (AlertManager manifests not yet created)"
fi

# ── Node exporter (infrastructure metrics) ──
if [ -f "${MON_DIR}/node-exporter-daemonset.yaml" ]; then
  echo "[4.5/5] Node exporter (infrastructure metrics)..."
  kubectl apply -f "${MON_DIR}/node-exporter-daemonset.yaml"
else
  echo "[4.5/5] Skipped (node-exporter manifest not present)"
fi

# ── Wait for pods ──
echo ""
echo "[5/5] Waiting for monitoring pods..."
kubectl -n "${NS}" wait --for=condition=ready pod -l app=prometheus --timeout=120s 2>/dev/null || true
kubectl -n "${NS}" wait --for=condition=ready pod -l app=grafana --timeout=120s 2>/dev/null || true

echo ""
echo "=== Done! Access monitoring ==="
echo "  Grafana:      kubectl -n ${NS} port-forward svc/grafana 3000:3000"
echo "  Prometheus:   kubectl -n ${NS} port-forward svc/prometheus 9090:9090"
echo "  AlertManager: kubectl -n ${NS} port-forward svc/alertmanager 9093:9093"
echo ""
echo "  Or use ./scripts/monitoring-socat-forward.sh for LAN access (Kind)"
echo "  Or use ./scripts/port-forward-monitoring.sh for LAN access (any cluster)"
