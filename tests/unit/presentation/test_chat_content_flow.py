"""Simulation test for the chat GUI flow from question to displayed answer.

This test does not mount React; it verifies the invariants that the frontend
relies on: unique message IDs, SSE status sequence, and answer assignment.
"""

import json


def _parse_sse(raw: str):
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


def _simulate_handle_send(question: str, now: int):
    """Reproduce the message IDs created by chat-content.tsx handleSend."""
    user_msg_id = f"user-{now}"
    assistant_msg_id = f"assistant-{now + 1}"
    return user_msg_id, assistant_msg_id


def _simulate_chat_messages(messages, is_loading, status_label, pending_answer, pending_citations):
    """Reproduce the final state ChatMessages would render."""
    rendered = list(messages)
    if is_loading:
        rendered.append(
            {
                "id": "loading",
                "role": "assistant",
                "content": "",
                "status_label": status_label,
            }
        )
    else:
        # final update effect: assign answer to pending assistant message
        rendered = [
            {
                **m,
                "content": pending_answer
                if m["role"] == "assistant" and m["content"] == ""
                else m["content"],
                "citations": pending_citations if m["role"] == "assistant" else m.get("citations"),
            }
            for m in rendered
        ]
    return rendered


def test_chat_flow_question_to_answer():
    question = "What is RAG?"
    now = 1234567890123
    user_id, assistant_id = _simulate_handle_send(question, now)

    # IDs must be unique so React keys don't collide and the assistant message
    # is updated instead of the user message.
    assert user_id != assistant_id
    assert user_id.startswith("user-")
    assert assistant_id.startswith("assistant-")

    messages = [
        {"id": user_id, "role": "user", "content": question, "timestamp": "2026-07-21T00:00:00Z"},
        {
            "id": assistant_id,
            "role": "assistant",
            "content": "",
            "citations": [],
            "timestamp": "2026-07-21T00:00:00Z",
        },
    ]

    sse_raw = "\n".join(
        [
            'data: {"type": "status", "status": "processing"}',
            'data: {"type": "status", "status": "retrieving"}',
            'data: {"type": "status", "status": "thinking"}',
            'data: {"type": "status", "status": "summarizing"}',
            'data: {"type": "complete", "answer": "RAG stands for retrieval-augmented generation.", "citations": [{"page_title": "RAG", "page_slug": "rag"}], "sources_used": []}',
            "data: [DONE]",
        ]
    )
    events = _parse_sse(sse_raw)
    complete = [e for e in events if e["type"] == "complete"][0]

    final = _simulate_chat_messages(
        messages,
        is_loading=False,
        status_label="",
        pending_answer=complete["answer"],
        pending_citations=complete["citations"],
    )

    user_msg = [m for m in final if m["role"] == "user"][0]
    assistant_msg = [m for m in final if m["role"] == "assistant"][0]

    assert user_msg["content"] == question
    assert assistant_msg["content"] == "RAG stands for retrieval-augmented generation."
    assert assistant_msg["citations"][0]["page_slug"] == "rag"


def test_chat_flow_status_labels_during_loading():
    sse_raw = "\n".join(
        [
            'data: {"type": "status", "status": "processing"}',
            'data: {"type": "status", "status": "retrieving"}',
            'data: {"type": "status", "status": "thinking"}',
        ]
    )
    events = _parse_sse(sse_raw)
    labels = {
        "processing": "Đang phân tích câu hỏi...",
        "retrieving": "Đang tìm kiếm tài liệu...",
        "thinking": "Đang suy luận...",
        "summarizing": "Đang tổng hợp câu trả lờI...",
    }
    for e in events:
        assert e["status"] in labels
