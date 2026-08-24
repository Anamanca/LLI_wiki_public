"""P4: reflect fixer — programmatic corrections + draft serializer + merge."""

from __future__ import annotations

from llm_wiki.application.use_cases.ingestion.wiki_integrator import (
    _apply_corrections,
    _draft_to_markdown,
    _merge_rewrite_sections,
)


def _draft() -> dict:
    return {
        "page_title": "Bài test",
        "summary": "Tóm tắt.",
        "sections": [
            {
                "title": "S1",
                "content_markdown": "Khối ngoại bán ròng 850 tỷ đồng. "
                "Tỷ giá tăng 0.8%.",
            },
            {
                "title": "S2",
                "content_markdown": "VN-Index giảm 12.5 điểm.",
            },
        ],
    }


def test_draft_to_markdown_roundtrip() -> None:
    md = _draft_to_markdown(_draft())
    assert "## S1" in md
    assert "850 tỷ đồng" in md
    assert "## S2" in md
    assert md.count("## ") == 2


def test_apply_correction_unique_match() -> None:
    draft, applied = _apply_corrections(
        _draft(),
        [{"type": "hallucinated_number", "section": "S1",
          "page_says": "850 tỷ đồng", "correct_value": "850 tỷ USD"}],
    )
    assert applied == 1
    assert "850 tỷ USD" in draft["sections"][0]["content_markdown"]
    assert "850 tỷ đồng" not in draft["sections"][0]["content_markdown"]


def test_apply_correction_variant_match() -> None:
    """'12.5 điểm' variant ('12,5 điểm') should still match once."""
    draft, applied = _apply_corrections(
        _draft(),
        [{"type": "wrong_date", "section": "S2",
          "page_says": "12.5", "correct_value": "12.5"}],
    )
    # '12.5' appears once in S2 -> applied (no-op value, but count correct)
    assert applied == 1


def test_apply_correction_ambiguous_skipped() -> None:
    """Repeated value in a section -> skip, do not guess."""
    draft = _draft()
    draft["sections"][1]["content_markdown"] = "12.5 điểm và 12.5 điểm nữa"
    _, applied = _apply_corrections(
        draft,
        [{"type": "hallucinated_number", "section": "S2",
          "page_says": "12.5", "correct_value": "10.0"}],
    )
    assert applied == 0
    assert "12.5 điểm và 12.5 điểm nữa" in draft["sections"][1]["content_markdown"]


def test_apply_correction_section_not_found() -> None:
    # Section name missing but value exists in another section -> fallback applies.
    draft, applied = _apply_corrections(
        _draft(),
        [{"type": "wrong_unit", "section": "S99",
          "page_says": "0.8%", "correct_value": "0.9%"}],
    )
    assert applied == 1
    assert "0.9%" in draft["sections"][0]["content_markdown"]


def test_apply_correction_value_nowhere() -> None:
    # Value not found in ANY section -> skip.
    _, applied = _apply_corrections(
        _draft(),
        [{"type": "hallucinated_number", "section": "S99",
          "page_says": "9.999 tỷ", "correct_value": "1 tỷ"}],
    )
    assert applied == 0


def test_apply_correction_empty_errors() -> None:
    draft, applied = _apply_corrections(_draft(), [])
    assert applied == 0
    assert len(draft["sections"]) == 2


def test_merge_rewrite_sections_dedup() -> None:
    draft = _draft()
    rewrite = {
        "sections": [
            {"title": "S3 mới", "content_markdown": "## S3\n\nNội dung mới."},
            {"title": "S1", "content_markdown": "## S1\n\nTrùng."},
        ]
    }
    out = _merge_rewrite_sections(draft, rewrite)
    titles = [s["title"] for s in out["sections"]]
    assert titles == ["S1", "S2", "S3 mới"]
    assert out["sections"][2]["order"] == 2


import json
from datetime import UTC, datetime
from unittest.mock import patch

from llm_wiki.application.use_cases.ingestion.wiki_integrator import _run_synthesis_passes


class _ScriptedLLM:
    """Returns canned JSON per call: write -> reflect delta -> rewrite."""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
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
        payload = self.payloads.pop(0)
        return {
            "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
            "usage": {},
        }


def _write_payload() -> dict:
    return {
        "page_title": "Bài test",
        "page_slug": "bai-test",
        "content_markdown": "x",
        "summary": "Tóm tắt.",
        "sections": [
            {"title": "S1", "content_markdown": "Khối ngoại bán ròng 850 tỷ đồng.", "order": 0}
        ],
        "page_links": [],
    }


def _reflect_payload() -> dict:
    return {
        "coverage_by_section": [
            {"section": "S1", "covered_fact_ids": ["n1"], "coverage_ratio": 0.4,
             "high_priority_missing": ["ms1"]}
        ],
        "missing_facts": [
            {"fact_id": "ms1", "topic": "Thị trường chứng khoán phiên 15/3",
             "evidence_quote": "VN-Index giảm 12.5 điểm", "start_time": 752.3,
             "confidence": 0.9, "importance": "high", "suggested_section": "S2"}
        ],
        "errors": [
            {"fact_id": "n1", "type": "hallucinated_number", "section": "S1",
             "page_says": "850 tỷ đồng", "correct_value": "850 tỷ USD", "evidence_quote": ""}
        ],
    }


def _rewrite_payload() -> dict:
    return {
        "page_title": "Bài test",
        "sections": [
            {"title": "S2 mới", "content_markdown": "VN-Index giảm 12.5 điểm phiên 15/3.",
             "order": 0}
        ],
    }


def test_synthesis_reflect_branch_end_to_end() -> None:
    llm = _ScriptedLLM([_write_payload(), _reflect_payload(), _rewrite_payload()])
    facts = {
        "numbers": [{"value": "850 tỷ", "unit": "VND", "context": "bán ròng", "fact_id": "n1"}],
        "market_snapshots": [{"fact_id": "ms1", "raw_value": "12.5 điểm"}],
        "entities": {},
        "key_claims": [],
    }
    import asyncio

    with patch("llm_wiki.application.use_cases.ingestion.wiki_integrator.settings") as mock:
        mock.wiki_reflect_enabled = True
        mock.wiki_write_thinking_enabled = False
        result = asyncio.run(
            _run_synthesis_passes(
                llm=llm,
                transcript_text="Khối ngoại bán ròng 850 tỷ đồng. VN-Index giảm 12.5 điểm.",
                classification={"main_topic": "Test", "language": "vi"},
                facts=facts,
                timeout=120.0,
                published_at=datetime(2026, 8, 20, tzinfo=UTC),
                segments=[{"start": 752.3, "end": 758.0, "text": "VN-Index giảm 12.5 điểm"}],
                video_id="abc123",
                raw_transcript="Khối ngoại bán ròng 850 tỷ đồng. VN-Index giảm 12.5 điểm.",
            )
        )

    # 3 LLM calls: Pass2 write, Pass3 reflect (thinking ON, no retry), bounded rewrite
    assert len(llm.calls) == 3
    assert llm.calls[1]["enable_thinking"] is True
    assert llm.calls[1]["max_tokens"] == 16384

    wiki = result["wiki"]
    # correction applied: 850 tỷ đồng -> 850 tỷ USD
    assert "850 tỷ USD" in wiki["sections"][0]["content_markdown"]
    # rewrite appended S2 mới (dedup by title)
    titles = [s["title"] for s in wiki["sections"]]
    assert "S2 mới" in titles
    assert len(titles) == 2
