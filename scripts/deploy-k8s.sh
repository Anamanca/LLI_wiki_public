#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
NAMESPACE="llm-wiki"

echo "=== Building backend (API) Docker image ==="
docker build --network=host --target api -t backend:latest "$PROJECT_DIR"

echo "=== Building worker (cpu-worker + wiki-consumer) Docker image ==="
docker build --network=host --target worker -t worker:latest "$PROJECT_DIR"

echo "=== Building frontend Docker image ==="
docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest "$PROJECT_DIR/frontend"

echo "=== Loading images into cluster ==="
if command -v kind >/dev/null 2>&1 && kind get clusters >/dev/null 2>&1 && [ -n "$(kind get clusters)" ]; then
  CLUSTER_NAME="$(kubectl config current-context 2>/dev/null | sed 's/^kind-//')"
  [ -n "$CLUSTER_NAME" ] || CLUSTER_NAME="$(kind get clusters | head -1)"
  echo "Detected kind cluster: ${CLUSTER_NAME} — using kind load"
  kind load docker-image backend:latest worker:latest 32_llm_wiki_clean_arch-frontend:latest --name "${CLUSTER_NAME}"
else
  echo "No kind cluster detected — falling back to k3s ctr import"
  docker save backend:latest | sudo k3s ctr images import -
  docker save worker:latest | sudo k3s ctr images import -
  docker save 32_llm_wiki_clean_arch-frontend:latest | sudo k3s ctr images import -
fi

echo "=== Deploying backend ==="
kubectl apply -f "${PROJECT_DIR}/k8s/backend/"

echo "=== Deploying cpu-worker ==="
kubectl apply -f "${PROJECT_DIR}/k8s/cpu-worker/"

echo "=== Deploying wiki-consumer ==="
kubectl apply -f "${PROJECT_DIR}/k8s/wiki-consumer/"

echo "=== Deploying frontend ==="
kubectl apply -f "${PROJECT_DIR}/k8s/frontend/"

if [ -f "${PROJECT_DIR}/k8s/ingress.yaml" ]; then
  echo "=== Deploying ingress ==="
  kubectl apply -f "${PROJECT_DIR}/k8s/ingress.yaml"
else
  echo "=== Skipping ingress (k8s/ingress.yaml not present) ==="
fi

echo "=== Restarting deployments ==="
kubectl rollout restart deployment/backend-v2 -n "$NAMESPACE"
kubectl rollout restart deployment/cpu-worker -n "$NAMESPACE"
kubectl rollout restart deployment/wiki-consumer -n "$NAMESPACE"
kubectl rollout restart deployment/frontend -n "$NAMESPACE"

echo ""
echo "=== Waiting for pods to be ready ==="
kubectl wait --for=condition=ready pod -l app=backend-v2 -n "$NAMESPACE" --timeout=120s 2>/dev/null || true
kubectl wait --for=condition=ready pod -l app=cpu-worker -n "$NAMESPACE" --timeout=120s 2>/dev/null || true
kubectl wait --for=condition=ready pod -l app=wiki-consumer -n "$NAMESPACE" --timeout=120s 2>/dev/null || true
kubectl wait --for=condition=ready pod -l app=frontend -n "$NAMESPACE" --timeout=120s 2>/dev/null || true

echo ""
echo "=== Done! Checking pods ==="
kubectl get pods -n "$NAMESPACE" | grep -E "backend-v2|cpu-worker|wiki-consumer|frontend"
