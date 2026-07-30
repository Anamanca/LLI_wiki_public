"""Tests for the migrated 64-predicate entity relation taxonomy."""

from llm_wiki.application.use_cases.ingestion.entity_relation_validator import (
    PREDICATE_TYPE_MATRIX,
    VALID_PREDICATES,
    _is_direction_forbidden,
)


class TestEntityRelationValidator:
    def test_all_valid_predicates_present(self):
        # The taxonomy should contain the 64 canonical predicates from wiki_prompts.py
        expected_core = {
            "is_subsidiary_of",
            "owns",
            "acquired_by",
            "merged_with",
            "spin_off_from",
            "partner_of",
            "customer_of",
            "creditor_of",
            "licenses_to",
            "led_by",
            "founded_by",
            "works_for",
            "major_shareholder",
            "competes_with",
            "supplies_to",
            "distributes",
            "disrupts",
            "belongs_to_sector",
            "sector_leader",
            "sector_benefits_from",
            "sector_hurt_by",
            "sector_impacted_by",
            "sector_weight_in_index",
            "invested_in",
            "shareholder_of",
            "funded_by",
            "yield_inverse_to",
            "competes_for_capital_with",
            "issued_by",
            "spread_over",
            "rated_by",
            "rating_impact",
            "affected_by_exchange_rate",
            "inverse_to",
            "priced_at_premium_to",
            "hedge_against",
            "supply_controlled_by",
            "safe_haven_when",
            "correlates_with_risk_on",
            "regulated_by",
            "banned_in",
            "leads",
            "dominance_over",
            "mining_dependent_on",
            "pegged_to",
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
            "correlated_with",
            "inversely_correlated",
            "lags",
            "tightens",
            "stimulates",
            "targets",
            "drives_price_of",
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
        assert expected_core.issubset(VALID_PREDICATES)

    def test_company_to_person_direction_forbidden(self):
        assert _is_direction_forbidden("stock_ticker", "person") is True
        assert _is_direction_forbidden("company", "executive") is True

    def test_person_to_person_direction_forbidden(self):
        assert _is_direction_forbidden("person", "person") is True
        assert _is_direction_forbidden("analyst", "investor") is True

    def test_allowed_directions(self):
        # person -> company
        assert _is_direction_forbidden("executive", "stock_ticker") is False
        # company -> company
        assert _is_direction_forbidden("stock_ticker", "stock_ticker") is False

    def test_belongs_to_sector_requires_company_to_sector(self):
        allowed = PREDICATE_TYPE_MATRIX["belongs_to_sector"]
        assert ("stock_ticker", "sector") in allowed
        assert ("company", "sector") in allowed

    def test_predicates_have_type_matrix(self):
        # Every predicate should have at least an empty entry in the matrix
        for predicate in VALID_PREDICATES:
            assert predicate in PREDICATE_TYPE_MATRIX, f"{predicate} missing from matrix"
