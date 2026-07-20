"""Mini HTTP health server for worker/consumer containers.
Runs on a dedicated port, returns JSON status for Docker healthcheck.
Uses asyncio.start_server (zero external dependency)."""

import asyncio
import json
import os
import time

SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8101"))

_start_time = time.time()

_state: dict = {
    "status": "starting",
    "current_stage": None,
}


def set_health_state(status: str, stage: str | None = None) -> None:
    _state["status"] = status
    if stage is not None:
        _state["current_stage"] = stage


async def handle_health(reader, writer) -> None:
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
    writer.close()
    await writer.wait_closed()


async def start_health_server() -> None:
    server = await asyncio.start_server(handle_health, "127.0.0.1", HEALTH_PORT)
    async with server:
        await server.serve_forever()
