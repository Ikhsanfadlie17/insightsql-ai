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
