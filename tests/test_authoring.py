"""Tests for the question-authoring assistant (open item #5, Phase A).

`build_from_spec` is deterministic and needs no API key — it executes a
hand-built reference solution through the real runner to fill each case's
`expected` — so the core logic is fully covered offline. The live model call
(`_draft_spec`) is exercised only by the pre-push live-key smoke test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from assessment_agent import api
from assessment_agent.agent import assess
from assessment_agent.api import app
from assessment_agent.authoring import DraftResult, DraftSpec, build_from_spec, draft_question
from assessment_agent.constants import OFFLINE_ENGINE
from assessment_agent.loader import question_from_dict

# Reference (oracle): read N, then N ints; print their sum.
REF_PY = "import sys\nd = sys.stdin.read().split()\nn = int(d[0])\nprint(sum(int(x) for x in d[1 : 1 + n]))\n"
# Generator: print one large valid input (N then N values).
GEN_PY = "n = 1000\nprint(n)\nprint(' '.join(str(i) for i in range(1, n + 1)))\n"
PERF_SUM = sum(range(1, 1001))


def _spec(**overrides) -> DraftSpec:
    base = dict(
        id="sum_n",
        title="Sum of N",
        prompt="Read N then N integers; print their sum.",
        constraints="1 <= N <= 100000.",
        reference_solution=REF_PY,
        reference_language="python",
        correctness_inputs=[
            {"name": "small", "stdin": "3\n1 2 3\n"},
            {"name": "single", "stdin": "1\n5\n"},
        ],
        performance_generator=GEN_PY,
        required_complexity="O(N)",
    )
    base.update(overrides)
    return DraftSpec.model_validate(base)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ASSESS_API_TOKEN", raising=False)


def test_offline_result_without_key():
    result = draft_question("anything", language="python")
    assert result.engine == OFFLINE_ENGINE
    assert result.question is None
    assert result.warnings


def test_build_fills_expected_and_validates():
    result = build_from_spec(_spec(), engine="test")
    assert result.question is not None, result.warnings
    q = result.question
    by_name = {c["name"]: c for c in q["test_cases"]}
    # `expected` came from executing the reference — never from the model.
    assert by_name["small"]["expected"] == "6"
    assert by_name["single"]["expected"] == "5"
    # The performance case is built from the generator's output, then graded by
    # running the reference on it.
    assert by_name["performance_large"]["expected"] == str(PERF_SUM)
    assert by_name["performance_large"]["category"] == "performance"
    # The worked example is ORACLE-derived from the first correctness case — its
    # executed output, never a model-written answer — and appended to the prompt.
    assert q["example"] == {"input": "3\n1 2 3\n", "output": "6"}
    assert "Example:" in q["prompt"] and q["prompt"].rstrip().endswith("6")
    # The drafted JSON round-trips through the same loader the intake uses...
    question_from_dict(q)
    # ...and grades a correct submission to PASS.
    graded = assess(REF_PY, "python", question_from_dict(q))
    assert graded.verdict == "PASS"


def test_example_uses_first_surviving_case_with_oracle_output():
    # The first correctness case is dropped (malformed) -> the worked example must
    # come from the next survivor, carrying its ORACLE output (30), not a guess.
    result = build_from_spec(
        _spec(
            correctness_inputs=[
                {"name": "bad", "stdin": "xyz\n"},
                {"name": "ok", "stdin": "2\n10 20\n"},
            ]
        ),
        engine="test",
    )
    assert result.question is not None, result.warnings
    assert result.question["example"] == {"input": "2\n10 20\n", "output": "30"}
    assert "Example:" in result.question["prompt"]


def test_reference_crash_drops_that_case():
    # A non-numeric input makes the python reference raise -> the correctness case
    # is dropped with a warning, and the question is still built from the survivors.
    result = build_from_spec(
        _spec(
            correctness_inputs=[
                {"name": "good", "stdin": "2\n4 6\n"},
                {"name": "malformed", "stdin": "abc\n"},
            ]
        ),
        engine="test",
    )
    assert result.question is not None, result.warnings
    names = {c["name"] for c in result.question["test_cases"]}
    assert names == {"good", "performance_large"}
    assert any("malformed" in w for w in result.warnings)


def test_broken_generator_rejects_question():
    # A generator that produces an input the reference can't parse yields no
    # trustworthy performance case -> the whole question is rejected.
    result = build_from_spec(
        _spec(performance_generator="print('not a valid input')\n"),
        engine="test",
    )
    assert result.question is None
    assert any("performance" in w.lower() or "reference" in w.lower() for w in result.warnings)


# --- endpoint ---------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _body(**extra) -> dict:
    return {"brief": "sum of n integers", "language": "python", **extra}


def test_draft_unsupported_language(client):
    r = client.post("/questions/draft", json=_body(language="cobol"))
    assert r.status_code == 400


def test_draft_offline_returns_503(client):
    # No ANTHROPIC_API_KEY -> drafting can't run.
    r = client.post("/questions/draft", json=_body())
    assert r.status_code == 503


def test_draft_requires_token_when_set(client, monkeypatch):
    monkeypatch.setenv("ASSESS_API_TOKEN", "secret")
    r = client.post("/questions/draft", json=_body())
    assert r.status_code == 401


def test_draft_success(client, monkeypatch):
    # Stub the model call; the deterministic reference-execution path is real.
    good = build_from_spec(_spec(), engine="stub")

    def _fake(brief, *, language, difficulty=None, target_complexity=None):
        return good

    monkeypatch.setattr(api, "draft_question", _fake)
    r = client.post("/questions/draft", json=_body())
    assert r.status_code == 200
    payload = r.json()
    assert payload["question"]["id"] == "sum_n"
    assert payload["reference_solution"] == REF_PY


def test_draft_unusable_returns_422(client, monkeypatch):
    bad = DraftResult(engine="stub", question=None, warnings=["nothing usable"])
    monkeypatch.setattr(api, "draft_question", lambda *a, **k: bad)
    r = client.post("/questions/draft", json=_body())
    assert r.status_code == 422
    assert "nothing usable" in str(r.json())
