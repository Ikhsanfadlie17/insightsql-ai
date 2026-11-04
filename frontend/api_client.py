"""Typed-ish HTTP client for the InsightSQL AI backend. Holds no secrets."""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class ApiError(Exception):
    """A backend error (or an unreachable backend) in a UI-friendly form."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ApiClient:
    """One method per backend endpoint used by the UI; returns parsed JSON."""

    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=DEFAULT_TIMEOUT, transport=transport)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(
                "backend_unreachable",
                "The InsightSQL AI backend is not reachable. Is it running?",
                0,
            ) from exc
        if response.status_code == 204:
            return None
        if response.is_error:
            raise self._to_error(response)
        return response.json()

    @staticmethod
    def _to_error(response: httpx.Response) -> ApiError:
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            return ApiError(
                error.get("code", "error"), error.get("message", ""), response.status_code
            )
        if response.status_code == 422:
            return ApiError("validation_error", "The request was invalid.", 422)
        return ApiError("error", f"Request failed ({response.status_code}).", response.status_code)

    # ---------------------------------------------------------------- endpoints
    def health(self) -> dict[str, Any]:
        """``GET /health``."""
        return self._request("GET", "/health")

    def inspect_upload(self, filename: str, content: bytes) -> dict[str, Any]:
        """``POST /datasets/inspect`` with a multipart file."""
        return self._request("POST", "/datasets/inspect", files={"file": (filename, content)})

    def create_dataset(
        self,
        upload_id: str,
        sheet: str | None = None,
        name: str | None = None,
        number_format: str = "auto",
        date_order: str = "auto",
        text_columns: list[str] | None = None,
    ) -> dict[str, Any]:
        """``POST /datasets``; ingestion continues in the background."""
        payload = {
            "upload_id": upload_id,
            "sheet": sheet,
            "name": name,
            "number_format": number_format,
            "date_order": date_order,
            "text_columns": text_columns or [],
        }
        return self._request("POST", "/datasets", json=payload)

    def list_datasets(self) -> list[dict[str, Any]]:
        """``GET /datasets``."""
        return self._request("GET", "/datasets")

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        """``GET /datasets/{id}``."""
        return self._request("GET", f"/datasets/{dataset_id}")

    def rename_dataset(self, dataset_id: str, name: str) -> dict[str, Any]:
        """``PATCH /datasets/{id}``."""
        return self._request("PATCH", f"/datasets/{dataset_id}", json={"name": name})

    def delete_dataset(self, dataset_id: str) -> None:
        """``DELETE /datasets/{id}``."""
        return self._request("DELETE", f"/datasets/{dataset_id}")

    def columns(self, dataset_id: str) -> list[dict[str, Any]]:
        """``GET /datasets/{id}/columns``."""
        return self._request("GET", f"/datasets/{dataset_id}/columns")

    def preview(self, dataset_id: str, limit: int = 50) -> dict[str, Any]:
        """``GET /datasets/{id}/preview``."""
        return self._request("GET", f"/datasets/{dataset_id}/preview", params={"limit": limit})

    def profile(self, dataset_id: str) -> dict[str, Any]:
        """``GET /datasets/{id}/profile``."""
        return self._request("GET", f"/datasets/{dataset_id}/profile")

    def column_mapping(self, dataset_id: str) -> dict[str, Any]:
        """``GET /datasets/{id}/column-mapping`` (suggestions + confirmed mapping)."""
        return self._request("GET", f"/datasets/{dataset_id}/column-mapping")

    def confirm_mapping(self, dataset_id: str, mapping: dict[str, Any]) -> dict[str, Any]:
        """``PUT /datasets/{id}/column-mapping``: the user's confirmed column roles."""
        return self._request("PUT", f"/datasets/{dataset_id}/column-mapping", json=mapping)

    # ------------------------------------------------------- client analytics
    def analytics_coverage(self, dataset_id: str) -> dict[str, Any]:
        """``GET /datasets/{id}/client-analytics/coverage``."""
        return self._request("GET", f"/datasets/{dataset_id}/client-analytics/coverage")

    def analytics_compare(
        self, dataset_id: str, previous: dict, current: dict, dedupe: bool = True
    ) -> dict[str, Any]:
        """``POST /datasets/{id}/client-analytics/compare`` (period ends are exclusive)."""
        body = {
            "previous": previous,
            "current": current,
            "options": {"dedupe_exact_duplicates": dedupe},
        }
        return self._request("POST", f"/datasets/{dataset_id}/client-analytics/compare", json=body)

    def analytics_clients(
        self,
        dataset_id: str,
        previous: dict,
        current: dict,
        category: str,
        offset: int = 0,
        limit: int = 100,
        dedupe: bool = True,
    ) -> dict[str, Any]:
        """``POST /datasets/{id}/client-analytics/clients``: one page of a category."""
        body = {
            "previous": previous,
            "current": current,
            "category": category,
            "offset": offset,
            "limit": limit,
            "options": {"dedupe_exact_duplicates": dedupe},
        }
        return self._request("POST", f"/datasets/{dataset_id}/client-analytics/clients", json=body)

    def analytics_trend(
        self, dataset_id: str, granularity: str = "month", dedupe: bool = True
    ) -> dict[str, Any]:
        """``GET /datasets/{id}/client-analytics/trend``."""
        return self._request(
            "GET",
            f"/datasets/{dataset_id}/client-analytics/trend",
            params={"granularity": granularity, "dedupe": str(dedupe).lower()},
        )

    def analytics_departments(
        self, dataset_id: str, previous: dict, current: dict, dedupe: bool = True
    ) -> dict[str, Any]:
        """``POST /datasets/{id}/client-analytics/departments``."""
        body = {
            "previous": previous,
            "current": current,
            "options": {"dedupe_exact_duplicates": dedupe},
        }
        return self._request(
            "POST", f"/datasets/{dataset_id}/client-analytics/departments", json=body
        )

    def analytics_identity(self, dataset_id: str) -> dict[str, Any]:
        """``GET /datasets/{id}/client-analytics/identity-issues``."""
        return self._request("GET", f"/datasets/{dataset_id}/client-analytics/identity-issues")

    # ------------------------------------------------------------- AI analyst
    def ask(self, dataset_id: str, question: str, conversation_id: str | None) -> dict[str, Any]:
        """``POST /analyses/ask``."""
        body = {"dataset_id": dataset_id, "conversation_id": conversation_id, "question": question}
        # A question can take several LLM calls plus query execution.
        return self._request("POST", "/analyses/ask", json=body, timeout=180.0)

    def list_conversations(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        """``GET /conversations?dataset_id=`` (all datasets when ``dataset_id`` is None)."""
        params = {"dataset_id": dataset_id} if dataset_id else None
        return self._request("GET", "/conversations", params=params)

    def recent_analyses(self, limit: int = 5) -> list[dict[str, Any]]:
        """``GET /analyses/recent``: newest questions across datasets."""
        return self._request("GET", "/analyses/recent", params={"limit": limit})

    def get_analysis(self, analysis_id: str) -> dict[str, Any]:
        """``GET /analyses/{id}``."""
        return self._request("GET", f"/analyses/{analysis_id}")

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        """``GET /conversations/{id}``."""
        return self._request("GET", f"/conversations/{conversation_id}")

    def delete_conversation(self, conversation_id: str) -> None:
        """``DELETE /conversations/{id}``."""
        return self._request("DELETE", f"/conversations/{conversation_id}")
