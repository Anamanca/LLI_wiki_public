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

echo "=== Loading images into k3s ==="
docker save backend:latest | sudo k3s ctr images import -
docker save worker:latest | sudo k3s ctr images import -
docker save 32_llm_wiki_clean_arch-frontend:latest | sudo k3s ctr images import -

echo "=== Deploying backend ==="
kubectl apply -f "${PROJECT_DIR}/k8s/backend/"

echo "=== Deploying cpu-worker ==="
kubectl apply -f "${PROJECT_DIR}/k8s/cpu-worker/"

echo "=== Deploying wiki-consumer ==="
kubectl apply -f "${PROJECT_DIR}/k8s/wiki-consumer/"

echo "=== Deploying frontend ==="
kubectl apply -f "${PROJECT_DIR}/k8s/frontend/"

echo "=== Deploying ingress ==="
kubectl apply -f "${PROJECT_DIR}/k8s/ingress.yaml"

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
