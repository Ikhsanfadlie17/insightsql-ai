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


def test_app_wires_the_text_to_sql_pipeline(client: TestClient) -> None:
    from backend.services.sql_generator import TextToSQLService

    assert isinstance(client.app.state.text_to_sql, TextToSQLService)
    assert client.app.state.llm_gateway.configured is False  # no key in tests


# ------------------------------------------- Task 21 (Phase 2 review L5): one error envelope
def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/no-such-route")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_wrong_method_uses_error_envelope(client: TestClient) -> None:
    response = client.put("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_request_validation_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/usage/requests", params={"limit": 0})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "limit" in error["message"]
    assert error["details"][0]["field"] == "query.limit"


def test_validation_error_never_echoes_the_input(client: TestClient) -> None:
    """Pydantic errors carry the rejected value; it can be long or sensitive."""
    secret_like = "x" * 6000 + "SECRET-INPUT"
    response = client.post("/analyses/ask", json={"dataset_id": "d", "question": secret_like})

    assert response.status_code == 422
    assert "SECRET-INPUT" not in response.text
    assert set(response.json()) == {"error"}


def test_malformed_json_uses_error_envelope(client: TestClient) -> None:
    response = client.post(
        "/analyses/ask", content=b"{not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --------------------------------------- Task 21 (Phase 2 review M4): periodic upload cleanup
def test_stale_uploads_are_removed_while_running(settings: Settings) -> None:
    import os
    import time

    fast = settings.model_copy(update={"upload_cleanup_interval_minutes": 0.002})  # ~0.1 s
    with TestClient(create_app(fast)):
        stale_dir = fast.uploads_dir / ("0" * 32)
        stale_dir.mkdir(parents=True)
        old = time.time() - 48 * 3600
        os.utime(stale_dir, (old, old))
        fresh_dir = fast.uploads_dir / ("1" * 32)
        fresh_dir.mkdir()

        deadline = time.monotonic() + 5
        while stale_dir.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

        assert not stale_dir.exists()
        assert fresh_dir.exists()


def test_cleanup_failure_does_not_stop_the_loop(settings: Settings) -> None:
    import time

    fast = settings.model_copy(update={"upload_cleanup_interval_minutes": 0.002})
    app = create_app(fast)
    calls = {"n": 0}

    def flaky(max_age_hours: float = 24) -> int:
        calls["n"] += 1
        raise OSError("disk hiccup")

    app.state.dataset_service.cleanup_stale_uploads = flaky
    with TestClient(app):
        deadline = time.monotonic() + 5
        while calls["n"] < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert calls["n"] >= 3
        assert TestClient(app).get("/health").status_code == 200
