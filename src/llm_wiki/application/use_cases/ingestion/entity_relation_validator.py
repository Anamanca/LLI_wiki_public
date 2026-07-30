"""Post-extraction validation for entity relations.

Catches: direction errors (company→person), type incompatibility,
unknown predicates, duplicate entities.

Used by: _store_entity_relations() in event_extractor.py
         backfill_entity_relations.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── Complete 64-predicate taxonomy ─────────────────────────────────────────

VALID_PREDICATES: set[str] = {
    # 1. Corporate Structure
    "is_subsidiary_of",
    "owns",
    "acquired_by",
    "merged_with",
    "spin_off_from",
    "partner_of",
    "customer_of",
    "creditor_of",
    "licenses_to",
    # 1b. Geographic (pre-existing high-frequency predicates)
    "located_in",
    "headquartered_in",
    # 2. Leadership
    "led_by",
    "founded_by",
    "works_for",
    "major_shareholder",
    # 3. Market & Competition
    "competes_with",
    "supplies_to",
    "distributes",
    "disrupts",
    # 4. Sector
    "belongs_to_sector",
    "sector_leader",
    "sector_benefits_from",
    "sector_hurt_by",
    "sector_impacted_by",
    "sector_weight_in_index",
    # 5. Investment
    "invested_in",
    "shareholder_of",
    "funded_by",
    # 6. Bonds
    "yield_inverse_to",
    "competes_for_capital_with",
    "issued_by",
    "spread_over",
    "rated_by",
    "rating_impact",
    "affected_by_exchange_rate",
    # 7. Gold
    "inverse_to",
    "priced_at_premium_to",
    "hedge_against",
    "supply_controlled_by",
    "safe_haven_when",
    # 8. Crypto
    "correlates_with_risk_on",
    "regulated_by",
    "banned_in",
    "leads",
    "dominance_over",
    "mining_dependent_on",
    "pegged_to",
    # 9. Real Estate
    "develops",
    "benefits_from_infrastructure",
    "zoning_affects",
    "infrastructure_drives_price",
    "tax_policy_affects",
    "interest_rate_sensitivity",
    "credit_growth_dependent",
    "has_price_per_sqm",
    "has_price_growth",
    "has_rental_yield",
    # 10. Macro
    "correlated_with",
    "inversely_correlated",
    "lags",
    "tightens",
    "stimulates",
    "targets",
    "drives_price_of",
    # 11. Cross-Border
    "spillover_impacts",
    "capital_flows_from",
    "export_competitor_of",
    "trade_surplus_with",
    "trade_deficit_with",
    "largest_import_from",
    "largest_export_to",
    "depreciates",
    "triggers_inflation_in",
    "reallocates_supply_chain_to",
    # 12. Financial Metrics
    "has_market_cap",
    "has_pe_ratio",
    "has_revenue",
    "has_profit",
    "has_dividend_yield",
    "has_roe",
    "has_foreign_ownership",
    "has_growth_rate",
    "has_debt_to_equity",
    "has_npl_ratio",
    "constituent_of",
    "has_weight_in",
}

# ── Direction rules: which (from_type, to_type) pairs are FORBIDDEN ─────────

COMPANY_TYPES: frozenset[str] = frozenset(
    {
        "stock_ticker",
        "company",
        "bank",
        "securities_firm",
        "fund",
        "real_estate_developer",
    }
)
PERSON_TYPES: frozenset[str] = frozenset(
    {
        "person",
        "executive",
        "founder",
        "analyst",
        "investor",
    }
)


def _is_direction_forbidden(from_type: str, to_type: str) -> bool:
    """Company → Person and Person → Person are always forbidden."""
    return (from_type in COMPANY_TYPES and to_type in PERSON_TYPES) or (
        from_type in PERSON_TYPES and to_type in PERSON_TYPES
    )


# ── Type compatibility matrix (all 64 predicates) ──────────────────────────

PREDICATE_TYPE_MATRIX: dict[str, list[tuple[str, str]]] = {
    # 1. Corporate Structure
    "is_subsidiary_of": [
        ("stock_ticker", "stock_ticker"),
        ("company", "company"),
        ("bank", "bank"),
    ],
    "owns": [
        ("stock_ticker", "stock_ticker"),
        ("company", "company"),
        ("bank", "bank"),
        ("stock_ticker", "real_estate_project"),
    ],
    "acquired_by": [("stock_ticker", "stock_ticker"), ("company", "company")],
    "merged_with": [("stock_ticker", "stock_ticker"), ("bank", "bank")],
    "spin_off_from": [("stock_ticker", "stock_ticker"), ("company", "company")],
    "partner_of": [("stock_ticker", "stock_ticker"), ("company", "company"), ("bank", "bank")],
    "customer_of": [("stock_ticker", "stock_ticker"), ("company", "company")],
    "creditor_of": [("bank", "stock_ticker"), ("bank", "company")],
    "licenses_to": [("stock_ticker", "stock_ticker"), ("company", "company")],
    # 1b. Geographic
    "located_in": [],  # Wide-open: any entity can be located somewhere
    "headquartered_in": [
        ("stock_ticker", "location"),
        ("company", "country"),
        ("company", "city"),
        ("bank", "country"),
    ],
    # 2. Leadership (person/executive → company ONLY)
    "led_by": [("stock_ticker", "executive"), ("stock_ticker", "person"), ("company", "executive")],
    "founded_by": [("stock_ticker", "founder"), ("company", "founder"), ("stock_ticker", "person")],
    "works_for": [
        ("executive", "stock_ticker"),
        ("person", "stock_ticker"),
        ("analyst", "securities_firm"),
    ],
    "major_shareholder": [
        ("person", "stock_ticker"),
        ("investor", "stock_ticker"),
        ("fund", "stock_ticker"),
    ],
    # 3. Market & Competition
    "competes_with": [("stock_ticker", "stock_ticker"), ("company", "company"), ("bank", "bank")],
    "supplies_to": [
        ("stock_ticker", "stock_ticker"),
        ("company", "company"),
        ("stock_ticker", "real_estate_developer"),
    ],
    "distributes": [("stock_ticker", "stock_ticker"), ("company", "company")],
    "disrupts": [("stock_ticker", "stock_ticker"), ("company", "sector")],
    # 4. Sector
    "belongs_to_sector": [("stock_ticker", "sector"), ("company", "sector"), ("bank", "sector")],
    "sector_leader": [("sector", "stock_ticker"), ("sector", "company")],
    "sector_benefits_from": [
        ("sector", "macro_indicator"),
        ("sector", "interest_rate"),
        ("sector", "policy"),
    ],
    "sector_hurt_by": [
        ("sector", "interest_rate"),
        ("sector", "policy"),
        ("sector", "exchange_rate"),
    ],
    "sector_impacted_by": [
        ("sector", "policy"),
        ("sector", "trade_policy"),
        ("sector", "monetary_policy"),
    ],
    "sector_weight_in_index": [("sector", "market_index")],
    # 5. Investment
    "invested_in": [
        ("stock_ticker", "stock_ticker"),
        ("fund", "stock_ticker"),
        ("stock_ticker", "real_estate_project"),
        ("bank", "stock_ticker"),
    ],
    "shareholder_of": [
        ("stock_ticker", "stock_ticker"),
        ("fund", "stock_ticker"),
        ("investor", "stock_ticker"),
    ],
    "funded_by": [("stock_ticker", "fund"), ("company", "bank"), ("real_estate_project", "bank")],
    # 6. Bonds
    "yield_inverse_to": [("bond", "interest_rate"), ("bond", "macro_indicator")],
    "competes_for_capital_with": [
        ("bond", "market_index"),
        ("bond", "sector"),
        ("precious_metal", "market_index"),
        ("real_estate_project", "market_index"),
    ],
    "issued_by": [("bond", "stock_ticker"), ("bond", "company"), ("bond", "country")],
    "spread_over": [("bond", "bond")],
    "rated_by": [("bond", "credit_rating")],
    "rating_impact": [("credit_rating", "bond")],
    "affected_by_exchange_rate": [("bond", "exchange_rate")],
    # 7. Gold
    "inverse_to": [("precious_metal", "exchange_rate"), ("precious_metal", "interest_rate")],
    "priced_at_premium_to": [("precious_metal", "precious_metal")],
    "hedge_against": [("precious_metal", "inflation")],
    "supply_controlled_by": [("precious_metal", "monetary_policy"), ("precious_metal", "bank")],
    "safe_haven_when": [("precious_metal", "market_index"), ("precious_metal", "sector")],
    # 8. Crypto
    "correlates_with_risk_on": [("cryptocurrency", "market_index"), ("cryptocurrency", "sector")],
    "regulated_by": [("cryptocurrency", "policy"), ("cryptocurrency", "country")],
    "banned_in": [("cryptocurrency", "country")],
    "leads": [("cryptocurrency", "cryptocurrency"), ("macro_indicator", "macro_indicator")],
    "dominance_over": [("cryptocurrency", "cryptocurrency")],
    "mining_dependent_on": [("cryptocurrency", "energy")],
    "pegged_to": [("cryptocurrency", "exchange_rate")],
    # 9. Real Estate
    "develops": [("real_estate_developer", "real_estate_project")],
    "benefits_from_infrastructure": [
        ("real_estate_project", "infrastructure_project"),
        ("location", "infrastructure_project"),
    ],
    "zoning_affects": [("policy", "real_estate_project"), ("policy", "location")],
    "infrastructure_drives_price": [
        ("infrastructure_project", "real_estate_project"),
        ("infrastructure_project", "location"),
    ],
    "tax_policy_affects": [
        ("policy", "real_estate_developer"),
        ("tax_policy", "real_estate_project"),
    ],
    "interest_rate_sensitivity": [
        ("real_estate_developer", "interest_rate"),
        ("sector", "interest_rate"),
    ],
    "credit_growth_dependent": [
        ("real_estate_developer", "macro_indicator"),
        ("sector", "macro_indicator"),
    ],
    "has_price_per_sqm": [
        ("location", "financial_metric"),
        ("real_estate_project", "financial_metric"),
    ],
    "has_price_growth": [
        ("location", "financial_metric"),
        ("real_estate_project", "financial_metric"),
    ],
    "has_rental_yield": [
        ("location", "financial_metric"),
        ("real_estate_project", "financial_metric"),
    ],
    # 10. Macro
    "correlated_with": [],  # Wide-open — any type pair allowed
    "inversely_correlated": [],  # Wide-open
    "lags": [("macro_indicator", "macro_indicator"), ("interest_rate", "macro_indicator")],
    "tightens": [("monetary_policy", "interest_rate"), ("monetary_policy", "macro_indicator")],
    "stimulates": [
        ("policy", "macro_indicator"),
        ("fiscal_policy", "macro_indicator"),
        ("monetary_policy", "sector"),
    ],
    "targets": [("policy", "macro_indicator"), ("monetary_policy", "inflation")],
    "drives_price_of": [
        ("macro_indicator", "commodity"),
        ("exchange_rate", "precious_metal"),
        ("interest_rate", "precious_metal"),
    ],
    # 11. Cross-Border
    "spillover_impacts": [
        ("country", "market_index"),
        ("policy", "market_index"),
        ("interest_rate", "exchange_rate"),
    ],
    "capital_flows_from": [("country", "market_index"), ("country", "stock_ticker")],
    "export_competitor_of": [("country", "country"), ("stock_ticker", "stock_ticker")],
    "trade_surplus_with": [("country", "country")],
    "trade_deficit_with": [("country", "country")],
    "largest_import_from": [("country", "country")],
    "largest_export_to": [("country", "country")],
    "depreciates": [("exchange_rate", "country"), ("exchange_rate", "currency")],
    "triggers_inflation_in": [
        ("commodity", "country"),
        ("energy", "country"),
        ("exchange_rate", "country"),
    ],
    "reallocates_supply_chain_to": [
        ("country", "industrial_park"),
        ("country", "economic_zone"),
        ("country", "country"),
    ],
    # 12. Financial Metrics
    "has_market_cap": [("stock_ticker", "financial_metric")],
    "has_pe_ratio": [("stock_ticker", "financial_metric")],
    "has_revenue": [("stock_ticker", "financial_metric"), ("company", "financial_metric")],
    "has_profit": [("stock_ticker", "financial_metric"), ("company", "financial_metric")],
    "has_dividend_yield": [("stock_ticker", "financial_metric")],
    "has_roe": [("stock_ticker", "financial_metric")],
    "has_foreign_ownership": [("stock_ticker", "financial_metric")],
    "has_growth_rate": [
        ("stock_ticker", "financial_metric"),
        ("company", "financial_metric"),
        ("macro_indicator", "financial_metric"),
    ],
    "has_debt_to_equity": [("stock_ticker", "financial_metric")],
    "has_npl_ratio": [("bank", "financial_metric")],
    "constituent_of": [("stock_ticker", "market_index")],
    "has_weight_in": [("stock_ticker", "financial_metric"), ("sector", "financial_metric")],
}


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""


def validate_relation(
    from_name: str,
    from_type: str,
    to_name: str,
    to_type: str,
    predicate: str,
    strict: bool = True,
) -> ValidationResult:
    """Validate a single entity relation.

    Checks:
      1. Predicate is in the known taxonomy
      2. Direction is not forbidden (company→person, person→person)
      3. Type pair is compatible with predicate (if matrix entry exists)

    Args:
        from_name: source entity display name
        from_type: source entity type
        to_name: target entity display name
        to_type: target entity type
        predicate: relationship predicate
        strict: if True, unknown predicates are rejected

    Returns:
        ValidationResult with valid=True/False and a reason on failure.
    """
    if not from_name or not to_name or not predicate:
        return ValidationResult(False, "missing required field")

    # 1. Known predicate
    if predicate not in VALID_PREDICATES:
        if strict:
            return ValidationResult(False, f"unknown predicate: {predicate}")
        logger.debug("Unknown predicate '%s' allowed (non-strict mode)", predicate)

    # 2. Direction check
    if _is_direction_forbidden(from_type, to_type):
        return ValidationResult(
            False, f"direction forbidden: {from_type}→{to_type} (company→person or person→person)"
        )

    # 3. Type compatibility (skip if predicate has no restrictions or is wide-open)
    allowed = PREDICATE_TYPE_MATRIX.get(predicate)
    if allowed is not None and len(allowed) > 0 and (from_type, to_type) not in allowed:
        # Check wildcard compatibility: correlated_with and inversely_correlated accept any pair
        if predicate in ("correlated_with", "inversely_correlated"):
            return ValidationResult(True)
        return ValidationResult(
            False, f"type mismatch: {predicate} not valid for {from_type}→{to_type}"
        )

    return ValidationResult(True)


def validate_relations_batch(
    relations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Validate a batch of relations. Returns (valid_rels, rejected_count, reasons).

    Each relation dict must have: from, from_type, to, to_type, predicate, confidence.
    """
    valid: list[dict[str, Any]] = []
    rejected = 0
    reasons: list[str] = []

    for rel in relations:
        if not isinstance(rel, dict):
            rejected += 1
            continue
        result = validate_relation(
            rel.get("from", ""),
            rel.get("from_type", "other"),
            rel.get("to", ""),
            rel.get("to_type", "other"),
            rel.get("predicate", ""),
        )
        if result.valid:
            valid.append(rel)
        else:
            rejected += 1
            reasons.append(
                f"{rel.get('from', '?')}"
                f" —{rel.get('predicate', '?')}→"
                f" {rel.get('to', '?')}: {result.reason}"
            )

    return valid, rejected, reasons
