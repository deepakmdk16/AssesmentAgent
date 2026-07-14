import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assessment_agent import api
from assessment_agent.api import app

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sum_of_n.json"
QUESTION = json.loads(EXAMPLE.read_text())

CORRECT_PY = "import sys\nd = sys.stdin.read().split()\nn = int(d[0])\nprint(sum(int(x) for x in d[1 : 1 + n]))\n"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # Never hit the paid judge in tests.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def client() -> TestClient:
    # TestClient runs BackgroundTasks synchronously before returning the response,
    # so a job is already "done" by the time POST returns.
    return TestClient(app)


def _job(candidate="Jane Doe", **extra) -> dict:
    return {
        "question": QUESTION,
        "code": CORRECT_PY,
        "language": "python",
        "candidate": candidate,
        **extra,
    }


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_accepts_job_and_completes(client):
    resp = client.post("/assessments", json=_job())
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert resp.json()["status"] == "accepted"

    got = client.get(f"/assessments/{job_id}").json()
    assert got["status"] == "done"
    result = got["result"]
    assert result["verdict"] == "PASS"
    assert result["score_pct"] == 100.0
    assert result["candidate"] == "Jane Doe"
    assert result["job_id"] == job_id


def test_callback_receives_result(client, monkeypatch):
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(api, "_post_callback", lambda url, payload: sent.append((url, payload)))

    resp = client.post("/assessments", json=_job(callback_url="https://platform/cb"))
    assert resp.status_code == 202
    assert len(sent) == 1
    url, payload = sent[0]
    assert url == "https://platform/cb"
    assert payload["verdict"] == "PASS"
    assert payload["job_id"] == resp.json()["job_id"]


def test_unsupported_language_is_400(client):
    resp = client.post("/assessments", json=_job() | {"language": "cobol"})
    assert resp.status_code == 400


def test_malformed_question_is_400(client):
    resp = client.post("/assessments", json=_job() | {"question": {"title": "oops"}})
    assert resp.status_code == 400
    assert "invalid question" in resp.json()["detail"]


def test_unknown_job_is_404(client):
    assert client.get("/assessments/deadbeef").status_code == 404


def test_email_without_credentials_reports_error_but_still_assesses(client, monkeypatch):
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    resp = client.post("/assessments", json=_job(email_to="interviewer@example.com"))
    job_id = resp.json()["job_id"]

    result = client.get(f"/assessments/{job_id}").json()["result"]
    assert result["verdict"] == "PASS"  # assessment still succeeds
    assert result["email"]["emailed"] is False
    assert result["email"]["error"]  # a clear reason (missing SMTP creds)
