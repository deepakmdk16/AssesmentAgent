"""The Assessment Agent: orchestrates execution, scoring, quality, and verdict.

The verdict is score-based (like a real judge): each test case carries points
(larger inputs are worth more), the candidate earns a percentage, and the
verdict is PASS if the score meets the question's threshold, else FAIL. A TLE
simply forfeits that case's points rather than hard-failing — so a correct-but-
too-slow solution scores lower and typically falls below the bar. Code quality
(including estimated time complexity) is always reported but does not gate the
verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import ERROR, FAIL, PASS, PERFORMANCE, Verdict
from .judge import QualityAssessment, assess_quality
from .pricing import Usage
from .questions import HARDCODED_QUESTION, Question
from .runner import ExecutionReport, run_submission


@dataclass
class AssessmentResult:
    question: Question
    language: str
    execution: ExecutionReport
    quality: QualityAssessment
    quality_engine: str
    verdict: Verdict
    reason: str
    score_pct: float
    points_earned: float
    points_total: float
    pass_threshold_pct: float
    usage: Usage | None = None


def _format_execution_summary(execution: ExecutionReport) -> str:
    """Human-readable execution summary handed to the judge (includes timing)."""
    if execution.compile_error:
        return f"COMPILE ERROR: {execution.compile_error}"
    _, _, pct = execution.score()
    lines = [f"Weighted score: {pct:.0f}%."]
    for o in execution.outcomes:
        status = "PASS" if o.passed else ("TLE" if o.timed_out else "FAIL")
        detail = f" ({o.error})" if o.error else ""
        lines.append(
            f"  [{status}] {o.name} ({o.category}, weight {o.weight:g}, {o.duration_s:.3f}s): "
            f"expected {o.expected!r}, got {o.actual!r}{detail}"
        )
    return "\n".join(lines)


def assess(
    source: str,
    language: str,
    question: Question = HARDCODED_QUESTION,
) -> AssessmentResult:
    execution = run_submission(
        source, language, question.test_cases, time_limit_s=question.time_limit_s
    )
    execution_summary = _format_execution_summary(execution)
    performance_ok = execution.category_passed(PERFORMANCE)

    quality, engine, usage = assess_quality(
        question_prompt=question.prompt,
        constraints=question.constraints,
        language=language,
        source=source,
        execution_summary=execution_summary,
        performance_ok=performance_ok,
    )

    earned, total, pct = execution.score()
    threshold_pct = question.pass_threshold * 100.0

    if execution.infra_error:
        verdict = ERROR
        reason = f"Could not evaluate submission: {execution.infra_error}"
    elif execution.compile_error:
        verdict = FAIL
        reason = "Submission did not compile — score 0%."
    else:
        wrong = [o.name for o in execution.outcomes if not o.passed and not o.timed_out]
        tle = [o.name for o in execution.outcomes if o.timed_out]
        notes = []
        if wrong:
            notes.append(f"wrong answer on {', '.join(wrong)}")
        if tle:
            notes.append(f"too slow (TLE) on {', '.join(tle)}")
        note = f" ({'; '.join(notes)})" if notes else ""
        verdict = PASS if pct >= threshold_pct else FAIL
        reason = (
            f"Scored {pct:.0f}% ({earned:g}/{total:g} points), "
            f"threshold {threshold_pct:.0f}%{note}. "
            f"Estimated complexity: {quality.time_complexity}."
        )

    return AssessmentResult(
        question=question,
        language=language,
        execution=execution,
        quality=quality,
        quality_engine=engine,
        verdict=verdict,
        reason=reason,
        score_pct=pct,
        points_earned=earned,
        points_total=total,
        pass_threshold_pct=threshold_pct,
        usage=usage,
    )


def result_to_dict(result: AssessmentResult) -> dict:
    """The full, storable record: verdict, score, every test case, and quality."""
    ex = result.execution
    return {
        "question_id": result.question.id,
        "question_title": result.question.title,
        "language": result.language,
        "verdict": result.verdict,
        "reason": result.reason,
        "score_pct": round(result.score_pct, 1),
        "points_earned": result.points_earned,
        "points_total": result.points_total,
        "pass_threshold_pct": result.pass_threshold_pct,
        "compile_error": ex.compile_error,
        "infra_error": ex.infra_error,
        "test_cases": [
            {
                "name": o.name,
                "category": o.category,
                "weight": o.weight,
                "status": "PASS" if o.passed else ("TLE" if o.timed_out else "FAIL"),
                "input": o.stdin,
                "expected": o.expected,
                "actual": o.actual,
                "duration_s": round(o.duration_s, 3),
                "timed_out": o.timed_out,
                "error": o.error,
            }
            for o in ex.outcomes
        ],
        "quality": {
            "engine": result.quality_engine,
            "time_complexity": result.quality.time_complexity,
            "meets_time_constraints": result.quality.meets_time_constraints,
            "overall_score": result.quality.overall_score,
            "criteria": [
                {"name": c.name, "score": c.score, "comment": c.comment}
                for c in result.quality.criteria
            ],
            "strengths": result.quality.strengths,
            "weaknesses": result.quality.weaknesses,
            "summary": result.quality.summary,
        },
        "judge_cost_usd": (result.usage.cost_usd if result.usage and result.usage.priced else None),
    }
