import json


def _parse_sse(raw: str):
    """Reproduce frontend SSE parsing logic in Python for unit tests."""
    events = []
    for line in raw.split("\n"):
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        try:
            events.append(json.loads(data))
        except json.JSONDecodeError:
            continue
    return events


def test_sse_status_sequence_and_complete():
    raw = "\n".join([
        'data: {"type": "status", "status": "processing"}',
        'data: {"type": "status", "status": "retrieving"}',
        'data: {"type": "status", "status": "thinking"}',
        'data: {"type": "status", "status": "summarizing"}',
        'data: {"type": "complete", "answer": "answer text", "citations": [{"page_title": "T", "page_slug": "t"}], "sources_used": []}',
        'data: [DONE]',
    ])
    events = _parse_sse(raw)
    statuses = [e["status"] for e in events if e["type"] == "status"]
    complete = [e for e in events if e["type"] == "complete"]

    assert statuses == ["processing", "retrieving", "thinking", "summarizing"]
    assert len(complete) == 1
    assert complete[0]["answer"] == "answer text"
    assert complete[0]["citations"][0]["page_slug"] == "t"


def test_sse_ignores_malformed_lines():
    raw = "\n".join([
        'data: {"type": "status", "status": "processing"}',
        'data: not-json',
        'data: {"type": "complete", "answer": "ok", "citations": [], "sources_used": []}',
        'data: [DONE]',
    ])
    events = _parse_sse(raw)
    assert len(events) == 2
    assert events[1]["answer"] == "ok"
