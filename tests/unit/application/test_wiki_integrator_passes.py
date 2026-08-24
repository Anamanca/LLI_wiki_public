"""Unit tests for wiki integrator LLM passes (P1: flags + grounding + telemetry)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from llm_wiki.application.use_cases.ingestion.wiki_integrator import (
    _call_llm_json,
    _pass_extract,
    _pass_write,
)

VALID_WRITE_JSON = {
    "page_title": "Test Page",
    "page_slug": "test-page",
    "content_markdown": "# Test\n",
    "summary": "Tóm tắt.",
    "sections": [],
    "page_links": [],
}

VALID_EXTRACT_JSON = {
    "classification": {
        "main_topic": "Test",
        "domain": "finance",
        "subtopics": [],
        "key_entities": [],
        "language": "vi",
        "summary_3sentences": "abc",
    },
    "entities": {},
    "numbers": [],
    "events": [],
    "relationships": [],
    "key_claims": [],
    "market_context": "",
    "chunk_summary": "",
    "entity_relations": [],
}


class FakeLLM:
    """Records chat_completion_raw kwargs and returns canned JSON."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def chat_completion_raw(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        enable_thinking: bool = True,
    ) -> dict:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "enable_thinking": enable_thinking,
            }
        )
        return {
            "choices": [{"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }


@pytest.fixture
def classification() -> dict:
    return {
        "main_topic": "Thị trường tài chính",
        "domain": "finance",
        "subtopics": ["Lãi suất"],
        "key_entities": ["VCB"],
        "language": "vi",
        "summary_3sentences": "Tóm tắt ba câu.",
    }


@pytest.mark.asyncio
async def test_pass_write_default_thinking_off(classification: dict) -> None:
    llm = FakeLLM(VALID_WRITE_JSON)
    with patch("llm_wiki.application.use_cases.ingestion.wiki_integrator.settings") as mock_settings:
        mock_settings.wiki_write_thinking_enabled = False
        await _pass_write(
            llm=llm,
            transcript_text="transcript ngắn",
            classification=classification,
            facts={"numbers": [], "entities": {}},
            existing_page_content=None,
            timeout=60.0,
            published_at=datetime(2026, 8, 20, tzinfo=UTC),
        )
    call = llm.calls[0]
    assert call["enable_thinking"] is False
    assert call["max_tokens"] == 65536


@pytest.mark.asyncio
async def test_pass_write_thinking_on_with_flag(classification: dict) -> None:
    llm = FakeLLM(VALID_WRITE_JSON)
    with patch("llm_wiki.application.use_cases.ingestion.wiki_integrator.settings") as mock_settings:
        mock_settings.wiki_write_thinking_enabled = True
        await _pass_write(
            llm=llm,
            transcript_text="transcript ngắn",
            classification=classification,
            facts={"numbers": [], "entities": {}},
            existing_page_content=None,
            timeout=60.0,
            published_at=datetime(2026, 8, 20, tzinfo=UTC),
        )
    call = llm.calls[0]
    assert call["enable_thinking"] is True
    assert call["max_tokens"] == 131072


@pytest.mark.asyncio
async def test_pass_extract_always_thinking_off(classification: dict) -> None:
    llm = FakeLLM(VALID_EXTRACT_JSON)
    with patch("llm_wiki.application.use_cases.ingestion.wiki_integrator.settings") as mock_settings:
        mock_settings.wiki_write_thinking_enabled = True  # must NOT affect extract
        await _pass_extract(
            llm=llm,
            transcript_text="transcript ngắn",
            classification=classification,
            timeout=60.0,
            published_at=datetime(2026, 8, 20, tzinfo=UTC),
            temperature=0.0,
        )
    call = llm.calls[0]
    assert call["enable_thinking"] is False
    assert call["max_tokens"] == 32768


@pytest.mark.asyncio
async def test_write_prompt_contains_grounding_rules(classification: dict) -> None:
    """WRITE_SYSTEM_PROMPT must carry the grounded-evidence constraint."""
    llm = FakeLLM(VALID_WRITE_JSON)
    with patch("llm_wiki.application.use_cases.ingestion.wiki_integrator.settings") as mock_settings:
        mock_settings.wiki_write_thinking_enabled = False
        await _pass_write(
            llm=llm,
            transcript_text="x",
            classification=classification,
            facts={},
            existing_page_content=None,
            timeout=60.0,
        )
    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "RANG BUOC BANG CHUNG" in system_prompt
    assert "Không đủ dữ liệu trong transcript" in system_prompt


class RetryFakeLLM:
    """Returns unparseable text first, then valid JSON — exercises the retry path."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def chat_completion_raw(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        enable_thinking: bool = True,
    ) -> dict:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "enable_thinking": enable_thinking,
            }
        )
        if len(self.calls) == 1:
            content = "khong co json o day"
        else:
            content = json.dumps(self.payload, ensure_ascii=False)
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }


@pytest.mark.asyncio
async def test_call_llm_json_retry_returns_parsed_dict(classification: dict) -> None:
    """Regression: retry path must return the parsed retry JSON, not None.

    Previously the retry response was parsed into ``content2`` but never returned,
    so the function implicitly returned None and every caller doing
    ``result.get(...)`` crashed with 'NoneType' object has no attribute 'get'.
    """
    llm = RetryFakeLLM(VALID_EXTRACT_JSON)
    with patch("llm_wiki.application.use_cases.ingestion.wiki_integrator.settings") as mock_settings:
        result = await _call_llm_json(
            llm=llm,
            system_prompt="system",
            user_content="user",
            timeout=30.0,
            pass_label="TestPass",
            max_tokens=1024,
        )
    assert len(llm.calls) == 2, "first call unparseable -> exactly one retry"
    assert isinstance(result, dict)
    assert result["classification"]["main_topic"] == "Test"
    retry_call = llm.calls[1]
    assert retry_call["temperature"] == 0.1
    assert retry_call["messages"][1]["content"] == "user"[:6000]
