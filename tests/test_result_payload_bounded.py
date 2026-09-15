"""R2-001: the result callback must fit the platform's request body cap.

`result_to_dict` echoes each case's `input`, `expected` and `actual` into the
payload the agent POSTs to the platform's `/assessments/callback`, where
`api.py::_limit_body_size` 413s anything over `MAX_BODY_BYTES`. The agent does
not retry a 4xx, so the reaper re-triggers, gives up, and the submission ends as
"error" with no stored reason — the candidate's grade is simply lost. A question
carrying a 9.6 MB performance input did exactly that twice in dev.

The fix is here rather than a bigger cap on the platform: a stored case's input
is bounded (512 KiB) but a candidate's *stdout* is bounded only by
`runner._OUTPUT_LIMIT_BYTES` (64 MB), so no body cap the platform could pick is
safe while the payload echoes whole values. It carries an excerpt instead.
"""

from __future__ import annotations

import json

from assessment_agent.agent import (
    PAYLOAD_EXCERPT_BYTES,
    AssessmentResult,
    result_from_dict,
    result_to_dict,
)
from assessment_agent.constants import PASS, PERFORMANCE
from assessment_agent.judge import skipped_assessment
from assessment_agent.questions import HARDCODED_QUESTION
from assessment_agent.runner import ExecutionReport, TestOutcome

# The platform's own cap (assessment_platform/config.py::MAX_BODY_BYTES). Mirrored
# as a ceiling to assert against, not as a contract — the cross-repo assertion
# that these two agree lives in the platform's test_agent_contract_parity.py,
# which reads PAYLOAD_EXCERPT_BYTES from this module.
PLATFORM_BODY_CAP = 16 * 1024 * 1024


def _result(outcomes: list[TestOutcome]) -> AssessmentResult:
    return AssessmentResult(
        question=HARDCODED_QUESTION,
        language="python",
        source="print(1)",
        execution=ExecutionReport(language="python", compile_error=None, outcomes=outcomes),
        quality=skipped_assessment(),
        quality_engine="skipped",
        verdict=PASS,
        reason="all cases passed",
        score_pct=100.0,
        points_earned=1.0,
        points_total=1.0,
        pass_threshold_pct=60.0,
    )


def _huge_case(name: str = "performance_large") -> TestOutcome:
    """A case shaped like the one that lost a grade: a multi-megabyte generated
    input, and a candidate program that echoed it back."""
    blob = "1234567890 " * 1_000_000  # ~11 MB
    return TestOutcome(
        name=name,
        stdin=blob,
        expected=blob,
        actual=blob,
        passed=True,
        category=PERFORMANCE,
        weight=3.0,
    )


def test_a_ten_megabyte_case_still_fits_the_callback() -> None:
    body = json.dumps(result_to_dict(_result([_huge_case()])))
    assert len(body.encode()) < PLATFORM_BODY_CAP, (
        f"{len(body.encode()):,}-byte callback for one large case — the platform 413s it "
        "and the grade is lost (R2-001)."
    )


def test_every_echoed_field_is_bounded_even_at_the_case_limit() -> None:
    """25 cases is the platform's `QuestionCreate.test_cases` cap, and each echoes
    three values, so this is the worst body the contract allows."""
    outcomes = [_huge_case(f"case_{i}") for i in range(25)]
    body = json.dumps(result_to_dict(_result(outcomes)))
    assert len(body.encode()) < PLATFORM_BODY_CAP


def test_an_oversized_value_says_how_much_was_dropped() -> None:
    case = result_to_dict(_result([_huge_case()]))["test_cases"][0]
    for field in ("input", "expected", "actual"):
        value = case[field]
        assert len(value.encode()) <= PAYLOAD_EXCERPT_BYTES, f"{field} not excerpted"
        assert "11,000,000 bytes" in value, f"{field} does not report the original size"


def test_an_ordinary_case_is_carried_verbatim() -> None:
    """The excerpt must not touch the 99% of cases that are tens of bytes —
    truncating those would be a silent loss of the report's evidence."""
    case = TestOutcome(name="small", stdin="3 4\n", expected="7\n", actual="7\n", passed=True)
    out = result_to_dict(_result([case]))["test_cases"][0]
    assert (out["input"], out["expected"], out["actual"]) == ("3 4\n", "7\n", "7\n")


def test_the_excerpt_survives_a_report_round_trip() -> None:
    """`POST /report` rebuilds a stored payload with `result_from_dict` and
    re-serializes it; an excerpt must be a fixed point, or the PDF path would
    truncate an already-truncated value and drift on every pass."""
    once = result_to_dict(_result([_huge_case()]))
    twice = result_to_dict(
        result_from_dict(once, question=HARDCODED_QUESTION, source="print(1)")
    )
    assert twice["test_cases"] == once["test_cases"]


def test_a_tiny_configured_cap_still_shrinks_the_value(monkeypatch) -> None:
    """`ASSESS_PAYLOAD_EXCERPT_KB=0` must not invert the head/tail arithmetic and
    hand back something larger than what it was asked to bound."""
    import assessment_agent.agent as agent_module

    monkeypatch.setattr(agent_module, "PAYLOAD_EXCERPT_BYTES", 1)
    out = agent_module._excerpt("9" * 100_000)
    assert len(out) < 200, f"excerpt grew to {len(out)} chars"


def test_a_runaway_compile_error_is_bounded_too() -> None:
    """A C++ template-error cascade is raw compiler stderr with no cap on it, so
    it loses the grade exactly the way an unbounded `actual` did — and unlike the
    test cases, nothing in the question's size bounds it."""
    result = _result([TestOutcome(name="c", stdin="", expected="", actual="", passed=False)])
    result.execution.compile_error = "error: no matching function\n" * 500_000
    payload = result_to_dict(result)
    assert len(payload["compile_error"].encode()) <= PAYLOAD_EXCERPT_BYTES
    assert len(json.dumps(payload).encode()) < PLATFORM_BODY_CAP


def test_a_none_error_stays_none() -> None:
    """`result_from_dict` reads these straight back into TestOutcome/ExecutionReport,
    where None is 'nothing went wrong' — an empty string is not the same thing."""
    payload = result_to_dict(_result([_huge_case()]))
    assert payload["compile_error"] is None
    assert payload["infra_error"] is None
    assert payload["test_cases"][0]["error"] is None
