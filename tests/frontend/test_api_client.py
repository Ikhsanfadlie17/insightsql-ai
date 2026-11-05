"""Task 6: HTTP client used by the Streamlit frontend."""

from __future__ import annotations

import json

import httpx
import pytest

from frontend.api_client import ApiClient, ApiError


def _client(handler) -> ApiClient:
    return ApiClient("http://backend", transport=httpx.MockTransport(handler))


def test_returns_parsed_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "llm_configured": False, "model": None})

    assert _client(handler).health()["status"] == "ok"


def test_backend_error_becomes_api_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "Nope."}})

    with pytest.raises(ApiError) as caught:
        _client(handler).get_dataset("x")

    assert (caught.value.code, caught.value.message, caught.value.status) == (
        "not_found",
        "Nope.",
        404,
    )


def test_validation_error_without_error_envelope() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": [{"msg": "too big"}]})

    with pytest.raises(ApiError) as caught:
        _client(handler).preview("x", limit=999)

    assert caught.value.code == "validation_error"
    assert caught.value.status == 422


def test_unreachable_backend() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ApiError) as caught:
        _client(handler).list_datasets()

    assert caught.value.code == "backend_unreachable"


def test_upload_sends_multipart_file() -> None:
    seen: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(200, json={"upload_id": "u", "sheets": []})

    _client(handler).inspect_upload("data.csv", b"a,b\n1,2\n")

    assert b'filename="data.csv"' in seen["body"]
    assert b"a,b\n1,2\n" in seen["body"]


def test_create_dataset_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "upload_id": "u1",
            "sheet": "S",
            "name": None,
            "number_format": "auto",
            "date_order": "auto",
            "text_columns": [],
            "header_row": None,  # detect (smart import)
            "skip_template_rows": True,
        }
        return httpx.Response(201, json={"id": "d1", "status": "processing"})

    assert _client(handler).create_dataset("u1", "S")["id"] == "d1"


def test_delete_returns_none_on_204() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(204)

    assert _client(handler).delete_dataset("d1") is None


def test_create_dataset_sends_text_columns() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["text_columns"] == ["Kode"]
        return httpx.Response(201, json={"id": "d1"})

    _client(handler).create_dataset("u1", text_columns=["Kode"])


def test_export_downloads_return_bytes() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.params.get("format")))
        if request.method == "POST":
            seen.append(json.loads(request.content))
        return httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"})

    api = _client(handler)
    period = {"label": "2025", "start": "2025-01-01", "end": "2026-01-01"}

    assert api.export_analysis("a1", "pdf") == b"%PDF-1.4"
    assert api.export_client_analytics("d1", period, period, True, "xlsx") == b"%PDF-1.4"
    assert seen[0] == ("GET", "/analyses/a1/export", "pdf")
    assert seen[1] == ("POST", "/datasets/d1/client-analytics/export", "xlsx")
    assert seen[2]["options"] == {"dedupe_exact_duplicates": True}


def test_export_error_is_an_api_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = {"error": {"code": "nothing_to_export", "message": "No table."}}
        return httpx.Response(409, json=body)

    with pytest.raises(ApiError) as caught:
        _client(handler).export_analysis("a1", "csv")

    assert caught.value.code == "nothing_to_export"


def test_create_dataset_sends_the_layout_choice() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["header_row"] == 4 and body["skip_template_rows"] is False
        return httpx.Response(200, json={"id": "d1"})

    _client(handler).create_dataset("u1", header_row=4, skip_template_rows=False)


def test_sheet_layout_posts_the_header_row() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/datasets/uploads/u1/layout"
        assert json.loads(request.content) == {"sheet": "S1", "header_row": 3}
        return httpx.Response(200, json={"name": "S1", "row_count": 2, "header_row": 3})

    assert _client(handler).sheet_layout("u1", "S1", 3)["header_row"] == 3
