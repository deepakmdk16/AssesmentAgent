import json

import pytest

from assessment_agent.judge import _parse_assessment


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
