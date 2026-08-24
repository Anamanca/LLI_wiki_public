"""P5: deterministic number normalization tests."""

from __future__ import annotations

from llm_wiki.application.use_cases.ingestion.number_normalizer import (
    normalize_facts,
    parse_vn_number,
)


def test_scale_vnd_explicit() -> None:
    p = parse_vn_number("850 tỷ đồng")
    assert p.value == 850e9
    assert p.currency == "VND"
    assert p.certainty == "certain"


def test_scale_without_currency_no_vnd_guess() -> None:
    p = parse_vn_number("850 tỷ")
    assert p.value == 850e9
    assert p.currency is None
    assert p.currency_inferred is True


def test_decimal_vn_comma() -> None:
    p = parse_vn_number("12,5%")
    assert p.value == 12.5
    assert p.unit == "%"


def test_decimal_dot() -> None:
    p = parse_vn_number("0.8%")
    assert p.value == 0.8
    assert p.unit == "%"


def test_thousands_ambiguous() -> None:
    """One separator + 3 trailing digits -> do NOT guess."""
    p = parse_vn_number("1,234")
    assert p.value is None
    assert p.certainty == "ambiguous"
    p2 = parse_vn_number("1.234")
    assert p2.value is None
    assert p2.certainty == "ambiguous"


def test_thousands_with_scale() -> None:
    p = parse_vn_number("1.234 tỷ")
    assert p.value == 1234e9
    assert p.certainty == "certain"


def test_range() -> None:
    p = parse_vn_number("2-5%")
    assert p.min == 2
    assert p.max == 5
    assert p.unit == "%"


def test_vague_range_percent() -> None:
    p = parse_vn_number("30 mấy phần trăm")
    assert p.min == 30
    assert p.max is not None and p.max >= 39
    assert p.unit == "%"
    assert p.certainty == "probable"


def test_vague_hundred_million() -> None:
    p = parse_vn_number("mấy trăm triệu")
    assert p.value is None
    assert p.certainty == "speculative"


def test_vague_under_one_million() -> None:
    p = parse_vn_number("dưới 1 triệu")
    assert p.value is None
    assert p.certainty == "speculative"


def test_vague_few_hundred_thousand_vnd() -> None:
    p = parse_vn_number("vài trăm nghìn đồng")
    assert p.value is None
    assert p.currency == "VND"
    assert p.certainty == "speculative"


def test_direction_from_context() -> None:
    assert parse_vn_number("tăng 0.8%").direction == "increase"
    assert parse_vn_number("giảm 12.5 điểm").direction == "decrease"
    assert parse_vn_number("12,5%").direction is None


def test_duration_left_alone() -> None:
    p = parse_vn_number("khoảng 3 năm")
    # duration: no money unit attached, value None is acceptable
    assert p.certainty == "speculative"
    assert p.currency is None


def test_no_candidate() -> None:
    p = parse_vn_number("")
    assert p.certainty == "speculative"
    assert p.value is None


def test_normalize_facts_adds_fields_without_overwrite() -> None:
    facts = {
        "numbers": [{"value": "850 tỷ đồng", "unit": "VND", "context": "bán ròng"}],
        "company_financials": [
            {"fact_id": "cf1", "raw_value": "1,234 tỷ", "unit": "VND", "period": "Q1/2025"}
        ],
        "supply_demand": [
            {"fact_id": "sd1", "raw_value": "2-3 lần", "unit": "lần"}
        ],
        "policy_events": [{"fact_id": "pe1", "name": "Nghị quyết 21"}],
        "market_snapshots": [
            {"fact_id": "ms1", "raw_value": "12.5 điểm"}
        ],
    }
    out = normalize_facts(facts)
    # raw value preserved
    assert out["numbers"][0]["value"] == "850 tỷ đồng"
    assert out["numbers"][0]["normalized_value"] == 850e9
    assert out["numbers"][0]["currency"] == "VND"
    assert out["numbers"][0]["direction"] == "decrease"  # "bán ròng"
    # comma + scale = VN decimal convention: 1,234 tỷ = 1.234 tỷ
    assert out["company_financials"][0]["normalized_value"] == 1.234e9
    assert out["company_financials"][0]["certainty"] == "certain"
    # supply_demand normalized too
    assert out["supply_demand"][0]["normalized_min"] == 2
    assert out["supply_demand"][0]["normalized_max"] == 3
    # policy_events untouched (non-numeric)
    assert "normalized_value" not in out["policy_events"][0]
    # market snapshot decimal parsed
    assert out["market_snapshots"][0]["normalized_value"] == 12.5
