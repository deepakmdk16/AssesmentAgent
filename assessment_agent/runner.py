"""Execute a candidate submission against a question's test cases.

Each submission is compiled (if needed) and run once per test case in a shared
temp directory, with the test input fed on stdin and stdout compared against the
expected output. Each run is bounded by a per-language time limit, so a
correct-but-too-slow solution registers as a time-limit-exceeded (TLE) failure
rather than a wrong answer.

Correctness cases run concurrently for throughput; performance cases run in
isolation so CPU contention can't inflate the timing that drives the TLE gate
(see `run_submission`). Output comparison — and therefore the verdict — is
unaffected by parallelism.

Security note: this executes untrusted candidate code with only a timeout for
protection. For production use, run this inside a locked-down sandbox
(container with no network, dropped capabilities, resource limits).
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .constants import CORRECTNESS, PERFORMANCE, Category
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

    @property
    def execution_failed(self) -> bool:
        """True when the submission could not be run meaningfully: it did not
        compile, the toolchain was missing, or every test case errored at
        runtime. A wrong-but-running or correct-but-slow (TLE) submission is NOT
        a failure here — those still merit a quality report."""
        if self.compile_error is not None or self.infra_error is not None:
            return True
        return bool(self.outcomes) and all(o.error is not None for o in self.outcomes)

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


def _run_case(run_cmd: list[str], workdir: Path, tc: TestCase, limit: float) -> TestOutcome | str:
    """Run one test case. Returns a TestOutcome, or an infra-error string when the
    runtime itself is missing (inconclusive — the caller must not treat it as a
    candidate failure)."""
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
        return f"runtime not installed: {exc}"
    except subprocess.TimeoutExpired:
        return TestOutcome(
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

    duration = time.perf_counter() - start
    error = proc.stderr.strip() if proc.returncode != 0 else None
    passed = error is None and _normalize(proc.stdout) == _normalize(tc.expected)
    return TestOutcome(
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

        # Execution is split by category so parallelism never corrupts the grade:
        #   - correctness cases run concurrently (bounded) — their inputs are small
        #     and their timing is not gated, so CPU contention is harmless;
        #   - performance cases run isolated, one at a time with nothing else
        #     running, so their measured duration (which drives the TLE gate) is
        #     trustworthy and a fast solution can't be falsely timed out under load.
        # They run in separate phases, so the two never overlap. The workdir is
        # shared, so a candidate that writes fixed-name files could race during the
        # correctness phase — acceptable under the existing untrusted-code caveat.
        outcomes: list[TestOutcome | None] = [None] * len(test_cases)
        corr_idx = [i for i, tc in enumerate(test_cases) if tc.category != PERFORMANCE]
        perf_idx = [i for i, tc in enumerate(test_cases) if tc.category == PERFORMANCE]

        # Phase 1: correctness cases in parallel (bounded to avoid heavy contention).
        if corr_idx:
            max_workers = min(len(corr_idx), os.cpu_count() or 1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_run_case, run_cmd, workdir, test_cases[i], limit): i
                    for i in corr_idx
                }
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if isinstance(result, str):  # runtime missing — inconclusive
                        return ExecutionReport(language, None, [], infra_error=result)
                    outcomes[futures[future]] = result

        # Phase 2: performance cases isolated, for clean (uncontended) timing.
        for i in perf_idx:
            result = _run_case(run_cmd, workdir, test_cases[i], limit)
            if isinstance(result, str):
                return ExecutionReport(language, None, [], infra_error=result)
            outcomes[i] = result

        return ExecutionReport(language, None, [o for o in outcomes if o is not None])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
