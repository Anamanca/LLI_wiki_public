#!/usr/bin/env bash
# Healthcheck cho LLM Wiki (deployment cá nhân).
# Thay thế toàn bộ stack Prometheus/Grafana/Loki/AlertManager đã gỡ bỏ.
#
# Kiểm tra: cluster node, workload readiness, HTTP qua NodePort, worker heartbeat
# trong DB, tài nguyên host (disk/memory/CPU kind node).
#
# Usage:
#   ./scripts/healthcheck.sh                     # chạy 1 lần, in report
#   */10 * * * * /path/scripts/healthcheck.sh >> /tmp/llm-wiki-health.log 2>&1
#
# Exit code: 0 = mọi thứ OK, 1 = có ít nhất 1 FAIL.

set -uo pipefail

NAMESPACE="llm-wiki"
APP_URL="http://localhost:30080"   # frontend NodePort (kind map host:30080)
API_URL="http://localhost:30081"   # backend NodePort (kind map host:30081)
KIND_NODE="llm-wiki-control-plane"
FAILED=0

check() {
  local name="$1" result="$2"
  case "$result" in
    OK*) printf "[OK]   %s\n" "$name" ;;
    *)    printf "[FAIL] %s — %s\n" "$name" "$result"; FAILED=1 ;;
  esac
}

echo "=== LLM Wiki healthcheck $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

# 1. Cluster node
if command -v kubectl >/dev/null 2>&1; then
  node_ready=$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' 2>/dev/null)
  check "k8s node ready" "$(echo "$node_ready" | grep -q " True" && echo OK || echo "node not ready: $node_ready")"
else
  check "kubectl available" "kubectl not found"
fi

# 2. Workload readiness (deployments + statefulsets)
if command -v kubectl >/dev/null 2>&1; then
  workloads=$(kubectl -n "$NAMESPACE" get deploy,sts -o jsonpath='{range .items[*]}{.kind}{"/"}{.metadata.name}{" "}{.status.readyReplicas}/{.spec.replicas}{"\n"}{end}' 2>/dev/null)
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    name="${line%% *}"
    ready="${line##* }"
    if [ "${ready%/*}" = "${ready#*/}" ] && [ "${ready#*/}" != "0" ]; then
      check "$name ready" "OK"
    else
      check "$name ready" "expect ready=${ready#*/} got=${ready%/*}"
    fi
  done <<< "$workloads"
else
  check "workload readiness" "kubectl not found"
fi

# 3. HTTP qua NodePort (frontend + backend health)
if command -v curl >/dev/null 2>&1; then
  app_code=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 5 "$APP_URL/" 2>/dev/null || echo "000")
  check "frontend :30080" "$([ "$app_code" = "200" ] && echo OK || echo "HTTP $app_code")"
  api_code=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 5 "$API_URL/api/health" 2>/dev/null || echo "000")
  check "backend :30081/api/health" "$([ "$api_code" = "200" ] && echo OK || echo "HTTP $api_code")"
else
  check "HTTP endpoints" "curl not found"
fi

# 4. Worker heartbeat trong DB (có worker ingestion sống trong 120s?)
if command -v kubectl >/dev/null 2>&1; then
  hb=$(kubectl -n "$NAMESPACE" exec postgres-0 -- psql -U wiki -d llm_wiki -tAc \
    "SELECT count(*) FROM worker_heartbeats WHERE last_heartbeat > now() - interval '120 seconds'" 2>/dev/null | tr -d '[:space:]')
  if [ -n "$hb" ] && [ "$hb" -ge 1 ] 2>/dev/null; then
    check "worker heartbeats (120s)" "OK ($hb worker alive)"
  else
    check "worker heartbeats (120s)" "no live worker (count=$hb)"
  fi
else
  check "worker heartbeats" "kubectl not found"
fi

# 5. Tài nguyên host
disk_pct=$(df -h / | awk 'NR==2 {print $5}' | tr -d '%')
check "host disk / (used)" "$([ "${disk_pct:-100}" -lt 90 ] && echo "OK (${disk_pct}%)" || echo "high usage ${disk_pct}%")"

mem_avail=$(free -m | awk '/^Mem:/ {print int($7/1024)}')
check "host memory available" "$([ "${mem_avail:-0}" -gt 2 ] && echo "OK (${mem_avail}GB)" || echo "low memory ${mem_avail}GB")"

if command -v docker >/dev/null 2>&1; then
  kind_cpu=$(docker stats --no-stream --format '{{.CPUPerc}}' "$KIND_NODE" 2>/dev/null | tr -d ' %')
  kind_cpu_int=$(printf '%.0f' "${kind_cpu:-100}" 2>/dev/null)
  check "kind node CPU" "$([ "${kind_cpu_int:-100}" -lt 90 ] && echo "OK (${kind_cpu}%)" || echo "high CPU ${kind_cpu}%")"
else
  check "kind node CPU" "docker not found"
fi

echo "=== result: $([ "$FAILED" = 0 ] && echo ALL_OK || echo HAS_FAILURES) ==="
exit "$FAILED"
