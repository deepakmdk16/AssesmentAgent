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


# ---------------------------------------------------------------------------
# T3 differentiation: the verdict logic is pure — no model, no drafting.

def test_differentiation_ok_on_rising_complexity_rank():
    status, detail = de._differentiation_verdict(
        {"easy": (1e4, 2), "medium": (1e4, 2), "hard": (1e4, 4)}
    )
    assert status == "OK"
    assert "rank rises" in detail


def test_differentiation_ok_on_tenfold_size_bound():
    status, detail = de._differentiation_verdict({"easy": (1e4, 2), "hard": (2e5, 2)})
    assert status == "OK"
    assert "size bound rises" in detail


def test_differentiation_ok_when_easy_bound_is_unparseable_but_hard_is_big():
    # An easy draft's N<=100 is invisible to the parser (bounds < 1e3 are skipped
    # by design) — a hard draft stating a big bound still counts as separation.
    status, detail = de._differentiation_verdict({"easy": (None, 2), "hard": (2e5, 2)})
    assert status == "OK"


def test_differentiation_below_tenfold_is_not_separation():
    status, detail = de._differentiation_verdict({"easy": (1e5, 2), "hard": (5e5, 2)})
    assert status == "FAIL"
    assert "indistinguishable" in detail


def test_differentiation_fails_on_size_inversion():
    status, detail = de._differentiation_verdict(
        {"easy": (2e5, 2), "medium": (1e4, 2), "hard": (1e6, 4)}
    )
    assert status == "FAIL"
    assert "inverted" in detail


def test_differentiation_rank_drop_is_not_inversion_but_not_separation_either():
    # A hard problem's insight can BE a low complexity bound, so a falling rank
    # must not fail as inverted — but with no size divergence there's nothing
    # separating the tiers, which is the actual defect.
    status, detail = de._differentiation_verdict({"easy": (1e4, 4), "hard": (1e4, 2)})
    assert status == "FAIL"
    assert "indistinguishable" in detail


def test_differentiation_fails_when_no_lever_is_parseable():
    status, detail = de._differentiation_verdict({"easy": (None, None), "hard": (None, None)})
    assert status == "FAIL"
    assert "indistinguishable" in detail


# The harness half, with drafting monkeypatched per tier.

DIFF_CASE = de.DifferentiationCase(id="t", brief="b", language="python")


def _tier_drafts(monkeypatch, questions: dict[str, dict | None]) -> None:
    def fake_draft(brief, *, language, difficulty=None, **kwargs):
        q = questions[difficulty]
        return DraftResult(engine="e", question=q, warnings=["broken"] if q is None else [])

    monkeypatch.setattr(de, "draft_question", fake_draft)


def test_check_differentiation_skips_offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _tier_drafts(monkeypatch, {"easy": None, "medium": None, "hard": None})
    status, _ = de._check_differentiation(DIFF_CASE)
    assert status == "SKIP"


def test_check_differentiation_fails_when_a_tier_drafts_nothing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    _tier_drafts(monkeypatch, {"easy": None, "medium": None, "hard": None})
    status, detail = de._check_differentiation(DIFF_CASE)
    assert status == "FAIL"
    assert "easy" in detail and "broken" in detail


def test_check_differentiation_parses_real_lever_text(monkeypatch):
    # End to end through the REAL parsers: easy's N<=100 is below the parser's
    # 1e3 floor (parses to None), hard states 2*10^5 and a higher complexity.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    _tier_drafts(
        monkeypatch,
        {
            "easy": {"constraints": "1 <= n <= 100", "required_complexity": "O(n)"},
            "medium": {
                "constraints": "1 <= n <= 10^4",
                "required_complexity": "O(n log n)",
            },
            "hard": {
                "constraints": "1 <= n <= 2*10^5, values up to 10^9",
                "required_complexity": "O(n log n)",
            },
        },
    )
    status, detail = de._check_differentiation(DIFF_CASE)
    assert status == "OK"
    assert "rank rises" in detail
