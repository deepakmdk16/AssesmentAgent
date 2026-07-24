"""Tests for the question-authoring assistant (open item #5, Phase A).

`build_from_spec` is deterministic and needs no API key — it executes a
hand-built reference solution through the real runner to fill each case's
`expected` — so the core logic is fully covered offline. The live model call
(`_draft_spec`) is exercised only by the pre-push live-key smoke test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from assessment_agent import api, authoring
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
# Independent brute force: same contract, accumulated the obvious slow way.
BRUTE_PY = (
    "import sys\n"
    "d = sys.stdin.read().split()\n"
    "n = int(d[0])\n"
    "t = 0\n"
    "for x in d[1 : 1 + n]:\n"
    "    t += int(x)\n"
    "print(t)\n"
)
# A reference that is WRONG but deterministic: it doubles the answer when N == 3.
# It agrees with itself on every run, so nothing but a second implementation can
# catch it.
WRONG_REF_PY = (
    "import sys\n"
    "d = sys.stdin.read().split()\n"
    "n = int(d[0])\n"
    "s = sum(int(x) for x in d[1 : 1 + n])\n"
    "print(s * 2 if n == 3 else s)\n"
)


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
            {"name": "pair", "stdin": "2\n10 20\n"},
            {"name": "quad", "stdin": "4\n1 1 1 1\n"},
            {"name": "quint", "stdin": "5\n2 2 2 2 2\n"},
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


def test_ollama_provider_routes_and_degrades(monkeypatch):
    """ASSESS_LLM_PROVIDER=ollama drafts via the local model with no API key; a
    failure there degrades to an empty result tagged with the local model, and
    must not fall back to the offline no-key path."""
    monkeypatch.setenv("ASSESS_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("ASSESS_OLLAMA_MODEL", "qwen3-coder:30b")

    def _boom(**kwargs):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(authoring, "ollama_chat", _boom)
    result = draft_question("some brief", language="python", attempts=1)
    assert result.engine == "qwen3-coder:30b"
    assert result.question is None
    assert any("ollama down" in w for w in result.warnings)
    assert not any("ANTHROPIC_API_KEY" in w for w in result.warnings)


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
    question, _ = question_from_dict(q)
    # ...and grades a correct submission to PASS.
    graded = assess(REF_PY, "python", question)
    assert graded.verdict == "PASS"


def test_example_uses_first_surviving_case_with_oracle_output():
    # The first correctness case is dropped (malformed) -> the worked example must
    # come from the next survivor, carrying its ORACLE output (30), not a guess.
    result = build_from_spec(
        _spec(
            correctness_inputs=[
                {"name": "bad", "stdin": "xyz\n"},
                {"name": "ok", "stdin": "2\n10 20\n"},
                # Padding so enough correctness cases survive the drop to clear the
                # floor; the example still comes from the first survivor ("ok").
                {"name": "p2", "stdin": "1\n7\n"},
                {"name": "p3", "stdin": "3\n1 1 1\n"},
                {"name": "p4", "stdin": "4\n2 2 2 2\n"},
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
                # Padding so the survivors still clear the correctness-case floor.
                {"name": "g2", "stdin": "1\n9\n"},
                {"name": "g3", "stdin": "3\n1 2 3\n"},
                {"name": "g4", "stdin": "4\n5 5 5 5\n"},
            ]
        ),
        engine="test",
    )
    assert result.question is not None, result.warnings
    names = {c["name"] for c in result.question["test_cases"]}
    assert names == {"good", "g2", "g3", "g4", "performance_large"}
    assert any("malformed" in w for w in result.warnings)


def test_agreeing_brute_force_keeps_every_case():
    result = build_from_spec(_spec(brute_force_solution=BRUTE_PY), engine="test")
    assert result.question is not None, result.warnings
    names = {c["name"] for c in result.question["test_cases"]}
    assert names == {"small", "single", "pair", "quad", "quint", "performance_large"}
    assert not any("disagree" in w for w in result.warnings)


def test_wrong_oracle_is_caught_by_the_brute_force():
    """The defect the cross-check exists for: a reference that is wrong but
    deterministic. Every expected output comes from running it, so it agrees with
    itself and passes every other check — only an independent implementation can
    contradict it. Here the reference doubles the answer when N == 3, so the
    disputed case is dropped while the ones both agree on survive."""
    result = build_from_spec(
        _spec(reference_solution=WRONG_REF_PY, brute_force_solution=BRUTE_PY),
        engine="test",
    )
    assert result.question is not None, result.warnings
    names = {c["name"] for c in result.question["test_cases"]}
    # 'small' (N == 3) is disputed and dropped; the other N != 3 cases are agreed
    # and kept (and enough survive to clear the correctness-case floor).
    assert names == {"single", "pair", "quad", "quint", "performance_large"}
    assert any("small" in w and "disagree" in w for w in result.warnings)
    # The brute force is never run on the performance input — it would be far too
    # slow — so that case is unaffected by the cross-check.
    perf = next(c for c in result.question["test_cases"] if c["name"] == "performance_large")
    assert perf["expected"] == str(PERF_SUM)


def test_missing_brute_force_warns_that_the_oracle_is_unverified():
    result = build_from_spec(_spec(), engine="test")
    assert result.question is not None, result.warnings
    assert any("unverified" in w for w in result.warnings)


def test_broken_brute_force_degrades_to_a_warning():
    # A second opinion that cannot compile leaves the reference unverified, which
    # is no worse than not asking for one — the question must still be built.
    result = build_from_spec(
        _spec(brute_force_solution="this is not valid python ((("), engine="test"
    )
    assert result.question is not None, result.warnings
    names = {c["name"] for c in result.question["test_cases"]}
    assert names == {"small", "single", "pair", "quad", "quint", "performance_large"}
    assert any("unverified" in w for w in result.warnings)


def test_broken_generator_rejects_question():
    # A generator that produces an input the reference can't parse yields no
    # trustworthy performance case -> the whole question is rejected.
    result = build_from_spec(
        _spec(performance_generator="print('not a valid input')\n"),
        engine="test",
    )
    assert result.question is None
    assert any("performance" in w.lower() or "reference" in w.lower() for w in result.warnings)


# --- retry ------------------------------------------------------------------
#
# Drafting is stochastic, so an unusable draft is worth asking again for. These
# drive `_draft_spec` directly (no API key, no model call).


def _with_key_and_specs(monkeypatch, specs: list[DraftSpec | Exception]):
    """Make draft_question run, with `_draft_spec` yielding `specs` in order."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls: list[int] = []

    def _fake(config, brief, language, difficulty, target_complexity):
        item = specs[len(calls)]
        calls.append(1)
        if isinstance(item, Exception):
            raise item
        return item, None

    monkeypatch.setattr(authoring, "_draft_spec", _fake)
    return calls


def test_retries_when_the_first_draft_is_unusable(monkeypatch):
    """The C++-header case: attempt 1's reference won't run, attempt 2 is fine."""
    bad = _spec(reference_solution="import sys\nraise SystemExit(1)\n")
    calls = _with_key_and_specs(monkeypatch, [bad, _spec()])

    result = draft_question("sum of n", language="python", attempts=2)

    assert len(calls) == 2  # it asked again
    assert result.question is not None  # and the retry produced a usable draft


def test_does_not_retry_a_good_draft(monkeypatch):
    """A usable first draft must not burn a second paid model call."""
    calls = _with_key_and_specs(monkeypatch, [_spec(), _spec()])

    result = draft_question("sum of n", language="python", attempts=2)

    assert len(calls) == 1
    assert result.question is not None


def test_gives_up_after_the_attempt_budget_and_explains(monkeypatch):
    bad = _spec(reference_solution="import sys\nraise SystemExit(1)\n")
    calls = _with_key_and_specs(monkeypatch, [bad, bad])

    result = draft_question("sum of n", language="python", attempts=2)

    assert len(calls) == 2
    assert result.question is None
    assert any("gave up after 2" in w.lower() for w in result.warnings)
    # The underlying reason survives alongside the give-up note.
    assert any("reference" in w.lower() for w in result.warnings)


def test_retries_a_model_call_that_raises(monkeypatch):
    """A transient API error on attempt 1 shouldn't sink the whole draft."""
    calls = _with_key_and_specs(monkeypatch, [RuntimeError("overloaded"), _spec()])

    result = draft_question("sum of n", language="python", attempts=2)

    assert len(calls) == 2
    assert result.question is not None


def test_attempts_of_one_disables_retry(monkeypatch):
    bad = _spec(reference_solution="import sys\nraise SystemExit(1)\n")
    calls = _with_key_and_specs(monkeypatch, [bad])

    result = draft_question("sum of n", language="python", attempts=1)

    assert len(calls) == 1
    assert result.question is None
    # No "gave up" preamble when retry wasn't in play — just the real reason.
    assert not any("gave up" in w.lower() for w in result.warnings)


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
