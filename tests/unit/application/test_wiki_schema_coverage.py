"""P3: finance schema coverage — verify_and_repair reads the finance arrays,
and the WRITE prompt carries the fact→section mapping rule."""

from __future__ import annotations

from llm_wiki.application.use_cases.ingestion.wiki_integrator import _verify_and_repair
from llm_wiki.application.use_cases.ingestion.wiki_prompts import (
    EXTRACT_SYSTEM_PROMPT,
    WRITE_SYSTEM_PROMPT,
)


class _NoopLLM:
    async def chat_completion_raw(self, *args, **kwargs):  # pragma: no cover - unused
        raise AssertionError("should not be called")


def test_extract_prompt_contains_finance_arrays() -> None:
    for arr in (
        "market_snapshots",
        "company_financials",
        "macro_series",
        "policy_events",
        "supply_demand",
        "valuations",
        "other_financial_facts",
        "overflow_facts",
        "fact_id",
    ):
        assert arr in EXTRACT_SYSTEM_PROMPT, f"missing {arr} in EXTRACT prompt"


def test_extract_prompt_has_caps_and_quote_limit() -> None:
    assert "source_quote ≤ 240 ký tự" in EXTRACT_SYSTEM_PROMPT
    assert "MỌI mảng ≤ 20 items" in EXTRACT_SYSTEM_PROMPT
    assert "speculative" in EXTRACT_SYSTEM_PROMPT
    assert "DOMAIN CHECKLIST" in EXTRACT_SYSTEM_PROMPT


def test_extract_prompt_has_fewshot() -> None:
    assert "FEW-SHOT" in EXTRACT_SYSTEM_PROMPT
    assert "850 tỷ đồng" in EXTRACT_SYSTEM_PROMPT


def test_write_prompt_has_mapping_rule() -> None:
    assert "ANH XA FACT -> SECTION" in WRITE_SYSTEM_PROMPT
    assert "coverage_missing" in WRITE_SYSTEM_PROMPT
    assert "KHÔNG tự tạo fact mới" in WRITE_SYSTEM_PROMPT


def test_verify_and_repair_reads_finance_arrays() -> None:
    """Numeric fidelity must scan finance-array raw_values (no crash, no repair call)."""
    facts = {
        "numbers": [{"value": "850 tỷ", "unit": "VND", "context": "bán ròng"}],
        "market_snapshots": [
            {"fact_id": "ms1", "raw_value": "12,5 điểm", "unit": "điểm"}
        ],
        "company_financials": [
            {"fact_id": "cf1", "raw_value": "1.234 tỷ", "unit": "VND", "period": "Q1/2025"}
        ],
        "policy_events": [{"fact_id": "pe1", "name": "Nghị quyết 21"}],  # non-numeric: skipped
        "key_claims": [],
    }
    data = {
        "page_title": "Bài test",
        "summary": "Tóm tắt.",
        "sections": [
            {
                "title": "S1",
                "content_markdown": "## S1\n\n850 tỷ và 12,5 điểm và 1.234 tỷ.",
            }
        ],
    }
    # Should not raise and must not call the LLM (all values present).
    import asyncio

    result = asyncio.run(
        _verify_and_repair(
            llm=_NoopLLM(),  # type: ignore[arg-type]
            transcript_text="transcript",
            classification={"language": "vi"},
            facts=facts,
            data=data,
            timeout=60.0,
        )
    )
    assert result == data
