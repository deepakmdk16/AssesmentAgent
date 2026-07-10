import shutil
import subprocess

import pytest

from assessment_agent.agent import assess
from assessment_agent.eval_cases import EVAL_CASES

STRONG = next(c for c in EVAL_CASES if c.id == "strong").source


def _java_works() -> bool:
    # macOS ships a `javac` shim that exists but errors without a real JDK,
    # so `which` alone is not enough — confirm it actually runs.
    if shutil.which("javac") is None:
        return False
    try:
        return subprocess.run(["javac", "-version"], capture_output=True).returncode == 0
    except Exception:
        return False


JAVA_AVAILABLE = _java_works()


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch):
    # Keep the verdict deterministic and free: never hit the API in tests.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_correct_and_acceptable_quality_passes():
    result = assess(STRONG, "python")
    assert result.execution.all_passed
    assert result.verdict == "PASS"
    assert result.usage is None  # offline path carries no usage


def test_failing_tests_force_fail():
    # Prints a constant, so every test case mismatches → score 0% → FAIL.
    result = assess("print(0)\n", "python")
    assert result.verdict == "FAIL"
    assert result.score_pct == 0.0
    assert "wrong answer" in result.reason.lower()


@pytest.mark.skipif(not JAVA_AVAILABLE, reason="working JDK not installed")
def test_java_compile_error_fails():
    result = assess("public class Main { this is not java }", "java")
    assert result.verdict == "FAIL"
    assert result.execution.compile_error


@pytest.mark.skipif(not JAVA_AVAILABLE, reason="working JDK not installed")
def test_java_nonmain_classname_still_runs():
    # A correct submission whose public class is not "Main" must still compile
    # and pass — regression test for the fixed-filename bug.
    src = (
        "public class Solution {\n"
        "  public static void main(String[] a) throws Exception {\n"
        "    java.util.Scanner s = new java.util.Scanner(System.in);\n"
        "    int n = s.nextInt();\n"
        "    long best = Long.MIN_VALUE, cur = 0;\n"
        "    for (int i = 0; i < n; i++) { int x = s.nextInt(); cur = Math.max(x, cur + x); best = Math.max(best, cur); }\n"
        "    System.out.println(best);\n"
        "  }\n"
        "}\n"
    )
    result = assess(src, "java")
    assert result.execution.all_passed


def test_correct_but_slow_is_performance_fail(monkeypatch):
    # Correct on functional cases but TLE on the performance case → FAIL (too slow),
    # distinct from a wrong-answer failure.
    import assessment_agent.agent as agent_mod
    from assessment_agent.runner import ExecutionReport, TestOutcome

    outcomes = [
        TestOutcome("classic", "", "6", "6", True, category="correctness", weight=1.0),
        TestOutcome(
            "performance_large",
            "",
            "42",
            "",
            False,
            error="time limit exceeded (> 6.0s)",
            timed_out=True,
            category="performance",
            weight=6.0,
        ),
    ]
    monkeypatch.setattr(
        agent_mod,
        "run_submission",
        lambda *a, **k: ExecutionReport("python", None, outcomes),
    )
    result = assess("slow code", "python")
    # Earns 1 of 7 points (14%) → below the 90% bar → FAIL, flagged as too slow.
    assert result.verdict == "FAIL"
    assert result.score_pct < 90
    assert "tle" in result.reason.lower() or "slow" in result.reason.lower()


def test_missing_toolchain_is_error_not_fail(monkeypatch):
    # Simulate the runtime being absent: verdict must be ERROR, not FAIL.
    import assessment_agent.agent as agent_mod
    from assessment_agent.runner import ExecutionReport

    monkeypatch.setattr(
        agent_mod,
        "run_submission",
        lambda *a, **k: ExecutionReport("go", None, [], infra_error="runtime not installed"),
    )
    result = assess("package main", "go")
    assert result.verdict == "ERROR"
    assert "Could not evaluate" in result.reason
