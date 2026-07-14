import pytest

from assessment_agent.constants import CORRECTNESS, PERFORMANCE
from assessment_agent.questions import TestCase
from assessment_agent.runner import _normalize, run_submission

ECHO = "import sys\nprint(sys.stdin.read().strip())\n"


def tc(stdin: str, expected: str) -> TestCase:
    return TestCase("t", stdin, expected)


def test_correct_python_passes():
    src = "import sys\nd = sys.stdin.read().split()\nprint(int(d[0]) + int(d[1]))\n"
    report = run_submission(src, "python", (tc("2 3\n", "5"),))
    assert report.all_passed
    assert report.passed_count == 1


def test_wrong_output_fails():
    report = run_submission("print(0)\n", "python", (tc("2 3\n", "5"),))
    assert not report.all_passed
    assert report.outcomes[0].passed is False


def test_runtime_error_is_captured():
    report = run_submission("import sys\nsys.exit('boom')\n", "python", (tc("", "x"),))
    assert not report.all_passed
    assert report.outcomes[0].passed is False
    assert report.outcomes[0].error


def test_unsupported_language_raises():
    with pytest.raises(ValueError):
        run_submission("x", "cobol", ())


def test_normalize_ignores_trailing_whitespace():
    assert _normalize("3 6 \n") == _normalize("3 6")
    assert _normalize("a\nb\n") == "a\nb"


def test_time_limit_exceeded_is_flagged():
    src = "import time\ntime.sleep(3)\nprint('x')\n"
    report = run_submission(src, "python", (tc("", "x"),), time_limit_s=0.2)
    outcome = report.outcomes[0]
    assert outcome.timed_out
    assert not outcome.passed
    assert "time limit" in (outcome.error or "").lower()


def test_outcomes_preserve_input_order_under_parallelism():
    # Correctness cases run concurrently and can finish out of order; the report
    # must still list them in the original input order.
    cases = tuple(TestCase(f"c{i}", f"{i}\n", str(i)) for i in range(8))
    report = run_submission(ECHO, "python", cases)
    assert [o.name for o in report.outcomes] == [f"c{i}" for i in range(8)]
    assert all(o.passed for o in report.outcomes)


def test_mixed_correctness_and_performance_all_run_in_order():
    # Performance cases run isolated in a second phase; positions are preserved.
    cases = (
        TestCase("corr1", "1\n", "1", CORRECTNESS),
        TestCase("perf", "9\n", "9", PERFORMANCE),
        TestCase("corr2", "2\n", "2", CORRECTNESS),
    )
    report = run_submission(ECHO, "python", cases)
    assert [o.name for o in report.outcomes] == ["corr1", "perf", "corr2"]
    assert [o.category for o in report.outcomes] == [CORRECTNESS, PERFORMANCE, CORRECTNESS]
    assert all(o.passed for o in report.outcomes)
