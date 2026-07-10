import pytest

from assessment_agent.agent import assess, result_to_dict
from assessment_agent.questions import Question, TestCase


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# A trivial question: echo the single input line back.
def _echo_question(pass_threshold: float = 0.9) -> Question:
    return Question(
        id="echo", title="Echo", prompt="Print the input line.", constraints="tiny",
        test_cases=(
            TestCase("a", "5\n", "5", weight=1.0),
            TestCase("b", "9\n", "9", weight=3.0),
        ),
        pass_threshold=pass_threshold,
    )


ECHO_SRC = "import sys\nprint(sys.stdin.read().strip())\n"
CONST_SRC = "print(5)\n"  # only matches case 'a'


def test_full_score_passes():
    result = assess(ECHO_SRC, "python", _echo_question())
    assert result.score_pct == 100.0
    assert result.verdict == "PASS"


def test_partial_score_below_threshold_fails():
    # Passes only the weight-1 case → 1/4 = 25% < 90% → FAIL.
    result = assess(CONST_SRC, "python", _echo_question())
    assert result.score_pct == 25.0
    assert result.verdict == "FAIL"


def test_threshold_is_configurable():
    # Same 25% score passes when the question only requires 25%.
    result = assess(CONST_SRC, "python", _echo_question(pass_threshold=0.25))
    assert result.score_pct == 25.0
    assert result.verdict == "PASS"


def test_report_dict_has_full_record():
    d = result_to_dict(assess(ECHO_SRC, "python", _echo_question()))
    assert d["verdict"] == "PASS"
    assert d["score_pct"] == 100.0
    assert {c["name"] for c in d["test_cases"]} == {"a", "b"}
    first = d["test_cases"][0]
    assert set(first) >= {"name", "status", "input", "expected", "actual", "duration_s", "weight"}
    assert "time_complexity" in d["quality"]
