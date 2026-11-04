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
    ) -> dict[str, Any]:
        """``POST /datasets``; ingestion continues in the background."""
        payload = {
            "upload_id": upload_id,
            "sheet": sheet,
            "name": name,
            "number_format": number_format,
            "date_order": date_order,
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
