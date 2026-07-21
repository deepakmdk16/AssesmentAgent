"""Advisory adversarial test-gen (#4a).

The load-bearing property here is the invariant: adversarial probes are advisory
and must NEVER change the verdict, the score, or the points. Everything else
(offline fallback, skip-on-non-executing, crash/timeout classification) is
supporting behaviour.
"""

import pytest

import assessment_agent.agent as agent_mod
from assessment_agent.adversarial import (
    AdversarialFinding,
    AdversarialReport,
    _run_and_classify,
    adversarial_to_dict,
    probe_adversarial,
)
from assessment_agent.agent import assess, result_to_dict
from assessment_agent.eval_cases import EVAL_CASES
from assessment_agent.questions import HARDCODED_QUESTION
from assessment_agent.runner import ExecutionReport, TestOutcome

STRONG = next(c for c in EVAL_CASES if c.id == "strong").source


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch):
    # Never hit the API in tests.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _crash_heavy_report() -> AdversarialReport:
    """A report that, if it were ever allowed to gate, would tank any verdict."""
    return AdversarialReport(
        engine="test-model",
        probed=3,
        findings=[
            AdversarialFinding("adv_0_empty", "0\n", "minimum size", "crash", "IndexError"),
            AdversarialFinding("adv_1_big", "9\n...", "max size", "timeout", "time limit exceeded"),
        ],
        summary="Probed 3 generated edge case(s); 2 caused a crash or timeout.",
    )


# --- The invariant: advisory probes never gate ------------------------------


@pytest.mark.parametrize("source", [STRONG, "print(0)\n"])
def test_adversarial_findings_never_change_the_grade(monkeypatch, source):
    """Same submission, graded with and without a crash-heavy adversarial report:
    verdict, score, and points must be identical."""
    baseline = assess(source, "python", adversarial=False)

    monkeypatch.setattr(agent_mod, "probe_adversarial", lambda **_: _crash_heavy_report())
    probed = assess(source, "python", adversarial=True)

    assert probed.adversarial is not None and probed.adversarial.findings  # the report is attached
    assert probed.verdict == baseline.verdict
    assert probed.score_pct == baseline.score_pct
    assert probed.points_earned == baseline.points_earned
    assert probed.points_total == baseline.points_total
    assert probed.reason == baseline.reason  # the verdict rationale is unchanged too


def test_adversarial_absent_by_default():
    result = assess(STRONG, "python")
    assert result.adversarial is None
    assert result_to_dict(result)["adversarial"] is None


# --- Gating / skip behaviour ------------------------------------------------


def test_probe_skipped_when_submission_does_not_execute(monkeypatch):
    """A non-executing submission is a decided FAIL; the probe must not run."""

    def _boom(**_):
        raise AssertionError("probe_adversarial must not be called on a non-executing submission")

    monkeypatch.setattr(agent_mod, "probe_adversarial", _boom)
    result = assess("raise ValueError('boom')\n", "python", adversarial=True)
    assert result.adversarial is None
    assert result.verdict == "FAIL"


# --- Offline fallback -------------------------------------------------------


def test_offline_probe_produces_no_findings():
    report = probe_adversarial(question=HARDCODED_QUESTION, language="python", source=STRONG)
    assert report.engine == "offline-heuristic"
    assert report.probed == 0
    assert report.findings == []
    assert "ANTHROPIC_API_KEY" in report.summary


def test_ollama_provider_routes_and_degrades(monkeypatch):
    """ASSESS_LLM_PROVIDER=ollama probes via the local model with no API key; a
    failure there degrades to an empty advisory report tagged with the local
    model, never gating the verdict or falling to the offline path."""
    monkeypatch.setenv("ASSESS_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("ASSESS_OLLAMA_MODEL", "qwen3-coder:30b")

    def _boom(**kwargs):
        raise ConnectionError("ollama down")

    monkeypatch.setattr("assessment_agent.adversarial.ollama_chat", _boom)
    report = probe_adversarial(question=HARDCODED_QUESTION, language="python", source=STRONG)
    assert report.engine == "qwen3-coder:30b"
    assert report.probed == 0
    assert report.findings == []
    assert "ollama down" in report.summary
    assert "ANTHROPIC_API_KEY" not in report.summary


# --- Graceful failure: a generation error must never abort the assessment ----


def test_generation_failure_degrades_and_never_raises(monkeypatch):
    """A truncated/invalid model response (or any generation error) must produce an
    empty advisory report, not propagate and crash `assess`."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _boom(*a, **k):
        raise RuntimeError("Adversarial generator returned invalid JSON (truncated)")

    monkeypatch.setattr("assessment_agent.adversarial._generate_cases", _boom)
    report = probe_adversarial(question=HARDCODED_QUESTION, language="python", source=STRONG)
    assert report.probed == 0
    assert report.findings == []
    assert "failed" in report.summary.lower()


def test_assess_survives_generation_failure(monkeypatch):
    """End-to-end: even when adversarial generation blows up, the verdict/score
    (decided from execution alone) are returned unharmed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # Stub the judge so the fake key doesn't trigger a real quality API call.
    monkeypatch.setattr(
        agent_mod,
        "assess_quality",
        lambda **k: (agent_mod.skipped_assessment(), "offline-heuristic", None),
    )
    monkeypatch.setattr(
        "assessment_agent.adversarial._generate_cases",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("truncated")),
    )
    result = assess(STRONG, "python", adversarial=True)
    assert result.verdict == "PASS"
    assert result.score_pct == 100.0
    assert result.adversarial is not None and "failed" in result.adversarial.summary.lower()


# --- Classification (crash vs timeout vs clean) -----------------------------


def test_classification_keeps_only_crashes_and_timeouts(monkeypatch):
    from assessment_agent.adversarial import GeneratedCase

    cases = [
        GeneratedCase(name="empty", stdin="0\n", rationale="min size"),
        GeneratedCase(name="huge", stdin="1\n", rationale="max size"),
        GeneratedCase(name="fine", stdin="2\n", rationale="ordinary"),
    ]
    # Craft the runner's verdict per generated input: one crash, one timeout, one clean.
    outcomes = [
        TestOutcome(
            "adv_0_empty", "0\n", "", "", False, error="IndexError: list index out of range"
        ),
        TestOutcome(
            "adv_1_huge", "1\n", "", "", False, error="time limit exceeded", timed_out=True
        ),
        TestOutcome("adv_2_fine", "2\n", "", "3", False),  # ran clean; 'passed' is irrelevant here
    ]
    monkeypatch.setattr(
        "assessment_agent.adversarial.run_submission",
        lambda *a, **k: ExecutionReport("python", None, outcomes),
    )
    report = _run_and_classify(cases, "python", "src", HARDCODED_QUESTION, "test-model", None)

    assert report.probed == 3
    kinds = {f.name: f.kind for f in report.findings}
    assert kinds == {"adv_0_empty": "crash", "adv_1_huge": "timeout"}  # clean case not flagged
    assert "2 caused a crash or timeout" in report.summary


def test_classification_no_findings_when_all_clean(monkeypatch):
    from assessment_agent.adversarial import GeneratedCase

    cases = [GeneratedCase(name="a", stdin="1\n", rationale="x")]
    outcomes = [TestOutcome("adv_0_a", "1\n", "", "ok", False)]
    monkeypatch.setattr(
        "assessment_agent.adversarial.run_submission",
        lambda *a, **k: ExecutionReport("python", None, outcomes),
    )
    report = _run_and_classify(cases, "python", "src", HARDCODED_QUESTION, "test-model", None)
    assert report.findings == []
    assert "none crashed or timed out" in report.summary


def test_adversarial_to_dict_shape():
    d = adversarial_to_dict(_crash_heavy_report())
    assert d["engine"] == "test-model"
    assert d["probed"] == 3
    assert d["cost_usd"] is None
    assert [f["kind"] for f in d["findings"]] == ["crash", "timeout"]
    assert d["findings"][0]["input"] == "0\n"
