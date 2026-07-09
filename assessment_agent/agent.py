"""The Assessment Agent: orchestrates execution, quality judging, and the verdict."""

from __future__ import annotations

from dataclasses import dataclass

from .judge import QualityAssessment, assess_quality
from .pricing import Usage
from .questions import HARDCODED_QUESTION, Question
from .runner import ExecutionReport, run_submission

# All tests must pass, and quality must clear this bar, to earn a PASS.
PASS_QUALITY_THRESHOLD = 3.0


@dataclass
class AssessmentResult:
    question: Question
    language: str
    execution: ExecutionReport
    quality: QualityAssessment
    quality_engine: str
    verdict: str  # "PASS" or "FAIL"
    reason: str
    usage: Usage | None = None


def _format_test_summary(execution: ExecutionReport) -> str:
    if execution.compile_error:
        return f"COMPILE ERROR: {execution.compile_error}"
    total = len(execution.outcomes)
    lines = [f"{execution.passed_count}/{total} test cases passed."]
    for o in execution.outcomes:
        status = "PASS" if o.passed else "FAIL"
        detail = f" ({o.error})" if o.error else ""
        lines.append(f"  [{status}] {o.name}: expected {o.expected!r}, got {o.actual!r}{detail}")
    return "\n".join(lines)


def assess(
    source: str,
    language: str,
    question: Question = HARDCODED_QUESTION,
) -> AssessmentResult:
    execution = run_submission(source, language, question.test_cases)
    test_summary = _format_test_summary(execution)

    quality, engine, usage = assess_quality(
        question_prompt=question.prompt,
        language=language,
        source=source,
        test_summary=test_summary,
    )

    if execution.infra_error:
        verdict = "ERROR"
        reason = f"Could not evaluate submission: {execution.infra_error}"
    elif not execution.all_passed:
        verdict = "FAIL"
        if execution.compile_error:
            reason = "Submission did not compile."
        else:
            reason = (
                f"{execution.passed_count}/{len(execution.outcomes)} tests passed — "
                "functional correctness gate not met."
            )
    elif quality.overall_score < PASS_QUALITY_THRESHOLD:
        verdict = "FAIL"
        reason = (
            f"All tests passed, but code quality {quality.overall_score:.1f}/5 is "
            f"below the {PASS_QUALITY_THRESHOLD:.1f} bar."
        )
    else:
        verdict = "PASS"
        reason = (
            f"All tests passed and code quality {quality.overall_score:.1f}/5 "
            f"meets the {PASS_QUALITY_THRESHOLD:.1f} bar."
        )

    return AssessmentResult(
        question=question,
        language=language,
        execution=execution,
        quality=quality,
        quality_engine=engine,
        verdict=verdict,
        reason=reason,
        usage=usage,
    )
