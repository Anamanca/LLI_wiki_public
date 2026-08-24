"""Deterministic normalization of Vietnamese financial numbers.

Pure functions (no I/O). The LLM often leaves raw speech like "mấy trăm triệu"
or "30 mấy phần trăm" in extracted facts; this layer converts what is safely
convertible and marks the rest as ambiguous/speculative WITHOUT inventing a
precise value. Raw values are never overwritten — normalized fields are added.

Safety rules:
- "1,234" / "1.234" (one separator + exactly 3 trailing digits) is AMBIGUOUS:
  VN ASR punctuation cannot distinguish thousands from decimals. Never guess —
  value=None, certainty="ambiguous", keep the raw string.
- VND is only assigned when the quote explicitly says VND/đồng/đồng Việt Nam.
  A bare "tỷ" yields currency=null.
- Duration phrases ("khoảng 3 năm") are left untouched (no money unit).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Scales: nghìn (1e3), triệu (1e6), trăm triệu (1e8), tỷ (1e9)
_SCALE_RE = re.compile(
    r"(?P<num>\d[\d.,\s]*)\s*(?P<scale>trăm\s+triệu|tỷ|triệu|nghìn|ngàn)", re.IGNORECASE
)
_CURRENCY_VI = r"(?:vnd|đồng\s+việt\s+nam|đồng)"
_DECIMAL_RE = re.compile(r"^(\d{1,3}(?:[.,]\d{3})*|[.,]\d+|[0-9]+(?:[.,]\d+)?)$")
_VAGUE_PREFIX_RE = re.compile(
    r"^(mấy|vài|khoảng|gần|hơn|dưới|trên|khoảng\s+gần|chưa\s+đến)\s+", re.IGNORECASE
)
_VAGUE_RANGE_RE = re.compile(
    r"^(\d{1,2})\s+(mấy|vài)\s+(?:phần\s+trăm|%)$", re.IGNORECASE
)
_DIRECTION_KEYWORDS = {
    "tăng": "increase", "tăng trưởng": "increase", "gia tăng": "increase",
    "giảm": "decrease", "giảm mạnh": "decrease", "sụt giảm": "decrease",
    "up": "increase", "down": "decrease", "rise": "increase", "fall": "decrease",
    "tăng cao": "increase", "nhích": "increase", "tăng vọt": "increase",
    "bán ròng": "decrease", "mua ròng": "increase",
}


@dataclass
class ParsedNumber:
    raw: str
    value: float | None = None
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    currency: str | None = None
    currency_inferred: bool = False
    certainty: str = "certain"  # certain|probable|speculative|ambiguous
    direction: str | None = None


def _strip_units(text: str) -> str:
    """Normalize punctuation spacing for parsing."""
    return re.sub(r"\s+", " ", text).strip()


def _parse_decimal(token: str, with_scale: bool = False) -> tuple[float | None, str]:
    """Parse a plain decimal token; returns (value, certainty).

    One separator + exactly 3 trailing digits without a scale is AMBIGUOUS
    (VN ASR cannot tell thousands from decimals). With a scale (tỷ/triệu...),
    VN print convention applies: dot = thousands separator, comma = decimal.
    """
    t = token.replace(" ", "")
    if not with_scale:
        # Bare "1,234" / "1.234": one separator + exactly 3 trailing digits.
        for sep in (".", ","):
            if sep in t and t.count(sep) == 1:
                before, after = t.split(sep)
                if len(after) == 3 and len(before) <= 3:
                    return None, "ambiguous"
    if with_scale and "." in t and "," not in t and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):
        # VN thousands separator: "1.234" -> 1234
        try:
            return float(t.replace(".", "")), "certain"
        except ValueError:
            return None, "speculative"
    try:
        if "," in t and "." not in t:
            return float(t.replace(",", ".")), "certain"
        return float(t), "certain"
    except ValueError:
        return None, "speculative"


def parse_vn_number(text: str) -> ParsedNumber:
    """Parse a Vietnamese financial number phrase into a ParsedNumber.

    Always returns a ParsedNumber — never raises, never returns None. For
    phrases with no numeric candidate, value/certainty stay conservative.
    """
    raw = _strip_units(text)
    if not raw:
        return ParsedNumber(raw=text, certainty="speculative")

    direction = _detect_direction(raw)

    # Range: "2-5%", "2-3 lần"
    m = re.match(
        r"^(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*(%|phần trăm|lần|điểm|tỷ|triệu)?$",
        raw,
    )
    if m:
        lo, _ = _parse_decimal(m.group(1))
        hi, _ = _parse_decimal(m.group(2))
        unit = m.group(3) if m.group(3) else None
        if lo is not None and hi is not None:
            return ParsedNumber(
                raw=raw, min=lo, max=hi, unit=unit, certainty="certain",
                direction=direction,
            )

    # Vague range: "30 mấy phần trăm"
    m = _VAGUE_RANGE_RE.match(raw)
    if m:
        base, _ = _parse_decimal(m.group(1))
        if base is not None:
            return ParsedNumber(
                                raw=raw,
                min=base,
                max=(base * 1.5 + 9) if base >= 20 else (base + 9),
                unit="%", certainty="probable", direction=direction,
            )

    # Vague phrases FIRST: "mấy trăm triệu", "dưới 1 triệu", "vài trăm nghìn đồng"
    m = _VAGUE_PREFIX_RE.match(raw)
    if m:
        return ParsedNumber(
            raw=raw, certainty="speculative", direction=direction,
            currency=_detect_currency(raw),
        )

    # Scale + currency: "850 tỷ đồng", "1.234 tỷ", "12,5 triệu USD"
    m = _SCALE_RE.search(raw)
    if m:
        num_token = m.group("num").strip()
        value, dec_certainty = _parse_decimal(num_token, with_scale=True)
        if value is None:
            # Ambiguous thousands vs decimal — do not guess.
            return ParsedNumber(
                raw=raw, unit=_scale_unit(m.group("scale")), certainty=dec_certainty,
                direction=direction,
            )
        scaled = value * _scale_factor(m.group("scale"))
        currency = _detect_currency(raw)
        return ParsedNumber(
            raw=raw, value=scaled, unit=_scale_unit(m.group("scale")),
            currency=currency, currency_inferred=currency is None,
            certainty="certain", direction=direction,
        )

    # Plain decimal with optional %/điểm: "12,5%", "0.8%"
    m = re.match(r"^(\d+(?:[.,]\d+)?)\s*(%|phần trăm|điểm)?$", raw)
    if m:
        value, dec_certainty = _parse_decimal(m.group(1))
        unit = (
            "%" if m.group(2) in ("%", "phần trăm")
            else ("điểm" if m.group(2) == "điểm" else None)
        )
        return ParsedNumber(
            raw=raw, value=value, unit=unit, certainty=dec_certainty, direction=direction,
        )

    # No numeric candidate
    return ParsedNumber(raw=raw, certainty="speculative", direction=direction)


def _scale_factor(scale: str) -> float:
    s = scale.lower().replace(" ", "")
    return {"trămtriệu": 1e8, "tỷ": 1e9, "triệu": 1e6, "nghìn": 1e3, "ngàn": 1e3}[s]


def _scale_unit(scale: str) -> str:
    s = scale.lower().replace(" ", "")
    return {"trămtriệu": "trăm triệu", "tỷ": "tỷ", "triệu": "triệu", "nghìn": "nghìn",
            "ngàn": "nghìn"}[s]


def _detect_currency(text: str) -> str | None:
    if re.search(_CURRENCY_VI, text, re.IGNORECASE):
        return "VND"
    if re.search(r"\bUSD\b|\bđô la\b|\bđôla\b", text, re.IGNORECASE):
        return "USD"
    return None


def _detect_direction(text: str) -> str | None:
    low = text.lower()
    for kw, direction in _DIRECTION_KEYWORDS.items():
        if kw in low:
            return direction
    return None


_FINANCE_NUMERIC_ARRAYS = (
    "numbers",
    "company_financials",
    "market_snapshots",
    "macro_series",
    "supply_demand",
    "valuations",
    "other_financial_facts",
)


def normalize_facts(facts: dict) -> dict:
    """Add normalized_value/currency/direction to every numeric fact.

    Raw fields (value/raw_value) are never overwritten. ``policy_events`` is
    intentionally skipped (non-numeric core). Ambiguous/unsupported phrases get
    certainty markers instead of a guessed number.
    """
    for arr in _FINANCE_NUMERIC_ARRAYS:
        for item in facts.get(arr, []) or []:
            if not isinstance(item, dict):
                continue
            raw_val = str(item.get("raw_value") or item.get("value") or "").strip()
            if not raw_val:
                continue
            parsed = parse_vn_number(raw_val)
            # Preserve an explicit existing certainty if the phrase had one.
            if item.get("certainty") in ("certain", "probable", "speculative", "ambiguous"):
                parsed.certainty = item["certainty"]
            item.setdefault("normalized_value", parsed.value)
            item.setdefault("normalized_min", parsed.min)
            item.setdefault("normalized_max", parsed.max)
            item.setdefault("currency", parsed.currency)
            item.setdefault("currency_inferred", parsed.currency_inferred)
            item.setdefault("certainty", parsed.certainty)
            if not item.get("direction"):
                item["direction"] = parsed.direction or _detect_direction(
                    str(item.get("context") or "")
                )
    return facts
