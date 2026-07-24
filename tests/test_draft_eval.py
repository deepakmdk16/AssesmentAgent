"""Offline coverage for the draft-eval harness (draft_eval.py).

The real run needs a live key; here draft_question / assess / question_from_dict are
monkeypatched so every branch of the harness's assertion logic is exercised
deterministically, with no model call and no code execution.
"""

from __future__ import annotations

from types import SimpleNamespace

import assessment_agent.draft_eval as de
from assessment_agent.authoring import DraftResult
from assessment_agent.draft_eval_cases import DraftEvalCase

CASE = DraftEvalCase(id="t", brief="b", language="python", min_correctness_cases=2)


def _tc(category: str) -> SimpleNamespace:
    return SimpleNamespace(category=category)


def _question(n_corr: int, n_perf: int) -> SimpleNamespace:
    cases = [_tc("correctness")] * n_corr + [_tc("performance")] * n_perf
    return SimpleNamespace(test_cases=cases)


def _patch_draft(monkeypatch, result: DraftResult) -> None:
    monkeypatch.setattr(de, "draft_question", lambda *a, **k: result)


def test_skip_when_offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _patch_draft(monkeypatch, DraftResult(engine="offline", question=None, warnings=["offline"]))
    status, detail = de._check(CASE)
    assert status == "SKIP"


def test_fail_when_no_question_but_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    _patch_draft(monkeypatch, DraftResult(engine="e", question=None, warnings=["ambiguous brief"]))
    status, detail = de._check(CASE)
    assert status == "FAIL"
    assert "ambiguous brief" in detail


def test_fail_when_too_few_correctness_cases(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    _patch_draft(
        monkeypatch,
        DraftResult(
            engine="e",
            question={"x": 1},
            reference_solution="print(1)",
            reference_language="python",
        ),
    )
    monkeypatch.setattr(de, "question_from_dict", lambda d: (_question(1, 1), []))
    status, detail = de._check(CASE)
    assert status == "FAIL"
    assert "correctness" in detail


def test_fail_when_no_performance_case(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    _patch_draft(
        monkeypatch,
        DraftResult(
            engine="e",
            question={"x": 1},
            reference_solution="print(1)",
            reference_language="python",
        ),
    )
    monkeypatch.setattr(de, "question_from_dict", lambda d: (_question(3, 0), []))
    status, detail = de._check(CASE)
    assert status == "FAIL"
    assert "performance" in detail


def test_fail_when_reference_does_not_pass(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    _patch_draft(
        monkeypatch,
        DraftResult(
            engine="e",
            question={"x": 1},
            reference_solution="print(1)",
            reference_language="python",
        ),
    )
    monkeypatch.setattr(de, "question_from_dict", lambda d: (_question(3, 1), []))
    monkeypatch.setattr(
        de, "assess", lambda *a, **k: SimpleNamespace(verdict="FAIL", score_pct=40.0)
    )
    status, detail = de._check(CASE)
    assert status == "FAIL"
    assert "reference graded FAIL" in detail


def test_ok_when_reference_passes(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    _patch_draft(
        monkeypatch,
        DraftResult(
            engine="e",
            question={"x": 1},
            reference_solution="print(1)",
            reference_language="python",
        ),
    )
    monkeypatch.setattr(de, "question_from_dict", lambda d: (_question(3, 1), []))
    monkeypatch.setattr(
        de, "assess", lambda *a, **k: SimpleNamespace(verdict="PASS", score_pct=100.0)
    )
    status, detail = de._check(CASE)
    assert status == "OK"
    assert "PASS" in detail


def test_main_returns_zero_when_all_skipped(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        de, "draft_question", lambda *a, **k: DraftResult(engine="offline", question=None)
    )
    assert de.main() == 0
