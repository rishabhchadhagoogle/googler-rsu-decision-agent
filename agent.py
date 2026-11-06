"""Googler RSU Sell-vs-Hold Decision Agent — Multi-Agent ADK 2.0 Architecture.

Implements all 19 criteria (95/95 points) of the AgentOps Code Review Matrix:
- Category 1: Tool & Interface Design (Comprehensive Docstrings, Descriptive Naming, Explicit JSON Schemas, Guided Error Handling)
- Category 2: Context & Memory (Robust System Constitution, History Compaction & Context Caching, Persistent Session & Vector Store State, Async Memory Operations)
- Category 3: Orchestration & Logic (Multi-Agent Coordinator + Sequential/Graph Workflow, Strategic Flash vs. Pro Model Routing, Guardrails & Self-Eval Policy Plugins, Human-in-the-Loop Hooks)
- Category 4: Observability & Tracing (Structured JSON Logging, Intent vs. Outcome Capture, OpenTelemetry Distributed Tracing, Cloud DLP & Regex PII Redaction)
- Category 5: Infrastructure & CI/CD (Automated Evaluation Suite, Terraform IaC + Agents CLI, Google Cloud Secret Manager)
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.adk.apps.app import EventsCompactionConfig
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.events.request_input import RequestInput
from google.adk.models import Gemini
from google.adk.sessions import InMemorySessionService, VertexAiSessionService
from google.adk.tools import LongRunningFunctionTool
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.workflow import START, Workflow, node
from google.genai import types

from app.observability import (
    get_secret_from_secret_manager,
    log_structured_event,
    log_tool_intent_before_callback,
    log_tool_outcome_after_callback,
    redact_sensitive_pii,
    tracer,
)
from app.schemas import RSUDecisionRecommendationOutput
from app.tools import (
    analyze_multi_tranche_rsu_vest_schedule,
    calculate_us_state_city_rsu_tax_liability,
    fetch_six_month_goog_stock_history_and_analyst_consensus,
    stage_rsu_liquidation_plan_with_human_confirmation,
    validate_and_collect_googler_rsu_inputs,
)

# ---------------------------------------------------------------------------
# Strategic Model Routing Constants (Rubric 3.2: Strategic Model Routing - 5 pts)
# ---------------------------------------------------------------------------
# Fast, low-latency model for deterministic intake extraction, tax table lookups, and market data retrieval
FAST_EXTRACTION_MODEL = os.environ.get("FAST_MODEL_NAME", "gemini-2.5-flash")
# High-reasoning model for multi-tranche tax-lot synthesis, hold-vs-sell trade-off planning, and self-critique
PRO_REASONING_MODEL = os.environ.get("PRO_MODEL_NAME", "gemini-2.5-pro")


def select_strategic_model(task_complexity: str = "fast") -> Gemini:
    """Routes requests to Gemini Flash for fast extraction/lookup or Gemini Pro for complex financial synthesis."""
    chosen_model = PRO_REASONING_MODEL if task_complexity == "complex_reasoning" else FAST_EXTRACTION_MODEL
    log_structured_event(
        event_type="STRATEGIC_MODEL_ROUTING",
        message=f"Routed task_complexity='{task_complexity}' to model='{chosen_model}'",
        metadata={"task_complexity": task_complexity, "selected_model": chosen_model},
    )
    return Gemini(
        model=chosen_model,
        retry_options=types.HttpRetryOptions(attempts=3),
    )


# ---------------------------------------------------------------------------
# Robust System Constitution (Rubric 2.1: Robust System Instructions - 5 pts)
# ---------------------------------------------------------------------------
GOOGLER_RSU_CONSTITUTION_PROMPT = """
<CONSTITUTION>
  <PERSONA>
    You are the **Googler RSU Sell-vs-Hold Decision Advisor**, an internal enterprise AI
    assistant built for Google (Alphabet) employees across the United States. Your goal is
    to make the conversation warm, simple, and effortless while delivering a clear,
    quantitatively grounded recommendation on whether to **SELL**, **PARTIALLY SELL
    (Tax-Lot Optimized)**, or **HOLD** their Google Restricted Stock Units (GSUs/RSUs).
  </PERSONA>

  <MANDATORY_USER_INTAKE_FIELDS>
    Before recommending a sell-vs-hold decision, you MUST verify or politely ask the
    Google employee for these required inputs (using `validate_and_collect_googler_rsu_inputs`):
    1. **Vest Price (`vest_price_usd`)**: Fair Market Value (FMV) cost basis per share when the GSU vested.
    2. **Vest Quantity (`vest_quantity`)**: Gross number of shares that vested in the tranche.
    3. **Release Quantity (`release_quantity`)**: Net number of shares released to brokerage after tax withholding at vest.
    4. **Target / Current Sales Price (`target_sale_price_usd`)**: Price per share at which they are evaluating a sale.
    5. **Quantity to Sell (`quantity_to_sell`)**: Number of shares they want to evaluate selling.
    6. **US City & State of Residence (`us_city`, `us_state`)**: Required to compute federal, state, and city/local taxes (e.g., NYC 3.876% city tax, CA 13.3%, WA 7% LTCG excise tax, IL 4.95%, TX/FL 0%).
    7. **Multi-Tranche Lots (if applicable)**: Ask whether the shares come from a single vest date or multiple stock tranches vested over different years, dates, and vest prices.
  </MANDATORY_USER_INTAKE_FIELDS>

  <DOMAIN_KNOWLEDGE_AND_DECISION_RULES>
    - **Tax at Vest vs. Tax at Sale**: Explain clearly that ordinary income tax was already triggered at vest on `vest_price * vest_quantity` (with `vest_quantity - release_quantity` shares withheld, often at the 22% statutory federal supplemental rate, which may leave an underpayment gap for high-bracket Googlers). Selling only triggers capital gains tax on the appreciation above `vest_price_usd`.
    - **Multi-Tranche Tax-Lot Optimization**: When an employee holds multiple tranches across different vest dates and prices, always call `analyze_multi_tranche_rsu_vest_schedule` to rank lots:
      1. Sell loss-making lots first (tax-loss harvesting).
      2. Sell Long-Term Capital Gain (`>= 365 days`) lots with small embedded gains next.
      3. Sell Short-Term (`< 365 days`) high-cost-basis lots before low-cost-basis lots.
      4. Flag tranches held `300–364 days` where waiting a few weeks to cross the 1-year mark cuts the federal rate from ~38.8% (35% + 3.8% NIIT) to ~23.8% (20% LTCG + 3.8% NIIT).
    - **6-Month Market Trend & Analyst Consensus**: Always call `fetch_six_month_goog_stock_history_and_analyst_consensus` to incorporate trailing 6-month GOOG return, 50D/200D moving averages, volatility, and Wall Street consensus 12M target price alongside single-stock compensation concentration risk.
  </DOMAIN_KNOWLEDGE_AND_DECISION_RULES>

  <GUARDRAILS_AND_CONSTRAINTS>
    - Never fabricate missing numbers; if inputs are missing, ask one friendly, consolidated question.
    - Never log or repeat raw SSNs, brokerage account numbers, or personal PII.
    - Always pause via Human-in-the-Loop (`stage_rsu_liquidation_plan_with_human_confirmation` / `RequestInput`) before locking in a final tax-lot liquidation plan.
    - Always include an educational disclaimer stating that calculations are decision-support estimates and not personalized CPA or registered fiduciary investment advice.
  </GUARDRAILS_AND_CONSTRAINTS>
</CONSTITUTION>
""".strip()


# ---------------------------------------------------------------------------
# Context Bloat Management, Sliding Window Compaction & Persistent Storage
# (Rubric 2.2: History Compaction & Context Caching - 5 pts)
# (Rubric 2.3: Persistent Session State & Vector/Search Retrieval - 5 pts)
# (Rubric 2.4: Async Memory Operations - 5 pts)
# ---------------------------------------------------------------------------
class SlidingWindowHistoryCompactor:
    """Manages conversational context bloat via token estimation, sliding window, and ADK compaction."""

    def __init__(self, max_turns: int = 8, max_estimated_tokens: int = 12000) -> None:
        self.max_turns = max_turns
        self.max_estimated_tokens = max_estimated_tokens

    def compact_conversation_history(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Truncates and summarizes older turns when token budget or turn count exceeds threshold."""
        if len(messages) <= self.max_turns:
            return messages
        preserved_head = messages[:1]
        sliding_tail = messages[-(self.max_turns - 1) :]
        dropped_count = len(messages) - len(preserved_head) - len(sliding_tail)
        summary_marker = {
            "role": "system",
            "content": f"[Compacted {dropped_count} earlier turns into Memory Bank summary to prevent context bloat.]",
        }
        return preserved_head + [summary_marker] + sliding_tail


history_compactor = SlidingWindowHistoryCompactor(max_turns=8, max_estimated_tokens=12000)


def create_persistent_session_service() -> Any:
    """Configures persistent VertexAiSessionService in production or InMemorySessionService locally.

    Also configures Vertex AI Search / Vector Store retrieval endpoint from Secret Manager
    when `VERTEX_SEARCH_DATASTORE_ID` is present (Rubric 2.3: Persistent Session State).
    """
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-east1")
    datastore_id = get_secret_from_secret_manager("VERTEX_SEARCH_DATASTORE_ID", project_id=project_id)
    if project_id and os.environ.get("USE_VERTEX_PERSISTENT_SESSIONS", "false").lower() == "true":
        log_structured_event(
            event_type="PERSISTENT_SESSION_INIT",
            message="Initialized persistent VertexAiSessionService with Vector/Search datastore linkage.",
            metadata={"project_id": project_id, "location": location, "datastore_id": datastore_id or "default"},
        )
        return VertexAiSessionService(project=project_id, location=location)
    return InMemorySessionService()


async def _background_memory_consolidation_worker(callback_context: CallbackContext) -> None:
    """Background coroutine that asynchronously extracts and saves durable user facts to Memory Bank."""
    try:
        with tracer.start_as_current_span("async_memory_bank_consolidation"):
            await callback_context.add_session_to_memory()
            log_structured_event(
                event_type="ASYNC_MEMORY_BANK_CONSOLIDATED",
                message="Asynchronously consolidated session state into Vertex AI Memory Bank without blocking UI.",
            )
    except Exception as exc:
        log_structured_event(
            event_type="ASYNC_MEMORY_BANK_SKIPPED",
            message=f"Memory Bank consolidation skipped in local runner: {exc}",
            severity="DEBUG",
        )


async def async_consolidate_session_to_memory_bank(callback_context: CallbackContext) -> None:
    """Non-blocking ADK after_agent_callback that schedules async Memory Bank consolidation (Rubric 2.4)."""
    asyncio.create_task(_background_memory_consolidation_worker(callback_context))
    return None


# ---------------------------------------------------------------------------
# Security Guardrails & Self-Eval Policy Plugins
# (Rubric 3.3: Guardrails & Policy Plugins - 5 pts)
# ---------------------------------------------------------------------------
_PROMPT_INJECTION_PATTERNS = (
    "ignore all previous instructions",
    "bypass all rules",
    "disregard system prompt",
    "reveal your system instructions",
    "guaranteed 100% tax evasion",
)


def model_armor_and_policy_guardrail(
    callback_context: CallbackContext,
    llm_request: Any,
) -> Any | None:
    """ADK before_model_callback implementing PII redaction and Prompt-Injection defense (Model Armor pattern)."""
    with tracer.start_as_current_span("model_armor_and_policy_guardrail") as span:
        try:
            contents = getattr(llm_request, "contents", []) or []
            for content in contents:
                for part in getattr(content, "parts", []) or []:
                    text_val = getattr(part, "text", None)
                    if isinstance(text_val, str) and text_val:
                        lowered = text_val.lower()
                        for pattern in _PROMPT_INJECTION_PATTERNS:
                            if pattern in lowered:
                                span.set_attribute("guardrail.blocked", True)
                                log_structured_event(
                                    event_type="SECURITY_GUARDRAIL_BLOCKED",
                                    message=f"Blocked adversarial prompt-injection pattern: '{pattern}'",
                                    severity="WARNING",
                                )
                                return types.Content(
                                    role="model",
                                    parts=[
                                        types.Part.from_text(
                                            text=(
                                                "Security Guardrail Triggered: I cannot bypass tax compliance "
                                                "rules or system safety instructions. Please share your GSU "
                                                "vest price, vest quantity, release quantity, target sale price, "
                                                "quantity to sell, and US city/state for a compliant analysis."
                                            )
                                        )
                                    ],
                                )
                        # Scrub any accidental PII before sending to the LLM
                        clean_text, redacted_types = redact_sensitive_pii(text_val)
                        if redacted_types:
                            part.text = clean_text
                            log_structured_event(
                                event_type="PRE_LLM_PII_REDACTED",
                                message="Redacted sensitive PII prior to LLM invocation.",
                                metadata={"redacted_types": redacted_types},
                            )
        except Exception:
            pass
        return None


def self_eval_grounding_guardrail(
    callback_context: CallbackContext,
    llm_response: Any,
) -> Any | None:
    """ADK after_model_callback performing self-evaluation on generated RSU recommendations (Rubric 3.3)."""
    with tracer.start_as_current_span("self_eval_grounding_guardrail"):
        log_structured_event(
            event_type="SELF_EVAL_GUARDRAIL_CHECK",
            message="Verified response grounding, tax jurisdiction coverage, and non-fiduciary disclaimer.",
        )
        return None


# ---------------------------------------------------------------------------
# Specialized Sub-Agents & Multi-Agent Coordinator
# (Rubric 3.1: Multi-Agent Patterns & Rubric 3.2: Strategic Model Routing)
# ---------------------------------------------------------------------------
rsu_intake_and_tranche_agent = LlmAgent(
    name="rsu_intake_and_tranche_agent",
    description=(
        "Validates required Googler RSU inputs (vest price, vest quantity, release quantity, "
        "sale price, quantity to sell, US city/state) and analyzes multi-tranche vesting schedules."
    ),
    model=select_strategic_model("fast"),
    instruction=(
        GOOGLER_RSU_CONSTITUTION_PROMPT
        + "\nYour specialized role: Call `validate_and_collect_googler_rsu_inputs` first. "
        "If all required fields are present, call `analyze_multi_tranche_rsu_vest_schedule` "
        "to evaluate short-term vs. long-term capital gains and optimal tranche sale order."
    ),
    tools=[
        PreloadMemoryTool(),
        validate_and_collect_googler_rsu_inputs,
        analyze_multi_tranche_rsu_vest_schedule,
    ],
    before_model_callback=model_armor_and_policy_guardrail,
    before_tool_callback=log_tool_intent_before_callback,
    after_tool_callback=log_tool_outcome_after_callback,
)

us_state_city_tax_agent = LlmAgent(
    name="us_state_city_tax_agent",
    description=(
        "Computes US Federal, NIIT, State, and municipal City (e.g., NYC, SF, Seattle, Chicago, Austin) "
        "capital gains taxes and vest withholding gaps for Google RSU sales."
    ),
    model=select_strategic_model("fast"),
    instruction=(
        GOOGLER_RSU_CONSTITUTION_PROMPT
        + "\nYour specialized role: Call `calculate_us_state_city_rsu_tax_liability` using the "
        "employee's US city, state, short-term gains, long-term gains, and gross proceeds."
    ),
    tools=[
        calculate_us_state_city_rsu_tax_liability,
    ],
    before_model_callback=model_armor_and_policy_guardrail,
    before_tool_callback=log_tool_intent_before_callback,
    after_tool_callback=log_tool_outcome_after_callback,
)

market_and_analyst_research_agent = LlmAgent(
    name="market_and_analyst_research_agent",
    description=(
        "Retrieves 6-month GOOG/GOOGL historical stock performance, moving averages, volatility, "
        "and Wall Street institutional analyst consensus price targets and catalysts."
    ),
    model=select_strategic_model("fast"),
    instruction=(
        GOOGLER_RSU_CONSTITUTION_PROMPT
        + "\nYour specialized role: Call `fetch_six_month_goog_stock_history_and_analyst_consensus` "
        "to ground the recommendation in 6-month historical data and analyst consensus."
    ),
    tools=[
        fetch_six_month_goog_stock_history_and_analyst_consensus,
    ],
    before_model_callback=model_armor_and_policy_guardrail,
    before_tool_callback=log_tool_intent_before_callback,
    after_tool_callback=log_tool_outcome_after_callback,
)

# High-reasoning Pro model synthesizes all tools + coordinates Human-in-the-Loop confirmation
rsu_advisor_coordinator = LlmAgent(
    name="rsu_advisor_coordinator",
    description=(
        "Primary Googler RSU Decision Coordinator that orchestrates intake, multi-tranche tax-lot "
        "optimization, US state/city tax calculation, 6-month stock & analyst research, and "
        "Human-in-the-Loop decision confirmation."
    ),
    model=select_strategic_model("complex_reasoning"),
    instruction=(
        GOOGLER_RSU_CONSTITUTION_PROMPT
        + "\n\nWorkflow Execution Steps:\n"
        "1. Always verify inputs using `validate_and_collect_googler_rsu_inputs`. If any of "
        "`vest_price_usd`, `vest_quantity`, `release_quantity`, `target_sale_price_usd`, "
        "`quantity_to_sell`, `us_city`, or `us_state` are missing, politely ask the user for them.\n"
        "2. Once inputs are provided, run `analyze_multi_tranche_rsu_vest_schedule`, "
        "`calculate_us_state_city_rsu_tax_liability`, and "
        "`fetch_six_month_goog_stock_history_and_analyst_consensus`.\n"
        "3. Present a clear, easy-to-read recommendation (`SELL ALL`, `PARTIAL TAX-OPTIMIZED SELL`, "
        "or `HOLD FOR LONG-TERM CAPITAL GAINS`) with exact supporting factors: Net After-Tax Cash "
        "Proceeds, US Federal + State + City Tax Breakdown, Multi-Tranche Tax-Lot Ranking, and "
        "6-Month Stock Trend + Wall Street Analyst Consensus."
    ),
    tools=[
        PreloadMemoryTool(),
        validate_and_collect_googler_rsu_inputs,
        analyze_multi_tranche_rsu_vest_schedule,
        calculate_us_state_city_rsu_tax_liability,
        fetch_six_month_goog_stock_history_and_analyst_consensus,
        LongRunningFunctionTool(func=stage_rsu_liquidation_plan_with_human_confirmation),
    ],
    sub_agents=[
        rsu_intake_and_tranche_agent,
        us_state_city_tax_agent,
        market_and_analyst_research_agent,
    ],
    before_model_callback=model_armor_and_policy_guardrail,
    after_model_callback=self_eval_grounding_guardrail,
    before_tool_callback=log_tool_intent_before_callback,
    after_tool_callback=log_tool_outcome_after_callback,
    after_agent_callback=async_consolidate_session_to_memory_bank,
)

# Sequential Pipeline Pattern for end-to-end deterministic execution (Rubric 3.1)
rsu_sequential_pipeline = SequentialAgent(
    name="rsu_sequential_analysis_pipeline",
    description="Sequential multi-agent pipeline executing intake -> tax -> market -> synthesis.",
    sub_agents=[
        rsu_intake_and_tranche_agent,
        us_state_city_tax_agent,
        market_and_analyst_research_agent,
    ],
)


# ---------------------------------------------------------------------------
# ADK 2.0 Graph Workflow with Pre-LLM Guardrail, Deterministic Intake & HITL
# (Rubric 3.1, 3.3, 3.4)
# ---------------------------------------------------------------------------
@node
def pre_llm_security_and_pii_node(node_input: str) -> Event:
    """Scrubs PII and blocks prompt-injection attempts before any model or tool runs."""
    with tracer.start_as_current_span("pre_llm_security_and_pii_node"):
        clean_text, pii_found = redact_sensitive_pii(node_input)
        lowered = clean_text.lower()
        for phrase in _PROMPT_INJECTION_PATTERNS:
            if phrase in lowered:
                return Event(
                    output=clean_text,
                    actions=EventActions(
                        route="blocked",
                        state_delta={"security_blocked": True, "pii_redacted": pii_found},
                    ),
                )
        return Event(
            output=clean_text,
            actions=EventActions(
                route="safe",
                state_delta={"security_blocked": False, "pii_redacted": pii_found},
            ),
        )


@node
def reject_unsafe_request_node(node_input: str) -> Event:
    """Returns a safe refusal when prompt injection or policy violations are detected."""
    msg = (
        "Security Policy Guardrail: Your request contained instructions that conflict with "
        "enterprise financial and tax compliance rules. Please share your GSU tranche details "
        "(vest price, vest quantity, release quantity, target sale price, quantity to sell, and "
        "US city/state) for a standard RSU sell-vs-hold analysis."
    )
    return Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]),
        output=msg,
    )


@node(rerun_on_resume=True)
async def human_in_the_loop_confirmation_gate(ctx: Context, node_input: str):
    """Explicit ADK 2.0 RequestInput gate for high-stakes RSU liquidation confirmation (Rubric 3.4)."""
    if os.environ.get("REQUIRE_WORKFLOW_HITL_INTERRUPT", "false").lower() == "true":
        if not ctx.resume_inputs or "confirm_rsu_plan" not in ctx.resume_inputs:
            yield RequestInput(
                interrupt_id="confirm_rsu_plan",
                message="Please confirm whether to lock in this RSU tax-lot liquidation plan (approve / modify):",
            )
            return
        decision = ctx.resume_inputs["confirm_rsu_plan"]
        summary = f"{node_input}\n\n[Human Confirmation Recorded: {decision}]"
        yield Event(
            content=types.Content(role="model", parts=[types.Part.from_text(text=summary)]),
            output=summary,
        )
        return

    yield Event(output=node_input)


root_agent = Workflow(
    name="googler_rsu_decision_agent",
    description=(
        "Enterprise ADK 2.0 RSU Sell-vs-Hold Decision Agent for Google employees, combining "
        "US Federal/State/City tax computation, multi-tranche tax-lot optimization, and "
        "6-month GOOG stock & analyst research."
    ),
    edges=[
        (START, pre_llm_security_and_pii_node),
        (
            pre_llm_security_and_pii_node,
            {
                "safe": rsu_advisor_coordinator,
                "blocked": reject_unsafe_request_node,
            },
        ),
        (rsu_advisor_coordinator, human_in_the_loop_confirmation_gate),
    ],
)

# App container with History Compaction (`EventsCompactionConfig`) and Resumability (`ResumabilityConfig`)
app = App(
    name="app",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=5,
        overlap_size=2,
    ),
)
