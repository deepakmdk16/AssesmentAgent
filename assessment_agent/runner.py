"""Execute a candidate submission against a question's test cases.

Each submission is compiled (if needed) and run once per test case in an
isolated temp directory, with the test input fed on stdin and stdout compared
against the expected output. Each run is bounded by a per-language time limit,
so a correct-but-too-slow solution registers as a time-limit-exceeded (TLE)
failure rather than a wrong answer.

Security note: this executes untrusted candidate code with only a timeout for
protection. For production use, run this inside a locked-down sandbox
(container with no network, dropped capabilities, resource limits).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .constants import CORRECTNESS, Category
from .languages import LANGUAGES
from .questions import TestCase


@dataclass
class TestOutcome:
    name: str
    stdin: str
    expected: str
    actual: str
    passed: bool
    error: str | None = None
    duration_s: float = 0.0
    timed_out: bool = False
    category: Category = CORRECTNESS
    weight: float = 1.0


@dataclass
class ExecutionReport:
    language: str
    compile_error: str | None
    outcomes: list[TestOutcome]
    # Set when we could not run the submission at all (e.g. the compiler or
    # interpreter for the candidate's language is not installed). Distinct from
    # a candidate failure — the caller should report this as inconclusive.
    infra_error: str | None = None

    @property
    def all_passed(self) -> bool:
        return (
            self.infra_error is None
            and self.compile_error is None
            and bool(self.outcomes)
            and all(o.passed for o in self.outcomes)
        )

    @property
    def passed_count(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)

    def by_category(self, category: Category) -> list[TestOutcome]:
        return [o for o in self.outcomes if o.category == category]

    def category_passed(self, category: Category) -> bool:
        cases = self.by_category(category)
        return all(o.passed for o in cases)  # vacuously True if none

    def score(self) -> tuple[float, float, float]:
        """Return (points_earned, points_total, percentage) by test weight."""
        total = sum(o.weight for o in self.outcomes)
        earned = sum(o.weight for o in self.outcomes if o.passed)
        pct = 100.0 * earned / total if total else 0.0
        return earned, total, pct


def _normalize(text: str) -> str:
    """Trim trailing whitespace per line and surrounding blank lines."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def run_submission(
    source: str,
    language: str,
    test_cases: tuple[TestCase, ...],
    *,
    time_limit_s: float = 2.0,
    compile_timeout: int = 60,
) -> ExecutionReport:
    lang = LANGUAGES.get(language)
    if lang is None:
        supported = ", ".join(sorted(LANGUAGES))
        raise ValueError(f"Unsupported language {language!r}. Supported: {supported}")

    # Language-scaled limit (interpreted languages get more slack), as CP judges do.
    limit = max(0.1, time_limit_s * lang.time_multiplier)

    if lang.resolve is not None:
        source_filename, compile_cmd, run_cmd = lang.resolve(source)
    else:
        source_filename, compile_cmd, run_cmd = lang.source_filename, lang.compile, lang.run

    workdir = Path(tempfile.mkdtemp(prefix="assess_"))
    try:
        (workdir / source_filename).write_text(source)

        if compile_cmd is not None:
            try:
                proc = subprocess.run(
                    compile_cmd,
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=compile_timeout,
                )
            except FileNotFoundError as exc:
                return ExecutionReport(
                    language, None, [], infra_error=f"compiler not installed: {exc}"
                )
            except subprocess.TimeoutExpired:
                return ExecutionReport(language, "compilation timed out", [])
            if proc.returncode != 0:
                return ExecutionReport(language, proc.stderr.strip() or "compilation failed", [])

        outcomes: list[TestOutcome] = []
        for tc in test_cases:
            start = time.perf_counter()
            try:
                proc = subprocess.run(
                    run_cmd,
                    cwd=workdir,
                    input=tc.stdin,
                    capture_output=True,
                    text=True,
                    timeout=limit,
                )
            except FileNotFoundError as exc:
                # Runtime missing — inconclusive, not a candidate failure. Stop early.
                return ExecutionReport(
                    language, None, [], infra_error=f"runtime not installed: {exc}"
                )
            except subprocess.TimeoutExpired:
                outcomes.append(
                    TestOutcome(
                        tc.name,
                        tc.stdin,
                        tc.expected,
                        "",
                        False,
                        f"time limit exceeded (> {limit:.1f}s)",
                        duration_s=limit,
                        timed_out=True,
                        category=tc.category,
                        weight=tc.weight,
                    )
                )
                continue

            duration = time.perf_counter() - start
            error = proc.stderr.strip() if proc.returncode != 0 else None
            passed = error is None and _normalize(proc.stdout) == _normalize(tc.expected)
            outcomes.append(
                TestOutcome(
                    tc.name,
                    tc.stdin,
                    tc.expected,
                    proc.stdout.strip(),
                    passed,
                    error,
                    duration_s=duration,
                    category=tc.category,
                    weight=tc.weight,
                )
            )

        return ExecutionReport(language, None, outcomes)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
