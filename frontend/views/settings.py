"""Settings: AI configuration, limits, usage and cost, and how to change them (no secrets)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from frontend.api_client import ApiClient, ApiError
from frontend.components.formatting import NA, fmt_int, fmt_number
from frontend.components.layout import empty_state, page_header, show_api_error
from frontend.state import get_client

ENV_EXAMPLE = """\
# .env in the project folder (next to backend/). Never commit this file.
OPENAI_API_KEY=sk-...
OPENAI_MODEL=<a model ID your account can use>
# optional
OPENAI_INSIGHT_MODEL=
MAX_RESULT_ROWS=5000
SEND_SAMPLE_VALUES=true
ENABLE_LLM_INSIGHTS=true"""


def _seconds(value: float) -> str:
    return f"{fmt_number(value, 0 if float(value).is_integer() else 1)} s"


def _on_off(value: bool) -> str:
    return ":green[On]" if value else ":gray[Off]"


def _rows_table(rows: list[tuple[str, str, str]]) -> None:
    """Markdown table: setting, current value, environment variable."""
    lines = ["| Setting | Value | Environment variable |", "|---|---|---|"]
    lines += [f"| {name} | {value} | `{env}` |" for name, value, env in rows]
    st.markdown("\n".join(lines))


def _ai_section(llm: dict[str, Any]) -> None:
    with st.container(border=True):
        st.subheader("AI model")
        if llm["configured"]:
            st.markdown(":green[:material/check_circle: **Configured**]")
        else:
            st.markdown(":orange[:material/key_off: **Not configured**] — AI Analyst is off.")
            missing = [
                name
                for name, present in (
                    ("OPENAI_API_KEY", llm["api_key_set"]),
                    ("OPENAI_MODEL", bool(llm["model"])),
                )
                if not present
            ]
            st.caption(
                f"Missing: {', '.join(missing)}. Client Analytics, Data Sources, and exports "
                "work without them."
            )
        key_state = "Set (hidden)" if llm["api_key_set"] else ":orange[Not set]"
        price = "Yes" if llm["model_has_price"] else "No — costs show N/A"
        _rows_table(
            [
                ("API key", key_state, "OPENAI_API_KEY"),
                ("SQL model", f"`{llm['model']}`" if llm["model"] else NA, "OPENAI_MODEL"),
                (
                    "Insight model",
                    f"`{llm['insight_model']}`" if llm["insight_model"] else NA,
                    "OPENAI_INSIGHT_MODEL",
                ),
                ("Price in pricing file", price, "PRICING_FILE"),
                ("Request timeout", _seconds(llm["timeout_seconds"]), "LLM_TIMEOUT_SECONDS"),
                (
                    "Total timeout (with retries)",
                    _seconds(llm["total_timeout_seconds"]),
                    "LLM_TOTAL_TIMEOUT_SECONDS",
                ),
                ("Retries", fmt_int(llm["max_retries"]), "LLM_MAX_RETRIES"),
                ("Max output tokens", fmt_int(llm["max_output_tokens"]), "LLM_MAX_OUTPUT_TOKENS"),
            ]
        )


def _limits_section(limits: dict[str, Any], toggles: dict[str, Any]) -> None:
    with st.container(border=True):
        st.subheader("Limits and privacy")
        upload = limits["max_upload_mb"]
        _rows_table(
            [
                ("Max upload size", f"{fmt_number(upload, 0)} MB", "MAX_UPLOAD_MB"),
                ("Query timeout", _seconds(limits["query_timeout_seconds"]),
                 "QUERY_TIMEOUT_SECONDS"),
                ("Max result rows", fmt_int(limits["max_result_rows"]), "MAX_RESULT_ROWS"),
                ("Max question length", f"{fmt_int(limits['max_question_chars'])} characters",
                 "MAX_QUESTION_CHARS"),
                ("Automatic SQL repairs", fmt_int(limits["max_sql_repairs"]), "MAX_SQL_REPAIRS"),
                ("Send sample values to the AI", _on_off(toggles["send_sample_values"]),
                 "SEND_SAMPLE_VALUES"),
                ("AI-written insights", _on_off(toggles["enable_llm_insights"]),
                 "ENABLE_LLM_INSIGHTS"),
            ]
        )  # fmt: skip
        st.caption(
            "Sample values are never sent for client ID or client-name columns, and the full "
            "dataset is never sent to the AI. With AI-written insights off, summaries are "
            "built from the query results only."
        )


def _cost(totals: dict[str, Any]) -> str:
    if totals["requests"] and totals["requests_without_cost"] >= totals["requests"]:
        return NA
    return f"${totals['estimated_cost_usd']:,.4f}"


def _usage_table(rows: list[dict[str, Any]], key_label: str) -> None:
    st.dataframe(
        pd.DataFrame(
            {
                key_label: [r["key"] for r in rows],
                "Requests": [r["requests"] for r in rows],
                "Failed": [r["failed"] for r in rows],
                "Input tokens": [r["input_tokens"] for r in rows],
                "Output tokens": [r["output_tokens"] for r in rows],
                "Est. cost (USD)": [
                    None if r["requests_without_cost"] >= r["requests"] else r["estimated_cost_usd"]
                    for r in rows
                ],
            }
        ),
        hide_index=True,
        column_config={"Est. cost (USD)": st.column_config.NumberColumn(format="$%.4f")},
    )


def _usage_section(api: ApiClient) -> None:
    with st.container(border=True):
        st.subheader("AI usage and cost")
        try:
            usage = api.usage_summary()
        except ApiError as error:
            show_api_error(error)
            return
        totals = usage["totals"]
        if not totals["requests"]:
            empty_state("No AI requests yet. Usage appears here after questions in AI Analyst.")
            return
        cols = st.columns(4)
        cols[0].metric("AI requests", fmt_int(totals["requests"]))
        cols[1].metric("Failed", fmt_int(totals["failed"]))
        cols[2].metric(
            "Tokens (in / out)",
            f"{fmt_int(totals['input_tokens'])} / {fmt_int(totals['output_tokens'])}",
        )
        cols[3].metric("Estimated cost", _cost(totals))
        missing = totals["requests_without_cost"]
        if missing:
            st.caption(
                f"{fmt_int(missing)} of {fmt_int(totals['requests'])} requests have no cost "
                "estimate (no price for the model in the pricing file, or no token usage "
                "reported). The estimate covers the rest only."
            )
        else:
            st.caption("Costs are estimates from the prices in the pricing file, not invoices.")
        by_model, by_purpose = st.tabs(["By model", "By purpose"])
        with by_model:
            _usage_table(usage["by_model"], "Model")
        with by_purpose:
            _usage_table(usage["by_purpose"], "Purpose")


def _how_to_section() -> None:
    with st.expander("How to change these settings", icon=":material/tune:"):
        st.markdown(
            "Settings come from environment variables, usually a `.env` file next to the "
            "`backend` folder (copy `.env.example`). **Restart the backend** after a change; "
            "the values are read when it starts. The API key stays on the backend and is "
            "never sent to this page."
        )
        st.code(ENV_EXAMPLE, language="bash")
        st.caption(
            "Model prices for cost estimates go in `config/pricing.json` "
            "(USD per 1 million tokens)."
        )


def main() -> None:
    """Render the page."""
    page_header("Settings", "AI configuration, limits, and usage. Secrets are never shown here.")
    api = get_client()
    try:
        config = api.settings()
    except ApiError as error:
        show_api_error(error)
        return
    st.markdown("**Backend** :green[:material/check_circle: Connected]")
    _ai_section(config["llm"])
    _usage_section(api)
    _limits_section(config["limits"], config["toggles"])
    _how_to_section()


main()
