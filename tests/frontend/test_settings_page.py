"""Task 21: Settings page (configuration, usage, how to change it) and dataset management."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.frontend.fakes import FAILED, READY, AnalystFakeApi, FakeApi, usage_totals

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
OTHER = {**READY, "id": "d3", "name": "Data lain"}


def _page(api: FakeApi, name: str = "settings", **state: object) -> AppTest:
    at = AppTest.from_file(str(FRONTEND / "views" / f"{name}.py"), default_timeout=20)
    at.session_state["api_client"] = api
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def _text(at: AppTest) -> str:
    parts = [*at.markdown, *at.caption, *at.info, *at.warning, *at.success, *at.error, *at.code]
    return "\n".join(str(p.value).replace("\\", "") for p in parts)


# ------------------------------------------------------------------ settings
def test_settings_without_llm_explains_how_to_configure() -> None:
    at = _page(FakeApi())
    text = _text(at)

    assert not at.exception
    assert "Not configured" in text
    assert "OPENAI_API_KEY" in text and "OPENAI_MODEL" in text
    assert "restart" in text.casefold()


def test_settings_shows_model_limits_and_toggles() -> None:
    at = _page(AnalystFakeApi())
    text = _text(at)

    assert "`m`" in text or "m" in [str(c.value) for c in at.code]
    assert "5.000" in text  # max result rows, Indonesian format
    assert "MAX_RESULT_ROWS" in text
    assert "SEND_SAMPLE_VALUES" in text


def test_settings_with_no_usage_shows_no_fake_numbers() -> None:
    at = _page(AnalystFakeApi())

    assert len(at.metric) == 0
    assert any("No AI requests yet" in str(i.value) for i in at.info)


def test_settings_shows_usage_totals_and_cost_estimate() -> None:
    api = AnalystFakeApi()
    api.usage = {
        "currency": "USD",
        "totals": usage_totals(
            requests=3,
            succeeded=2,
            failed=1,
            input_tokens=1200,
            output_tokens=300,
            estimated_cost_usd=0.0123,
        ),
        "by_model": [usage_totals("m", requests=3, estimated_cost_usd=0.0123)],
        "by_purpose": [usage_totals("sql_generation", requests=3)],
    }
    at = _page(api)
    metrics = {m.label: m.value for m in at.metric}

    assert metrics["AI requests"] == "3"
    assert metrics["Failed"] == "1"
    assert metrics["Tokens (in / out)"] == "1.200 / 300"
    assert metrics["Estimated cost"] == "$0.0123"
    assert len(at.dataframe) >= 1


def test_cost_without_prices_is_not_shown_as_zero() -> None:
    api = AnalystFakeApi()
    api.usage["totals"] = usage_totals(requests=2, succeeded=2, requests_without_cost=2)
    at = _page(api)
    metrics = {m.label: m.value for m in at.metric}

    assert metrics["Estimated cost"] == "N/A"
    assert "pricing" in _text(at).casefold()


def test_partial_cost_is_flagged() -> None:
    api = AnalystFakeApi()
    api.usage["totals"] = usage_totals(requests=3, estimated_cost_usd=0.5,
                                       requests_without_cost=1)  # fmt: skip
    at = _page(api)

    assert "1 of 3" in _text(at)


def test_settings_survives_usage_failure() -> None:
    api = AnalystFakeApi()
    api.failing = {"usage_summary"}
    at = _page(api)

    assert not at.exception
    assert "usage_summary failed" in _text(at)
    assert "MAX_RESULT_ROWS" in _text(at)  # the rest of the page still renders


# --------------------------------------------------------- dataset management
def test_rename_dataset() -> None:
    api = FakeApi(datasets=[dict(READY)])
    at = _page(api, "data_sources")

    at.text_input(key="ds_rename_d1").set_value("Layanan 2026").run()
    at.button(key="ds_rename_save_d1").click().run()

    assert api.rename_calls == [("d1", "Layanan 2026")]
    assert "Dataset renamed" in _text(at)


def test_rename_rejects_blank_name_without_calling_backend() -> None:
    api = FakeApi(datasets=[dict(READY)])
    at = _page(api, "data_sources")

    at.text_input(key="ds_rename_d1").set_value("   ").run()
    at.button(key="ds_rename_save_d1").click().run()

    assert api.rename_calls == []
    assert "cannot be empty" in _text(at)


def test_delete_dataset_needs_confirmation() -> None:
    api = FakeApi(datasets=[dict(READY), dict(OTHER)])
    at = _page(api, "data_sources", active_dataset_id="d1")

    at.button(key="ds_delete_d1").click().run()
    assert api.delete_dataset_calls == []
    assert "cannot be undone" in _text(at)

    at.button(key="ds_delete_cancel").click().run()
    assert api.delete_dataset_calls == []
    assert "cannot be undone" not in _text(at)

    at.button(key="ds_delete_d1").click().run()
    at.button(key="ds_delete_confirm").click().run()

    assert api.delete_dataset_calls == ["d1"]
    assert "deleted" in _text(at)
    assert at.session_state["active_dataset_id"] == "d3"  # falls back to a remaining dataset


def test_delete_last_dataset_shows_empty_state() -> None:
    api = FakeApi(datasets=[dict(READY)])
    at = _page(api, "data_sources")

    at.button(key="ds_delete_d1").click().run()
    at.button(key="ds_delete_confirm").click().run()

    assert not at.exception
    assert any("No datasets yet" in str(i.value) for i in at.info)
    assert at.session_state["active_dataset_id"] is None


def test_failed_dataset_can_be_deleted() -> None:
    api = FakeApi(datasets=[dict(READY), dict(FAILED)])
    at = _page(api, "data_sources")

    inspect = next(s for s in at.selectbox if s.label == "Inspect dataset")
    inspect.set_value("d2").run()
    at.button(key="ds_delete_d2").click().run()
    at.button(key="ds_delete_confirm").click().run()

    assert api.delete_dataset_calls == ["d2"]


def test_delete_failure_is_shown() -> None:
    api = FakeApi(datasets=[dict(READY)])
    api.failing = {"delete_dataset"}
    at = _page(api, "data_sources")

    at.button(key="ds_delete_d1").click().run()
    at.button(key="ds_delete_confirm").click().run()

    assert not at.exception
    assert "delete_dataset failed" in _text(at)


def test_delete_confirmation_does_not_carry_over_to_another_dataset() -> None:
    api = FakeApi(datasets=[dict(READY), dict(OTHER)])
    at = _page(api, "data_sources", active_dataset_id="d1")

    at.button(key="ds_delete_d1").click().run()
    inspect = next(s for s in at.selectbox if s.label == "Inspect dataset")
    inspect.set_value("d3").run()

    assert "cannot be undone" not in _text(at)


# ------------------------------------------------- UI polish (Phase 5 review L8)
def test_profile_table_uses_indonesian_number_format() -> None:
    import copy

    from tests.frontend.fakes import PROFILE

    class NumericProfileApi(FakeApi):
        def profile(self, dataset_id: str) -> dict:
            data = copy.deepcopy(PROFILE)
            data["columns"][0] |= {"logical_type": "integer", "min": 4000000.0,
                                   "max": 189100000.0, "mean": 38247512.5,
                                   "median": 29500000.0, "null_count": 1234,
                                   "null_pct": 12.5}  # fmt: skip
            data["missing_pct"] = 0.6
            return data

    at = _page(NumericProfileApi(datasets=[dict(READY)]), "data_sources")
    table = at.dataframe[1].value  # [0] is the datasets table
    row = table.iloc[0]

    assert row["Min"] == "4.000.000"
    assert row["Mean"] == "38.247.512,50"
    assert row["Missing"] == "1.234 (12,5%)"
    assert {m.label: m.value for m in at.metric}["Missing cells"] == "0,6%"


def test_dataset_detail_explains_how_the_table_was_read() -> None:
    layout = {
        "header_row": 4,
        "header_chosen": False,
        "ignored_columns": ["Daftar hari libur"],
        "template_rows_skipped": 320,
    }
    api = FakeApi(datasets=[{**READY, "layout": layout}])

    at = _page(api, "data_sources")

    assert "Header row 4 (detected)" in _text(at) and "320" in _text(at)


# --- real-data feedback (2026-11-05): all columns are read; roles are optional -----------
def test_dataset_detail_says_how_many_columns_were_read() -> None:
    at = _page(FakeApi(datasets=[dict(READY)]), "data_sources")
    text = _text(at)

    assert "2 columns read" in text and "1 date" in text and "1 text" in text
    assert "AI Analyst" in text  # every column can be used there


def test_column_roles_are_marked_optional_and_explained() -> None:
    at = _page(FakeApi(datasets=[dict(READY)]), "data_sources")

    labels = [t.label for t in at.tabs]
    assert "Column mapping (optional)" in labels and "Columns (2)" in labels
    text = _text(at)
    assert "Not used" in text and "Client Analytics" in text


def test_ai_analyst_does_not_push_the_mapping() -> None:
    at = _page(AnalystFakeApi(mapped=False), "ai_analyst")

    assert "may need clarification" not in _text(at)
    assert "optional" in _text(at).lower()
