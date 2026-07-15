export const maxDuration = 300;

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const body = await req.json();

  try {
    const backendRes = await fetch("http://backend-v2.llm-wiki.svc.cluster.local:8000/api/query/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      duplex: "half",
    } as RequestInit);

    if (!backendRes.body) {
      return new Response(JSON.stringify({ error: "No response body" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response(backendRes.body, {
      status: backendRes.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Stream proxy failed";
    return new Response(JSON.stringify({ error: message }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
