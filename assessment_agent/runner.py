"""Execute a candidate submission against a question's test cases.

Each submission is compiled (if needed) and run once per test case in an
isolated temp directory, with the test input fed on stdin and stdout compared
against the expected output.

Security note: this executes untrusted candidate code with only a timeout for
protection. For production use, run this inside a locked-down sandbox
(container with no network, dropped capabilities, resource limits).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

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


def _normalize(text: str) -> str:
    """Trim trailing whitespace per line and surrounding blank lines."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _java_entrypoint(source: str) -> str:
    """Java requires the file name to match the public class, so derive it."""
    match = re.search(r"public\s+class\s+([A-Za-z_]\w*)", source) or re.search(
        r"\bclass\s+([A-Za-z_]\w*)", source
    )
    return match.group(1) if match else "Main"


def run_submission(
    source: str,
    language: str,
    test_cases: tuple[TestCase, ...],
    *,
    run_timeout: int = 10,
    compile_timeout: int = 60,
) -> ExecutionReport:
    lang = LANGUAGES.get(language)
    if lang is None:
        supported = ", ".join(sorted(LANGUAGES))
        raise ValueError(f"Unsupported language {language!r}. Supported: {supported}")

    source_filename = lang.source_filename
    compile_cmd = lang.compile
    run_cmd = lang.run
    if language == "java":
        cls = _java_entrypoint(source)
        source_filename = f"{cls}.java"
        compile_cmd = ["javac", source_filename]
        run_cmd = ["java", cls]

    workdir = Path(tempfile.mkdtemp(prefix="assess_"))
    try:
        (workdir / source_filename).write_text(source)

        if compile_cmd is not None:
            try:
                proc = subprocess.run(
                    compile_cmd, cwd=workdir, capture_output=True, text=True,
                    timeout=compile_timeout,
                )
            except FileNotFoundError as exc:
                return ExecutionReport(language, None, [], infra_error=f"compiler not installed: {exc}")
            except subprocess.TimeoutExpired:
                return ExecutionReport(language, "compilation timed out", [])
            if proc.returncode != 0:
                return ExecutionReport(language, proc.stderr.strip() or "compilation failed", [])

        outcomes: list[TestOutcome] = []
        for tc in test_cases:
            try:
                proc = subprocess.run(
                    run_cmd, cwd=workdir, input=tc.stdin, capture_output=True, text=True,
                    timeout=run_timeout,
                )
            except FileNotFoundError as exc:
                # Runtime missing — inconclusive, not a candidate failure. Stop early.
                return ExecutionReport(language, None, [], infra_error=f"runtime not installed: {exc}")
            except subprocess.TimeoutExpired:
                outcomes.append(
                    TestOutcome(tc.name, tc.stdin, tc.expected, "", False,
                                f"timed out after {run_timeout}s")
                )
                continue

            error = proc.stderr.strip() if proc.returncode != 0 else None
            passed = error is None and _normalize(proc.stdout) == _normalize(tc.expected)
            outcomes.append(
                TestOutcome(tc.name, tc.stdin, tc.expected, proc.stdout.strip(), passed, error)
            )

        return ExecutionReport(language, None, outcomes)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
