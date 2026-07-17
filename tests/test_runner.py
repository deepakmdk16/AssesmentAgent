import os
import shutil
import subprocess
import time
import uuid

import pytest

from assessment_agent import runner
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


@pytest.mark.skipif(runner.resource is None, reason="POSIX resource limits unavailable")
def test_output_cap_fails_a_runaway_print(monkeypatch):
    # A submission that prints far past the output ceiling is killed (SIGXFSZ)
    # and surfaces as a failing case — never a worker OOM or an infra error.
    monkeypatch.setattr(runner, "_OUTPUT_LIMIT_BYTES", 4096)
    report = run_submission("print('x' * 1_000_000)\n", "python", (tc("", "irrelevant"),))
    assert report.infra_error is None
    assert report.outcomes[0].passed is False
    assert report.outcomes[0].error


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


def test_outcomes_preserve_input_order():
    # Every case must appear in the report in the order it was declared, so a
    # report card lines up with the question. (Cases run serially now — see
    # run_submission — but the guarantee is the report's, not the scheduler's.)
    cases = tuple(TestCase(f"c{i}", f"{i}\n", str(i)) for i in range(8))
    report = run_submission(ECHO, "python", cases)
    assert [o.name for o in report.outcomes] == [f"c{i}" for i in range(8)]
    assert all(o.passed for o in report.outcomes)


def test_mixed_correctness_and_performance_all_run_in_order():
    # Categories interleave freely; each case still lands in its declared slot.
    cases = (
        TestCase("corr1", "1\n", "1", CORRECTNESS),
        TestCase("perf", "9\n", "9", PERFORMANCE),
        TestCase("corr2", "2\n", "2", CORRECTNESS),
    )
    report = run_submission(ECHO, "python", cases)
    assert [o.name for o in report.outcomes] == ["corr1", "perf", "corr2"]
    assert [o.category for o in report.outcomes] == [CORRECTNESS, PERFORMANCE, CORRECTNESS]
    assert all(o.passed for o in report.outcomes)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
@pytest.mark.skipif(shutil.which("pgrep") is None, reason="needs pgrep to spot survivors")
def test_timeout_kills_the_whole_process_tree_not_just_the_child():
    """A submission that forks and then hangs must not leave orphans behind.

    `subprocess.run`'s timeout signals only the direct child, so the grandchild
    here would survive the case being scored and keep running on the worker.
    Each child leads its own process group precisely so the timeout can take the
    whole tree.
    """
    marker = f"assess_orphan_probe_{uuid.uuid4().hex}"
    src = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', \"import time; time.sleep(60)  # {marker}\"])\n"
        "time.sleep(60)\n"
    )
    report = run_submission(src, "python", (TestCase("forker", "", "x"),), time_limit_s=0.5)

    assert report.outcomes[0].timed_out is True
    time.sleep(0.3)  # give the kill a beat to land
    survivors = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    assert survivors.stdout.strip() == "", "the forked grandchild outlived the timeout"
