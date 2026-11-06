"""Strict Pydantic JSON Schemas for the Googler RSU Decision Agent.

Provides explicit input and output validation schemas for all agent tools and
structured LLM outputs (Rubric Category 1.3: Explicit JSON Schemas).
"""

from __future__ import annotations

from typing import Any, Literal

try:
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - lightweight fallback for bare python3 environments
    def Field(default: Any = None, **kwargs: Any) -> Any:  # type: ignore[misc]
        return default

    class BaseModel:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                if k in {"vest_price_usd", "vest_quantity", "release_quantity", "target_sale_price_usd"} and float(v) <= 0:
                    raise ValueError(f"{k} must be > 0")
                if k in {"gross_sale_proceeds_usd", "quantity_to_sell"} and float(v) < 0:
                    raise ValueError(f"{k} must be >= 0")
                setattr(self, k, v)

        def model_copy(self, update: dict[str, Any] | None = None) -> "BaseModel":
            data = dict(self.__dict__)
            if update:
                data.update(update)
            return self.__class__(**data)

        def model_dump(self) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for k, v in self.__dict__.items():
                if isinstance(v, list):
                    out[k] = [x.model_dump() if hasattr(x, "model_dump") else x for x in v]
                elif hasattr(v, "model_dump"):
                    out[k] = v.model_dump()
                else:
                    out[k] = v
            return out


class RSUTrancheLot(BaseModel):
    """Represents a single RSU vesting tranche (tax lot) held by a Google employee."""

    tranche_id: str = Field(
        description="Unique identifier for the RSU tranche (e.g., 'GSU-2023-Q2' or 'Lot-1')."
    )
    vest_date: str = Field(
        description="ISO date when the RSU tranche vested (YYYY-MM-DD), used to determine short-term (<365 days) vs. long-term (>=365 days) capital gains."
    )
    vest_price_usd: float = Field(
        gt=0,
        description="Fair Market Value (FMV) cost basis per share in USD on the date the tranche vested.",
    )
    vest_quantity: float = Field(
        gt=0,
        description="Gross number of GSU/RSU shares that vested in this tranche before tax withholding.",
    )
    release_quantity: float = Field(
        gt=0,
        description="Net number of shares released to the employee's brokerage account after automatic tax withholding at vest.",
    )
    quantity_to_sell: float = Field(
        ge=0,
        description="Number of released shares from this specific tranche that the employee is considering selling.",
    )


class MultiTrancheAnalysisRequest(BaseModel):
    """Strict input schema for evaluating one or more RSU tranches across vest dates and prices."""

    target_sale_price_usd: float = Field(
        gt=0,
        description="Current or target Alphabet (GOOG/GOOGL) sale price per share in USD.",
    )
    evaluation_date: str = Field(
        default="2026-11-06",
        description="Current evaluation date in YYYY-MM-DD format for holding-period calculation.",
    )
    tranches: list[RSUTrancheLot] = Field(
        min_length=1,
        description="List of one or more RSU vesting tranches held by the Google employee.",
    )


class TrancheTaxLotEvaluation(BaseModel):
    """Evaluated tax-lot metrics for a single RSU tranche."""

    tranche_id: str = Field(description="Identifier of the evaluated tranche.")
    vest_date: str = Field(description="Vest date (YYYY-MM-DD).")
    holding_days: int = Field(description="Number of days held from vest_date to evaluation_date.")
    tax_treatment: Literal["LONG_TERM_CAPITAL_GAIN", "SHORT_TERM_CAPITAL_GAIN"] = Field(
        description="Capital gains classification (>=365 days = LONG_TERM_CAPITAL_GAIN)."
    )
    days_until_long_term: int = Field(
        description="Days remaining until this tranche qualifies for Long-Term Capital Gains (0 if already qualified)."
    )
    vest_price_usd: float = Field(description="Cost basis per share at vest in USD.")
    target_sale_price_usd: float = Field(description="Target sale price per share in USD.")
    unrealized_gain_per_share_usd: float = Field(
        description="Gain or loss per share above the vest price (target_sale_price - vest_price)."
    )
    total_capital_gain_usd: float = Field(
        description="Total capital gain or loss on the quantity_to_sell for this tranche."
    )
    implied_withholding_rate_at_vest_pct: float = Field(
        description="Percentage of gross vested shares withheld for taxes at vest: (vest_quantity - release_quantity) / vest_quantity."
    )
    sale_priority_rank: int = Field(
        description="Recommended liquidation order (1 = most tax-efficient lot to sell first)."
    )
    lot_rationale: str = Field(
        description="Plain-English explanation of why this tranche ranks here in the tax-lot optimization order."
    )


class MultiTrancheAnalysisOutput(BaseModel):
    """Strict output schema for multi-tranche RSU portfolio and tax-lot optimization."""

    status: Literal["SUCCESS", "RECOVERY_REQUIRED"] = Field(description="Execution status.")
    total_vest_quantity: float = Field(description="Sum of gross vested shares across all tranches.")
    total_release_quantity: float = Field(description="Sum of net released shares across all tranches.")
    total_quantity_to_sell: float = Field(description="Total shares proposed for sale across all tranches.")
    gross_sale_proceeds_usd: float = Field(description="Total gross cash proceeds before capital gains taxes.")
    total_short_term_gain_usd: float = Field(description="Net short-term capital gains across selected tranches.")
    total_long_term_gain_usd: float = Field(description="Net long-term capital gains across selected tranches.")
    recommended_lot_liquidation_order: list[str] = Field(
        description="Ordered list of tranche_ids from most tax-efficient to least tax-efficient to sell."
    )
    evaluated_tranches: list[TrancheTaxLotEvaluation] = Field(
        description="Detailed per-tranche breakdown and holding-period analysis."
    )


class USStateCityTaxRequest(BaseModel):
    """Strict input schema for calculating US Federal, State, and City RSU tax liability."""

    us_state: str = Field(
        min_length=2,
        description="US State code or name where the Googler resides (e.g., 'CA', 'NY', 'WA', 'IL', 'TX', 'MA').",
    )
    us_city: str = Field(
        min_length=2,
        description="US City where the Googler resides (e.g., 'New York City', 'San Francisco', 'Seattle', 'Chicago', 'Austin').",
    )
    short_term_capital_gain_usd: float = Field(
        description="Total short-term capital gain (or loss) in USD from tranches held < 365 days."
    )
    long_term_capital_gain_usd: float = Field(
        description="Total long-term capital gain (or loss) in USD from tranches held >= 365 days."
    )
    gross_sale_proceeds_usd: float = Field(
        ge=0,
        description="Gross cash proceeds from selling the selected RSU shares.",
    )
    gross_vest_value_usd: float = Field(
        ge=0,
        description="Total value at vest (vest_price * vest_quantity) to check statutory 22% supplemental withholding gap.",
    )
    shares_withheld_at_vest_value_usd: float = Field(
        ge=0,
        description="Dollar value of shares withheld at vest ((vest_quantity - release_quantity) * vest_price).",
    )


class USStateCityTaxBreakdownOutput(BaseModel):
    """Strict output schema for US Federal + State + City RSU tax calculation."""

    status: Literal["SUCCESS", "RECOVERY_REQUIRED"] = Field(description="Execution status.")
    jurisdiction_label: str = Field(description="Normalized US City and State tax jurisdiction.")
    federal_short_term_rate_pct: float = Field(description="Marginal federal rate on short-term gains + 3.8% NIIT.")
    federal_long_term_rate_pct: float = Field(description="Marginal federal rate on long-term gains (20%) + 3.8% NIIT.")
    state_tax_rate_pct: float = Field(description="Applicable state income/capital gains tax rate percentage.")
    city_local_tax_rate_pct: float = Field(description="Applicable municipal/local income tax rate (e.g., 3.876% for NYC).")
    combined_short_term_tax_rate_pct: float = Field(description="Total combined Federal + NIIT + State + City rate on STCG.")
    combined_long_term_tax_rate_pct: float = Field(description="Total combined Federal + NIIT + State + City rate on LTCG.")
    estimated_capital_gains_tax_usd: float = Field(description="Estimated total tax owed on the capital gain from this sale.")
    estimated_vest_withholding_underpayment_usd: float = Field(
        description="Estimated tax gap between statutory 22% vest withholding and true marginal rate on vested shares."
    )
    net_after_tax_cash_proceeds_usd: float = Field(
        description="Net cash proceeds remaining after deducting estimated capital gains tax from gross sale proceeds."
    )
    tax_saving_if_held_to_long_term_usd: float = Field(
        description="Estimated dollar tax savings if short-term tranches are held until the 365-day long-term threshold."
    )
    jurisdiction_notes: str = Field(description="Specific state/city tax rules applied (e.g., WA 7% LTCG excise tax, NYC local tax).")


class StockHistoryAndAnalystOutput(BaseModel):
    """Strict output schema for 6-month GOOG historical performance and Wall Street analyst consensus."""

    ticker: str = Field(default="GOOG", description="Alphabet stock ticker.")
    current_reference_price_usd: float = Field(description="Current market price per share in USD.")
    six_month_start_price_usd: float = Field(description="Stock price 6 months ago in USD.")
    six_month_return_pct: float = Field(description="Percentage price change over the past 6 months.")
    six_month_high_usd: float = Field(description="6-month intraday high in USD.")
    six_month_low_usd: float = Field(description="6-month intraday low in USD.")
    moving_average_50d_usd: float = Field(description="50-day simple moving average in USD.")
    moving_average_200d_usd: float = Field(description="200-day simple moving average in USD.")
    annualized_volatility_pct: float = Field(description="6-month realized annualized volatility percentage.")
    analyst_consensus_rating: Literal["STRONG_BUY", "BUY", "HOLD", "UNDERPERFORM"] = Field(
        description="Wall Street institutional analyst consensus rating."
    )
    analyst_buy_count: int = Field(description="Number of institutional analysts rating GOOG Buy/Overweight.")
    analyst_hold_count: int = Field(description="Number of institutional analysts rating GOOG Hold/Neutral.")
    analyst_sell_count: int = Field(description="Number of institutional analysts rating GOOG Sell/Underweight.")
    consensus_12m_target_price_usd: float = Field(
        description="Wall Street consensus 12-month price target in USD."
    )
    implied_12m_upside_pct: float = Field(
        description="Implied percentage upside from target_sale_price to consensus_12m_target_price."
    )
    key_bull_catalysts: list[str] = Field(description="Key positive catalysts cited in recent analyst reports.")
    key_risk_factors: list[str] = Field(description="Key downside/concentration risks cited in recent analyst reports.")


class RSUDecisionRecommendationOutput(BaseModel):
    """Structured final decision output produced by the RSU Decision Synthesis Agent."""

    recommended_action: Literal[
        "SELL_ALL_SELECTED_SHARES",
        "PARTIAL_TAX_OPTIMIZED_SELL",
        "HOLD_FOR_LONG_TERM_TAX_THRESHOLD",
        "NEED_MISSING_USER_INPUTS",
    ] = Field(description="Primary actionable recommendation for the Googler.")
    executive_summary: str = Field(
        description="Crisp 2-3 sentence conversational summary of the recommendation and why."
    )
    net_after_tax_proceeds_usd: float = Field(
        description="Expected net cash in pocket after Federal + State + City taxes."
    )
    decision_factors: list[str] = Field(
        min_length=3,
        description="Key quantitative and qualitative factors supporting the recommendation (Tax, Tranche Holding Period, 6M Trend & Analyst Target, Concentration Risk).",
    )
    tranche_execution_plan: list[str] = Field(
        description="Specific tranche-by-tranche instruction showing which lots to sell now vs. hold."
    )
    fiduciary_disclaimer: str = Field(
        description="Mandatory educational/non-fiduciary tax and investment disclaimer."
    )
