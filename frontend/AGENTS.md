# Agent Notes — Frontend

## Docker Build

```bash
cd /home/hieunt/32_LLM_wiki_clean_arch
docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest -f frontend/Dockerfile frontend/
```

### Why `--network=host`?
The default Docker bridge network on this machine cannot resolve `registry.npmjs.org` reliably (`EAI_AGAIN`). Always add `--network=host`; otherwise `npm ci` will appear to hang or fail with DNS errors.

### Dockerfile structure
- `deps`: `npm ci`
- `builder`: `npm run build` (requires `output: 'standalone'` in `next.config.js`)
- `runner`: copies `.next/standalone` + `public`, runs as non-root `nextjs` user

### Why no `curl` in the image?
The frontend does not need `curl` to talk to the backend. Next.js uses Node.js built-in HTTP clients (`http`/`https` modules / `node-fetch`) for API calls. `curl` is only useful for manual debugging, so we keep the image minimal and use a `busybox` debug pod or Node.js one-liners when needed.

### Local sanity check
If Docker fails but local build works, the problem is Docker networking, not the code:
```bash
cd frontend
npm ci
npm run build
```

## Next.js Rewrites / API Proxy

`next.config.js` rewrites `/api/:path*` to `http://backend-v2.llm-wiki.svc.cluster.local:8000/api/:path*`. This only works inside the cluster where that DNS name resolves.

For local testing through `kubectl port-forward`, use IPv4 to avoid intermittent 500 errors:
```bash
kubectl -n llm-wiki port-forward --address 127.0.0.1 svc/frontend 3001:3000
curl http://127.0.0.1:3001/api/health
```

## Container Debugging

The frontend image is based on `node:22-slim` and does **not** include `curl` or `nslookup`. Use Node.js built-in modules or a `busybox` debug pod for network tests:

```bash
# Test connectivity using Node.js
kubectl -n llm-wiki exec deployment/frontend -- node -e \
  "http.get('http://backend-v2.llm-wiki.svc.cluster.local:8000/api/health', r => { let d=''; r.on('data', c => d+=c); r.on('end', () => console.log(r.statusCode, d)); })"

# Or use a busybox debug pod
kubectl -n llm-wiki run debug --rm -i --restart=Never --image=busybox:1.36 -- \
  wget -qO- http://backend-v2.llm-wiki.svc.cluster.local:8000/api/health
```
