"""Generic, registry-driven checks that hold for EVERY built-in question.

The point of this file is that adding a question to `QUESTIONS` is automatically
covered: you register it (plus an independent oracle cross-check and a reference
good sample), and these parameterized tests exercise it — no per-question test
code. The coverage test below fails if you forget either registration, so a new
question cannot slip in unvalidated.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from assessment_agent.questions import (
    QUESTIONS,
    Question,
    TestCase,
    _knapsack,
    _max_subarray,
    validate_question,
)
from assessment_agent.runner import run_submission

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Independent oracle cross-checks (differential testing).
#
# The grading oracles (_max_subarray, _knapsack) *define* the expected answers,
# so "expected matches oracle" is tautological. Here we pit each fast oracle
# against a naive reference written independently, on many random small inputs.
# This is what actually validates that the oracle is correct.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OracleCheck:
    make_args: Callable[[random.Random], tuple]  # random input, as oracle args
    fast: Callable[..., int]  # the grading oracle
    naive: Callable[..., int]  # independent reference


def _naive_max_subarray(nums: list[int]) -> int:
    best = nums[0]
    for i in range(len(nums)):
        total = 0
        for j in range(i, len(nums)):
            total += nums[j]
            best = max(best, total)
    return best


def _naive_knapsack(capacity: int, items: list[tuple[int, int]]) -> int:
    best = 0
    for mask in range(1 << len(items)):
        w = v = 0
        for i, (wi, vi) in enumerate(items):
            if mask >> i & 1:
                w += wi
                v += vi
        if w <= capacity:
            best = max(best, v)
    return best


def _gen_max_subarray(rng: random.Random) -> tuple:
    nums = [rng.randint(-9, 9) for _ in range(rng.randint(1, 8))]
    return (nums,)


def _gen_knapsack(rng: random.Random) -> tuple:
    items = [(rng.randint(1, 8), rng.randint(1, 8)) for _ in range(rng.randint(1, 6))]
    return (rng.randint(0, 15), items)


# One entry per question id. Adding a question means adding a line here.
ORACLE_CHECKS: dict[str, OracleCheck] = {
    "max_subarray_sum": OracleCheck(_gen_max_subarray, _max_subarray, _naive_max_subarray),
    "knapsack_01": OracleCheck(_gen_knapsack, _knapsack, _naive_knapsack),
}

# A known-correct, fast reference submission per question — must score 100%.
GOOD_SAMPLES: dict[str, str] = {
    "max_subarray_sum": "submissions/good_solution.py",
    "knapsack_01": "submissions/knapsack_good.py",
}


# --------------------------------------------------------------------------- #
# Coverage enforcement: a new question cannot be added without its cross-check
# and reference sample. This is the guardrail that keeps the suite honest.
# --------------------------------------------------------------------------- #
def test_every_question_has_oracle_check_and_sample():
    assert set(ORACLE_CHECKS) == set(QUESTIONS), (
        "every question needs an independent oracle cross-check in ORACLE_CHECKS"
    )
    assert set(GOOD_SAMPLES) == set(QUESTIONS), (
        "every question needs a reference good sample in GOOD_SAMPLES"
    )


@pytest.mark.parametrize("qid,q", sorted(QUESTIONS.items()))
def test_registry_key_matches_id(qid, q):
    assert qid == q.id


# --------------------------------------------------------------------------- #
# Generic per-question checks.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("q", QUESTIONS.values(), ids=lambda q: q.id)
def test_builtin_questions_are_structurally_valid(q):
    validate_question(q)  # raises on any invariant violation


@pytest.mark.parametrize("qid,check", sorted(ORACLE_CHECKS.items()))
def test_grading_oracle_matches_naive_reference(qid, check):
    rng = random.Random(2024)
    for _ in range(400):
        args = check.make_args(rng)
        assert check.fast(*args) == check.naive(*args), f"oracle mismatch on {args!r}"


@pytest.mark.parametrize("qid,path", sorted(GOOD_SAMPLES.items()))
def test_reference_good_sample_scores_full(qid, path):
    q = QUESTIONS[qid]
    source = (ROOT / path).read_text()
    report = run_submission(source, "python", q.test_cases, time_limit_s=q.time_limit_s)
    assert report.all_passed, (
        f"reference good sample for {qid} did not pass every case: "
        f"{[(o.name, o.passed, o.error) for o in report.outcomes if not o.passed]}"
    )


# --------------------------------------------------------------------------- #
# validate_question must actually reject malformed questions.
# --------------------------------------------------------------------------- #
def _q(**over) -> Question:
    base = dict(
        id="x",
        title="X",
        prompt="p",
        constraints="c",
        test_cases=(
            TestCase("ok1", "1\n", "1"),
            TestCase("ok2", "2\n", "2"),
            TestCase("ok3", "3\n", "3"),
            TestCase("ok4", "4\n", "4"),
            TestCase("perf", "1\n", "1", category="performance", weight=6.0),
        ),
    )
    base.update(over)
    return Question(**base)


@pytest.mark.parametrize(
    "bad,match",
    [
        (dict(test_cases=(TestCase("only", "1\n", "1"),)), "performance"),
        (
            dict(
                test_cases=(
                    TestCase("a", "1\n", "1", weight=0.0),
                    TestCase("p", "1\n", "1", category="performance", weight=6.0),
                )
            ),
            "weight",
        ),
        (dict(pass_threshold=1.5), "pass_threshold"),
        (dict(time_limit_s=0.0), "time_limit_s"),
        (dict(constraints="  "), "constraints"),
        (
            dict(
                test_cases=(
                    TestCase("dup", "1\n", "1"),
                    TestCase("dup", "1\n", "1", category="performance", weight=6.0),
                )
            ),
            "unique",
        ),
    ],
)
def test_validate_question_rejects_malformed(bad, match):
    with pytest.raises(ValueError, match=match):
        validate_question(_q(**bad))


# --------------------------------------------------------------------------- #
# The case-count floor is an AUTHORING invariant: hard when authoring, but on the
# grade/intake path it degrades to a warning so a pre-floor question still grades
# instead of 400ing the candidate (F4).
# --------------------------------------------------------------------------- #
def _under_floor() -> Question:
    return _q(
        test_cases=(
            TestCase("ok1", "1\n", "1"),
            TestCase("ok2", "2\n", "2"),
            TestCase("ok3", "3\n", "3"),
            TestCase("perf", "1\n", "1", category="performance", weight=6.0),
        )
    )


def test_case_floor_is_hard_when_authoring():
    with pytest.raises(ValueError, match="correctness"):
        validate_question(_under_floor())  # default (authoring) mode


def test_case_floor_degrades_to_warning_on_grade_path():
    warnings = validate_question(_under_floor(), degrade_authoring=True)
    assert any("correctness" in w for w in warnings)


def test_structural_invariant_stays_hard_on_grade_path():
    # Only authoring-shape invariants degrade; a broken grading parameter still raises.
    with pytest.raises(ValueError, match="pass_threshold"):
        validate_question(_q(pass_threshold=1.5), degrade_authoring=True)
