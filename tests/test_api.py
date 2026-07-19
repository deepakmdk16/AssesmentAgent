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
    # Never hit the paid judge in tests; start with auth disabled unless a test opts in.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ASSESS_API_TOKEN", raising=False)
    monkeypatch.delenv("CALLBACK_TOKEN", raising=False)


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


class _FakeResponse:
    """Minimal stand-in for an httpx response. `_post_callback` inspects
    `status_code` to decide whether to retry, so a stub must carry one."""

    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_run_endpoint_is_rate_limited(client, monkeypatch):
    # Auth gates who; this caps how hard one caller can hammer code execution.
    monkeypatch.setitem(api._RATE_LIMITS, "run", 2)
    payload = {"code": "print(1)", "language": "python", "stdin": ""}
    assert client.post("/run", json=payload).status_code == 200
    assert client.post("/run", json=payload).status_code == 200
    over = client.post("/run", json=payload)
    assert over.status_code == 429
    assert "too many requests" in over.json()["detail"].lower()


def test_run_and_run_tests_share_the_run_bucket(client, monkeypatch):
    # Both are the same untrusted-execution surface, so they count together.
    monkeypatch.setitem(api._RATE_LIMITS, "run", 1)
    assert client.post("/run", json={"code": "print(1)", "language": "python"}).status_code == 200
    over = client.post(
        "/run/tests",
        json={"code": "print(1)", "language": "python", "question": QUESTION},
    )
    assert over.status_code == 429


def test_rate_limit_zero_disables_the_bucket(client, monkeypatch):
    monkeypatch.setitem(api._RATE_LIMITS, "run", 0)
    payload = {"code": "print(1)", "language": "python", "stdin": ""}
    for _ in range(3):
        assert client.post("/run", json=payload).status_code == 200


def test_auth_rejects_missing_or_wrong_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("ASSESS_API_TOKEN", "s3cret")
    assert client.post("/assessments", json=_job()).status_code == 401
    assert (
        client.post("/assessments", json=_job(), headers={"X-Assess-Token": "nope"}).status_code
        == 401
    )
    ok = client.post("/assessments", json=_job(), headers={"X-Assess-Token": "s3cret"})
    assert ok.status_code == 202


def test_auth_is_fail_closed_when_unconfigured(client, monkeypatch):
    """Forgetting ASSESS_API_TOKEN must not silently publish an endpoint that
    executes submitted code. Unset = 503, not "auth off"."""
    monkeypatch.delenv("ASSESS_API_TOKEN", raising=False)
    monkeypatch.delenv("ASSESS_AUTH_DISABLED", raising=False)
    posts = ["/assessments", "/run", "/run/tests", "/questions/draft"]
    responses = [(p, client.post(p, json={})) for p in posts]
    responses.append(("/assessments/{id}", client.get("/assessments/whatever")))

    for path, resp in responses:
        assert resp.status_code == 503, f"{path} was not fail-closed"
        assert "ASSESS_API_TOKEN" in resp.json()["detail"]


def test_health_needs_no_auth(client, monkeypatch):
    # Liveness must work before the operator has configured anything.
    monkeypatch.delenv("ASSESS_API_TOKEN", raising=False)
    monkeypatch.delenv("ASSESS_AUTH_DISABLED", raising=False)
    assert client.get("/health").status_code == 200


def test_polling_a_job_requires_the_token(client, monkeypatch):
    """GET /assessments/{id} returns each case's input/expected/actual — the
    answer key. An unguessable job_id is not an access control."""
    monkeypatch.setenv("ASSESS_API_TOKEN", "s3cret")
    auth = {"X-Assess-Token": "s3cret"}
    job_id = client.post("/assessments", json=_job(), headers=auth).json()["job_id"]

    assert client.get(f"/assessments/{job_id}").status_code == 401
    assert client.get(f"/assessments/{job_id}", headers=auth).status_code == 200


def test_oversized_code_is_rejected(client):
    huge = "x = 1\n" * 200_000
    assert client.post("/assessments", json=_job() | {"code": huge}).status_code == 422
    assert client.post("/run", json={"code": huge, "language": "python"}).status_code == 422


def test_callback_retries_then_gives_up(client, monkeypatch):
    """The callback is the only durable delivery path, so a transient 5xx must be
    retried rather than silently dropped."""
    monkeypatch.setattr(api, "_CALLBACK_ATTEMPTS", 3)
    monkeypatch.setattr(api, "_CALLBACK_BACKOFF_S", 0.0)  # don't actually sleep
    calls: list[str] = []

    def _post(url, **kw):
        calls.append(url)
        return _FakeResponse(503, "upstream down")

    monkeypatch.setattr(api.httpx, "post", _post)
    resp = client.post("/assessments", json=_job(callback_url="https://platform/cb"))
    assert resp.status_code == 202
    assert len(calls) == 3  # retried to the cap, then gave up


def test_callback_stops_retrying_on_4xx(client, monkeypatch):
    """A 4xx is the platform rejecting these bytes; resending them is pointless."""
    monkeypatch.setattr(api, "_CALLBACK_ATTEMPTS", 3)
    monkeypatch.setattr(api, "_CALLBACK_BACKOFF_S", 0.0)
    calls: list[str] = []

    def _post(url, **kw):
        calls.append(url)
        return _FakeResponse(400, "bad payload")

    monkeypatch.setattr(api.httpx, "post", _post)
    client.post("/assessments", json=_job(callback_url="https://platform/cb"))
    assert len(calls) == 1


def test_callback_succeeds_after_a_transient_failure(client, monkeypatch):
    monkeypatch.setattr(api, "_CALLBACK_ATTEMPTS", 3)
    monkeypatch.setattr(api, "_CALLBACK_BACKOFF_S", 0.0)
    calls: list[str] = []

    def _post(url, **kw):
        calls.append(url)
        return _FakeResponse(500) if len(calls) == 1 else _FakeResponse(200)

    monkeypatch.setattr(api.httpx, "post", _post)
    client.post("/assessments", json=_job(callback_url="https://platform/cb"))
    assert len(calls) == 2  # failed once, then delivered — no further attempts


def test_callback_carries_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("CALLBACK_TOKEN", "cbtok")
    captured: dict = {}

    def _post(url, **kw):
        captured.update(kw)
        return _FakeResponse()

    monkeypatch.setattr(api.httpx, "post", _post)
    client.post("/assessments", json=_job(callback_url="https://platform/cb"))
    assert captured["headers"].get("X-Assess-Token") == "cbtok"


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
    monkeypatch.setattr(
        api, "_post_callback", lambda job_id, url, payload: sent.append((url, payload))
    )

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


def test_jobs_map_is_bounded(client, monkeypatch):
    # The polling map is transient run-state, not a datastore: it must not grow
    # without bound. Once over the cap, the oldest job is evicted (FIFO).
    monkeypatch.setattr(api, "_MAX_JOBS", 3)
    api._JOBS.clear()
    ids = [client.post("/assessments", json=_job()).json()["job_id"] for _ in range(5)]
    assert len(api._JOBS) <= 3
    assert client.get(f"/assessments/{ids[0]}").status_code == 404  # oldest evicted
    assert client.get(f"/assessments/{ids[-1]}").status_code == 200  # newest retained


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1/cb",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://10.0.0.5/cb",
        "http://localhost/cb",
        "file:///etc/passwd",
        "ftp://example.com/cb",
    ],
)
def test_callback_url_ssrf_is_rejected(client, bad_url):
    assert client.post("/assessments", json=_job(callback_url=bad_url)).status_code == 400


def test_public_callback_url_is_accepted(client, monkeypatch):
    # A public IP (or a plain hostname) is fine — only internal targets are blocked.
    # Stub the outbound POST so the accepted job doesn't hit the real network.
    monkeypatch.setattr(api.httpx, "post", lambda *a, **k: _FakeResponse())
    r = client.post("/assessments", json=_job(callback_url="http://93.184.216.34/cb"))
    assert r.status_code == 202


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
