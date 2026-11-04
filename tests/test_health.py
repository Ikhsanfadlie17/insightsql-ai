"""Task 1: health endpoint, secret handling, and error response shape."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.core.config import Settings
from backend.core.errors import NotFoundError
from backend.main import create_app


def test_health_reports_llm_not_configured(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "llm_configured": False, "model": None}


def test_health_reports_configured_model(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"openai_api_key": SecretStr("sk-test"), "openai_model": "model-from-env"}
    )
    with TestClient(create_app(configured)) as client:
        body = client.get("/health").json()

    assert body == {"status": "ok", "llm_configured": True, "model": "model-from-env"}


def test_llm_not_configured_without_model(settings: Settings) -> None:
    key_only = settings.model_copy(update={"openai_api_key": SecretStr("sk-test")})

    assert key_only.llm_configured is False


def test_health_never_returns_api_key(settings: Settings) -> None:
    secret = "sk-test-secret-value"
    configured = settings.model_copy(
        update={"openai_api_key": SecretStr(secret), "openai_model": "m"}
    )
    with TestClient(create_app(configured)) as client:
        response = client.get("/health")

    assert secret not in response.text


def test_settings_repr_hides_api_key(settings: Settings) -> None:
    configured = settings.model_copy(update={"openai_api_key": SecretStr("sk-hidden")})

    assert "sk-hidden" not in repr(configured)


def test_app_error_shape(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/boom")
    def boom() -> None:
        raise NotFoundError("Dataset 'x' was not found.")

    with TestClient(app) as client:
        response = client.get("/boom")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Dataset 'x' was not found."}
    }


def test_unexpected_error_is_generic(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/crash")
    def crash() -> None:
        raise RuntimeError("/secret/path/leak")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/crash")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "/secret/path" not in response.text
