"""Offline coverage for the adversarial-eval harness (adversarial_eval.py).

The real run needs a live key; here probe_adversarial is monkeypatched so every branch of
the harness's assertion logic is exercised deterministically, with no model call and no
code execution.
"""

from __future__ import annotations

import assessment_agent.adversarial_eval as ae
from assessment_agent.adversarial import AdversarialFinding, AdversarialReport
from assessment_agent.adversarial_eval_cases import AdversarialEvalCase

CASE = AdversarialEvalCase(
    id="t", question_id="max_subarray_sum", language="python", source="print(1)"
)


def _patch(monkeypatch, report: AdversarialReport) -> None:
    monkeypatch.setattr(ae, "probe_adversarial", lambda **k: report)


def test_skip_when_offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _patch(monkeypatch, AdversarialReport(engine="offline", probed=0, findings=[], summary="no key"))
    status, detail = ae._check(CASE)
    assert status == "SKIP"


def test_fail_when_zero_probed_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    _patch(monkeypatch, AdversarialReport(engine="e", probed=0, findings=[], summary="generation failed"))
    status, detail = ae._check(CASE)
    # A 0-case run is a distinct failure from a false positive (timeout / bad output).
    assert status == "EMPTY"
    assert "0 cases" in detail
    assert "false positive" not in detail


def test_fail_on_false_positive_finding(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    finding = AdversarialFinding(name="edge", stdin="", rationale="r", kind="crash", detail="boom")
    _patch(monkeypatch, AdversarialReport(engine="e", probed=5, findings=[finding], summary="1 crash"))
    status, detail = ae._check(CASE)
    assert status == "FINDING"
    assert "false positive" in detail


def test_ok_when_no_findings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    _patch(monkeypatch, AdversarialReport(engine="e", probed=8, findings=[], summary="clean"))
    status, detail = ae._check(CASE)
    assert status == "OK"
    assert "probed 8" in detail


def test_main_returns_zero_when_all_skipped(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        ae, "probe_adversarial",
        lambda **k: AdversarialReport(engine="offline", probed=0, findings=[], summary=""),
    )
    assert ae.main() == 0
