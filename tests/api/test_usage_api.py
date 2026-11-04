"""Task 11: usage endpoints make token use and cost traceable per request."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.services.llm.client import TokenUsage
from tests.llm_fakes import FakeLLMClient


class Out(BaseModel):
    answer: str


def test_empty_usage(client: TestClient) -> None:
    summary = client.get("/usage/summary").json()

    assert summary["totals"]["requests"] == 0
    assert summary["totals"]["estimated_cost_usd"] == 0
    assert client.get("/usage/requests").json() == []


def test_usage_after_requests(client: TestClient) -> None:
    gateway = client.app.state.llm_gateway
    gateway._client = FakeLLMClient(  # noqa: SLF001 - inject the fake into the running app
        responses=[Out(answer="a")], usage=TokenUsage(input_tokens=10, output_tokens=5)
    )

    gateway.complete(purpose="sql_generation", system="s", user="u", schema=Out, dataset_id="d1")
    requests = client.get("/usage/requests", params={"limit": 5}).json()
    summary = client.get("/usage/summary").json()

    assert len(requests) == 1
    assert requests[0]["purpose"] == "sql_generation"
    assert requests[0]["cost_status"] in {"estimated", "unavailable_no_price"}
    assert summary["totals"]["requests"] == 1
    assert summary["currency"] == "USD"


def test_usage_limit_is_bounded(client: TestClient) -> None:
    assert client.get("/usage/requests", params={"limit": 10_000}).status_code == 422
