# Agent Guidelines — Frontend

## Stack
- Next.js 14 App Router, React 18, TypeScript 5.6.
- TanStack Query v5 for server state.
- Tailwind CSS v3 + custom `components/ui/*` primitives (shadcn/ui style).
- Graph: `react-force-graph-3d`, `@xyflow/react`, `@dagrejs/dagre`.
- Markdown: `react-markdown` + `rehype-highlight` + `rehype-sanitize` + `remark-gfm`.

## Directory Conventions
```
app/            # Routes + route handlers (e.g. api/query/stream/route.ts)
components/ui/  # Primitive components (card, button, badge, input, …)
components/<feature>/  # Feature components (chat, wiki, kg, sources, admin, dashboard)
hooks/          # TanStack Query wrappers. One hook per data concern.
lib/            # api-client.ts, query-keys.ts, kg-colors.ts, utils.ts
types/index.ts  # Source of truth for API response shapes.
```

## Data Fetching
- All backend calls go through `lib/api-client.ts`.
- All server-state reads go through `hooks/use-<feature>.ts` using TanStack Query.
- API base: `process.env.NEXT_PUBLIC_API_URL || "/api"`.
- In K8s, `/api/*` is rewritten to `backend-v2.llm-wiki.svc.cluster.local:8000/api/*`.
- For local dev against backend directly: `echo 'NEXT_PUBLIC_API_URL=http://localhost:8000/api' > .env.local`.

## Streaming Chat
- Browser calls `/api/query/stream` (Next.js route handler).
- `app/api/query/stream/route.ts` proxies SSE to backend service.
- `hooks/use-query-stream.ts` consumes SSE and updates state.
- Backend emits `type: "token"` and `type: "complete"` after translation in `routes/query.py`.

## Component Rules
- Use `components/ui/*` primitives. Build feature components on top of them.
- Keep pages thin; put complex UI in `components/<feature>/`.
- Use `clsx` + `tailwind-merge` via `lib/utils.ts` for conditional classes.
- Client components need `"use client"`.

## Type Safety
- Every API shape must be defined in `types/index.ts`.
- `api-client.ts` functions must return typed promises using those interfaces.
- Updating an endpoint → update type + api-client + consuming hooks/components.

## Common Commands
```bash
cd frontend
npm install
npm run dev        # localhost:3000
npm run build      # standalone output
```

## Gotchas
- `node_modules` is excluded from host mount in K8s dev mode via emptyDir/initContainer trick.
- `next.config.js` sets `output: 'standalone'` and `serverExternalPackages: ['three', 'react-force-graph-3d']`.
- Long LLM proxy timeout: `experimental.proxyTimeout: 300_000`.
