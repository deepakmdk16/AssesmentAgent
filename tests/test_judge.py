import json

import pytest

from assessment_agent import judge
from assessment_agent.constants import FAILED_ENGINE
from assessment_agent.judge import _parse_assessment, assess_quality


def _payload(score, overall):
    return json.dumps(
        {
            "criteria": [{"name": "robustness", "score": score, "comment": "x"}],
            "overall_score": overall,
            "time_complexity": "O(n)",
            "meets_time_constraints": True,
            "strengths": ["a"],
            "weaknesses": ["b"],
            "summary": "s",
        }
    )


def test_out_of_range_scores_are_clamped():
    a = _parse_assessment(_payload(6, 9.0), "end_turn")
    assert a.criteria[0].score == 5
    assert a.overall_score == 5.0

    b = _parse_assessment(_payload(0, 0.0), "end_turn")
    assert b.criteria[0].score == 1
    assert b.overall_score == 1.0


def test_valid_scores_pass_through():
    a = _parse_assessment(_payload(4, 3.5), "end_turn")
    assert a.criteria[0].score == 4
    assert a.overall_score == 3.5


def test_truncated_json_raises_with_hint():
    with pytest.raises(RuntimeError, match="truncated"):
        _parse_assessment('{"criteria": [', "max_tokens")


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("Judge refused: policy"),
        RuntimeError("Judge returned invalid JSON: boom"),
        TimeoutError("read timed out"),
        ConnectionError("network down"),
    ],
)
def test_judge_failure_degrades_and_never_raises(monkeypatch, exc):
    """Quality is reported but must never gate the verdict (CONVENTIONS.md §1).

    An exception escaping the judge would gate it harder than any score could —
    it discards a grade the deterministic runner already decided. So every judge
    failure mode has to come back as a reported non-answer.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def _boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(judge, "_assess_with_claude", _boom)

    assessment, engine, usage = assess_quality(
        question_prompt="p",
        constraints="c",
        language="python",
        source="print(1)",
        execution_summary="Weighted score: 100%.",
        performance_ok=True,
    )
    assert engine == FAILED_ENGINE
    assert usage is None
    assert "unavailable" in assessment.summary
    assert str(exc) in assessment.summary
