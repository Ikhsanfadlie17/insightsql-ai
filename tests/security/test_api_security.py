"""Task 24: API-level security checks.

- Upload names cannot escape the upload folder; oversized and disguised files are refused.
- The API key never appears in any endpoint's response or in the logs, on success and on
  error paths alike (every route in the OpenAPI schema is called).
"""

from __future__ import annotations

import logging
import re

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.core.config import Settings
from backend.core.errors import LLMError
from backend.main import create_app
from backend.schemas.llm_outputs import SQLPlanOutput
from tests.api.test_mapping_api import FULL
from tests.conftest import SAMPLE_DATA_DIR
from tests.llm_fakes import FakeLLMClient

SECRET = "sk-proj-SECRETVALUE-0123456789abcdefABCDEF"
CSV = (SAMPLE_DATA_DIR / "client_services.csv").read_bytes()


# ------------------------------------------------------------------------------ uploads
@pytest.mark.parametrize(
    "filename",
    [
        "../../evil.csv",
        "..\\..\\evil.csv",
        "/etc/passwd.csv",
        "C:\\Windows\\System32\\evil.csv",
        "a/../../b.csv",
        "....//....//evil.csv",
        "%2e%2e%2fevil.csv",
    ],
)
def test_upload_names_cannot_escape_the_upload_folder(settings: Settings, filename: str) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post("/datasets/inspect", files={"file": (filename, b"a,b\n1,2\n")})

    assert response.status_code == 200, response.text
    uploads = settings.uploads_dir.resolve()
    written = [p for p in settings.data_dir.rglob("*") if p.is_file() and p.suffix == ".csv"]
    assert len(written) == 1
    assert written[0].resolve().is_relative_to(uploads)
    assert written[0].parent.parent.resolve() == uploads  # uploads/<id>/<safe name>
    assert ".." not in response.json()["filename"] and "/" not in response.json()["filename"]


def test_oversized_upload_is_refused_and_not_kept(settings: Settings) -> None:
    small = settings.model_copy(update={"max_upload_mb": 0.01})  # ~10 KB
    with TestClient(create_app(small)) as client:
        response = client.post("/datasets/inspect", files={"file": ("big.csv", CSV)})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upload_error"
    assert not any(p.is_file() for p in small.uploads_dir.rglob("*"))


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("data.xlsx", b"ID,Nama\n1,a\n"),  # CSV text named .xlsx
        ("data.xlsx", b"PK\x03\x04 not really a workbook"),  # ZIP magic, broken content
        ("data.csv", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00"),  # a Windows executable
        ("data.exe", CSV),
        ("data.csv.exe", CSV),
        ("data", CSV),
    ],
)
def test_disguised_files_are_refused(settings: Settings, filename: str, content: bytes) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post("/datasets/inspect", files={"file": (filename, content)})

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "upload_error"
    assert not any(p.is_file() for p in settings.uploads_dir.rglob("*"))


# ---------------------------------------------------------------------- secret exposure
def _plan(**fields) -> SQLPlanOutput:
    base = dict(intent="sql", language="id", sql=None, retention=None, explanation="",
                chart=None, metrics=[], assumptions=[], clarification_question=None)  # fmt: skip
    return SQLPlanOutput(**{**base, **fields})


def _fill(path: str, ids: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), "nope"), path)


def test_api_key_never_appears_in_responses_or_logs(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """Every route, with real ids and with unknown ids, plus LLM failures that carry the
    key in their exception text."""
    leaky = LLMError(f"provider said: invalid key {SECRET}")
    fake = FakeLLMClient(
        responses=[
            _plan(sql="SELECT departemen, COUNT(*) AS n FROM dataset GROUP BY departemen"),
            leaky,  # the insight request fails with the key in the message
            leaky,  # a whole question fails
            RuntimeError(f"unexpected {SECRET}"),
        ]
    )
    configured = settings.model_copy(
        update={"openai_api_key": SecretStr(SECRET), "openai_model": "m", "log_level": "DEBUG"}
    )
    texts: list[str] = []
    with (
        caplog.at_level(logging.DEBUG),
        TestClient(
            create_app(configured, llm_client=fake), raise_server_exceptions=False
        ) as client,
    ):
        upload = client.post("/datasets/inspect", files={"file": ("c.csv", CSV)}).json()
        dataset_id = client.post("/datasets", json={"upload_id": upload["upload_id"]}).json()["id"]
        client.put(f"/datasets/{dataset_id}/column-mapping", json=FULL)
        answer = client.post(
            "/analyses/ask", json={"dataset_id": dataset_id, "question": "Per departemen?"}
        ).json()
        for question in ("Gagal?", "Gagal lagi?"):
            texts.append(
                client.post(
                    "/analyses/ask", json={"dataset_id": dataset_id, "question": question}
                ).text
            )
        ids = {
            "dataset_id": dataset_id,
            "analysis_id": answer["analysis_id"],
            "conversation_id": answer["conversation_id"],
        }
        paths = client.get("/openapi.json").json()["paths"]
        assert len(paths) >= 20  # the sweep really covers the API
        for path, methods in paths.items():
            for method in methods:
                if method == "delete":
                    continue  # deleting would remove what the other calls need
                for chosen in (ids, {}):  # real ids, then unknown ids (404 paths)
                    url = _fill(path, chosen)
                    response = client.request(
                        method.upper(), url, params={"format": "csv"}, json={}
                    )
                    texts.append(response.text)
                    texts.append(str(response.headers))
        texts.append(client.get("/settings").text)
        texts.append(client.get("/health").text)

    joined = "\n".join(texts) + "\n" + caplog.text
    for fragment in (SECRET, SECRET[-12:], "SECRETVALUE"):
        assert fragment not in joined
    assert answer["status"] == "answered" and answer["insight"]["source"] == "deterministic"
