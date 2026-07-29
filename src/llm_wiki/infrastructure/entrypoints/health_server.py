"""Mini HTTP health server for worker/consumer containers.
Runs on a dedicated port, returns JSON status for Docker healthcheck.
Uses asyncio.start_server (zero external dependency).

When ``ENABLE_METRICS=true`` the server also serves Prometheus metrics at
GET /metrics and binds to 0.0.0.0 so Prometheus can scrape it from outside
the pod.
"""

import asyncio
import json
import os
import time

SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8101"))
_ENABLE_METRICS = os.getenv("ENABLE_METRICS", "false").lower() in ("1", "true", "yes")

_start_time = time.time()

_state: dict = {
    "status": "starting",
    "current_stage": None,
}


def set_health_state(status: str, stage: str | None = None) -> None:
    _state["status"] = status
    if stage is not None:
        _state["current_stage"] = stage


async def _handle_health(writer) -> None:
    body = json.dumps({
        "service": SERVICE_NAME,
        **_state,
        "uptime_seconds": int(time.time() - _start_time),
    })
    response = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
        f"{body}"
    )
    writer.write(response.encode())
    await writer.drain()


async def _handle_metrics(writer) -> None:
    try:
        from llm_wiki.infrastructure.telemetry.metrics_collector import get_metrics
        data = get_metrics().get_metrics_response()
    except Exception:
        data = b""
    response = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/plain; version=0.0.4\r\n"
        f"Content-Length: {len(data)}\r\n"
        "\r\n"
    )
    writer.write(response.encode())
    writer.write(data)
    await writer.drain()


async def handle_health(reader, writer) -> None:
    try:
        request_line = (await reader.read(4096)).decode(errors="replace")
    except Exception:
        request_line = ""

    if "GET /metrics" in request_line and _ENABLE_METRICS:
        await _handle_metrics(writer)
    elif "GET /health" in request_line or "GET / " in request_line or not request_line.strip():
        await _handle_health(writer)
    else:
        await _handle_health(writer)

    writer.close()
    await writer.wait_closed()


async def start_health_server() -> None:
    bind_addr = "0.0.0.0" if _ENABLE_METRICS else "127.0.0.1"
    server = await asyncio.start_server(handle_health, bind_addr, HEALTH_PORT)
    async with server:
        await server.serve_forever()
