"""Unit Test Suite for the Googler RSU Decision Agent."""

from __future__ import annotations

from app.observability import redact_sensitive_pii
from app.tools import (
    analyze_multi_tranche_rsu_vest_schedule,
    calculate_us_state_city_rsu_tax_liability,
    fetch_six_month_goog_stock_history_and_analyst_consensus,
    stage_rsu_liquidation_plan_with_human_confirmation,
    validate_and_collect_googler_rsu_inputs,
)
from tests.eval.eval_suite import run_golden_evaluation_suite


def test_guided_error_handling_on_missing_inputs() -> None:
    res = validate_and_collect_googler_rsu_inputs(vest_price_usd=150.0)
    assert res["status"] == "RECOVERY_REQUIRED"
    assert "llm_recovery_instructions" in res
    assert len(res["missing_fields"]) == 6


def test_multi_tranche_tax_lot_ranking() -> None:
    res = analyze_multi_tranche_rsu_vest_schedule(
        target_sale_price_usd=190.0,
        evaluation_date="2026-11-06",
        tranches=[
            {
                "tranche_id": "STCG-LowBasis",
                "vest_date": "2026-06-01",
                "vest_price_usd": 160.0,
                "vest_quantity": 50.0,
                "release_quantity": 30.0,
                "quantity_to_sell": 20.0,
            },
            {
                "tranche_id": "Loss-Lot",
                "vest_date": "2026-09-01",
                "vest_price_usd": 200.0,
                "vest_quantity": 50.0,
                "release_quantity": 30.0,
                "quantity_to_sell": 20.0,
            },
            {
                "tranche_id": "LTCG-Lot",
                "vest_date": "2024-03-01",
                "vest_price_usd": 140.0,
                "vest_quantity": 50.0,
                "release_quantity": 30.0,
                "quantity_to_sell": 20.0,
            },
        ],
    )
    assert res["status"] == "SUCCESS"
    # Loss lot should rank #1, LTCG lot #2, STCG lot #3
    assert res["recommended_lot_liquidation_order"] == ["Loss-Lot", "LTCG-Lot", "STCG-LowBasis"]


def test_nyc_and_ca_tax_calculations() -> None:
    nyc = calculate_us_state_city_rsu_tax_liability(
        us_state="NY",
        us_city="New York City",
        short_term_capital_gain_usd=1000.0,
        long_term_capital_gain_usd=2000.0,
        gross_sale_proceeds_usd=10000.0,
    )
    assert nyc["status"] == "SUCCESS"
    assert nyc["city_local_tax_rate_pct"] == 3.876
    assert nyc["state_tax_rate_pct"] == 10.90

    ca = calculate_us_state_city_rsu_tax_liability(
        us_state="CA",
        us_city="San Francisco",
        short_term_capital_gain_usd=1000.0,
        long_term_capital_gain_usd=2000.0,
        gross_sale_proceeds_usd=10000.0,
    )
    assert ca["status"] == "SUCCESS"
    assert ca["state_tax_rate_pct"] == 13.30
    assert ca["city_local_tax_rate_pct"] == 0.0


def test_six_month_history_and_analyst_consensus() -> None:
    mkt = fetch_six_month_goog_stock_history_and_analyst_consensus(target_sale_price_usd=195.0)
    assert mkt["ticker"] == "GOOG"
    assert mkt["analyst_consensus_rating"] == "BUY"
    assert mkt["consensus_12m_target_price_usd"] > 195.0


def test_pii_redaction_and_golden_suite() -> None:
    clean, cats = redact_sensitive_pii("SSN 123-45-6789 and email rishabh@google.com")
    assert "123-45-6789" not in clean
    assert "REDACTED_SSN" in cats
    assert "REDACTED_EMAIL" in cats

    suite = run_golden_evaluation_suite()
    assert suite["all_passed"] is True
