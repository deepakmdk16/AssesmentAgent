"""Tests for the non-grading execution endpoints (POST /run, POST /run/tests).

These back the candidate's editor: "Run" against their own input, and "Run
against test cases" as a rehearsal before submitting. Neither grades — no
verdict, no LLM judge, nothing stored.

They execute real code through the real runner (python only, so no extra
toolchain is needed) — that's the point: mocking execution here would test
nothing.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assessment_agent.api import app

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sum_of_n.json"
QUESTION = json.loads(EXAMPLE.read_text())

CORRECT_PY = "import sys\nd = sys.stdin.read().split()\nn = int(d[0])\nprint(sum(int(x) for x in d[1 : 1 + n]))\n"
# A constant no case expects, so every case fails (print(0) would accidentally
# pass any case whose sum happens to be 0).
WRONG_PY = "print(-987654321)\n"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ASSESS_API_TOKEN", raising=False)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- #
# POST /run — execute once against the caller's own stdin                       #
# --------------------------------------------------------------------------- #


def test_run_echoes_program_output_for_given_stdin(client):
    resp = client.post(
        "/run", json={"code": CORRECT_PY, "language": "python", "stdin": "3\n1 2 4\n"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stdout"] == "7"
    assert body["stderr"] is None
    assert body["timed_out"] is False
    assert body["compile_error"] is None
    assert body["duration_s"] >= 0


def test_run_with_empty_stdin(client):
    resp = client.post("/run", json={"code": "print('hi')", "language": "python"})
    assert resp.status_code == 200
    assert resp.json()["stdout"] == "hi"


def test_run_reports_a_runtime_error_without_failing_the_request(client):
    """A crashing program is a normal outcome of Run, not a 500."""
    resp = client.post("/run", json={"code": "raise ValueError('boom')", "language": "python"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stdout"] == ""
    assert "boom" in body["stderr"]


def test_run_reports_a_timeout(client):
    resp = client.post(
        "/run",
        json={
            "code": "while True: pass",
            "language": "python",
            "time_limit_s": 0.2,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["timed_out"] is True


def test_run_reports_a_compile_error(client):
    """A C program that doesn't compile reports compile_error, not a crash."""
    resp = client.post("/run", json={"code": "int main(void) { return", "language": "c"})
    assert resp.status_code == 200
    body = resp.json()
    # Skip when no C toolchain is available on this machine (infra, not the code).
    if body["infra_error"]:
        pytest.skip(f"no C toolchain: {body['infra_error']}")
    assert body["compile_error"]


def test_run_rejects_unsupported_language(client):
    resp = client.post("/run", json={"code": "x", "language": "brainfuck"})
    assert resp.status_code == 400


def test_run_rejects_empty_code(client):
    assert client.post("/run", json={"code": "", "language": "python"}).status_code == 422


def test_run_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("ASSESS_API_TOKEN", "s3cret")
    body = {"code": "print(1)", "language": "python"}
    assert client.post("/run", json=body).status_code == 401
    assert (
        client.post("/run", json=body, headers={"X-Assess-Token": "s3cret"}).status_code == 200
    )


# --------------------------------------------------------------------------- #
# POST /run/tests — rehearse the suite; pass/fail only                          #
# --------------------------------------------------------------------------- #


def test_run_tests_reports_every_case_passing(client):
    resp = client.post(
        "/run/tests", json={"question": QUESTION, "code": CORRECT_PY, "language": "python"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["compile_error"] is None
    assert len(body["test_cases"]) == len(QUESTION["test_cases"])
    assert all(c["status"] == "PASS" for c in body["test_cases"])


def test_run_tests_reports_failures(client):
    resp = client.post(
        "/run/tests", json={"question": QUESTION, "code": WRONG_PY, "language": "python"}
    )
    assert resp.status_code == 200
    statuses = {c["status"] for c in resp.json()["test_cases"]}
    assert statuses == {"FAIL"}


def test_run_tests_never_returns_the_answer_key(client):
    """The whole point: a candidate may learn pass/fail, never the I/O."""
    resp = client.post(
        "/run/tests", json={"question": QUESTION, "code": WRONG_PY, "language": "python"}
    )
    body = resp.json()
    # The guarantee: each case carries only these four keys — no I/O fields at all.
    for case in body["test_cases"]:
        assert set(case.keys()) == {"name", "category", "status", "duration_s"}
    # Canary on top of it. Only distinctive values: a short expected like "2"
    # would collide with unrelated digits (e.g. inside duration_s).
    blob = json.dumps(body)
    for tc in QUESTION["test_cases"]:
        for value in (tc["expected"], tc["stdin"]):
            if len(value.strip()) >= 4:
                assert value.strip() not in blob


def test_run_tests_does_not_grade(client):
    """No verdict/score leaks in — grading stays on POST /assessments."""
    resp = client.post(
        "/run/tests", json={"question": QUESTION, "code": CORRECT_PY, "language": "python"}
    )
    body = resp.json()
    for banned in ("verdict", "score_pct", "quality", "reason", "points_earned"):
        assert banned not in body


def test_run_tests_rejects_a_malformed_question(client):
    resp = client.post(
        "/run/tests", json={"question": {"nope": 1}, "code": "print(1)", "language": "python"}
    )
    assert resp.status_code == 400


def test_run_tests_rejects_unsupported_language(client):
    resp = client.post(
        "/run/tests", json={"question": QUESTION, "code": "x", "language": "cobol"}
    )
    assert resp.status_code == 400


def test_run_tests_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("ASSESS_API_TOKEN", "s3cret")
    body = {"question": QUESTION, "code": CORRECT_PY, "language": "python"}
    assert client.post("/run/tests", json=body).status_code == 401
    assert (
        client.post("/run/tests", json=body, headers={"X-Assess-Token": "s3cret"}).status_code
        == 200
    )
