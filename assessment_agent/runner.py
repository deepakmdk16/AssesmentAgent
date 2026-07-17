"""Execute a candidate submission against a question's test cases.

Each submission is compiled (if needed) and run once per test case in a shared
temp directory, with the test input fed on stdin and stdout compared against the
expected output. Each run is bounded by a per-language time limit, so a
correct-but-too-slow solution registers as a time-limit-exceeded (TLE) failure
rather than a wrong answer.

Every case runs serially, one child at a time. Performance cases need it (CPU
contention would inflate the timing that drives the TLE gate), and correctness
cases are held to it because the per-child resource limits are applied with
`preexec_fn`, which CPython documents as unsafe to use from a multithreaded
parent — a fork/exec deadlock there would hang a candidate's grade. Small
correctness inputs are cheap, so the throughput we give up is worth strictly
more as a guarantee. See `run_submission`.

Security note: this executes untrusted candidate code. Beyond the per-run timeout,
each child gets a best-effort output ceiling (`RLIMIT_FSIZE`) and — for languages
whose runtime doesn't reserve address space wholesale — a memory ceiling
(`RLIMIT_AS`); see `_apply_limits` and `Language.address_space_capped`, tune with
`ASSESS_MEM_LIMIT_MB` / `ASSESS_OUTPUT_LIMIT_MB`. Each child also leads its own
process group, so a timeout kills the whole tree rather than just the direct
child and can't leave orphans running on the worker (see `_kill_tree`).

Be precise about what the memory ceiling is worth, because it is easy to overrate:
`RLIMIT_AS` caps *address space*, not memory in use, and it is skipped entirely
for the JVM and Go (which reserve GBs of untouched virtual space at startup) and
silently ignored by macOS. So it bounds a runaway allocation in CPython on Linux
and little else.

These are defense-in-depth, NOT a sandbox. Nothing here bounds fork bombs,
network egress, or memory on the runtimes that opt out. For production, run this
inside a locked-down sandbox (container with no network, dropped capabilities,
and cgroups for memory + pids) — that is the layer that can actually express
"this submission gets N megabytes", which no rlimit here can.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import resource  # POSIX-only; used to cap child memory/output.
except ImportError:  # pragma: no cover - non-POSIX (e.g. Windows)
    resource = None  # type: ignore[assignment]

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
class RunResult:
    """The outcome of a single ad-hoc execution (the 'Run' path, not grading).

    Nothing is compared and there is no verdict: this is a candidate trying their
    own input, so we report only what the program did.
    """

    stdout: str
    stderr: str | None
    duration_s: float
    timed_out: bool
    compile_error: str | None = None
    infra_error: str | None = None


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


# Best-effort per-child resource ceilings (0 disables). Read at import; override
# with env vars. RLIMIT_AS caps address space (memory) and RLIMIT_FSIZE caps bytes
# written to stdout/stderr (which we redirect to files, so this bites).
#
# Deliberately NOT here: RLIMIT_NPROC. It counts processes per *UID*, not per
# process tree, so it cannot bound one submission: set high it does nothing, and
# set low it fails any child the login session's existing process count already
# exceeds — breaking legitimate submissions rather than fork bombs. Orphans are
# handled by the process-group kill (`_kill_tree`); a real fork-bomb brake needs
# the sandbox's pids controller (cgroups), per the module docstring.
_MEM_LIMIT_BYTES = int(os.environ.get("ASSESS_MEM_LIMIT_MB", "512")) * 1024 * 1024
_OUTPUT_LIMIT_BYTES = int(os.environ.get("ASSESS_OUTPUT_LIMIT_MB", "64")) * 1024 * 1024


def _apply_limits(cap_address_space: bool) -> None:
    """Set resource ceilings on the child just before exec (POSIX only).

    Best-effort: a platform that rejects a given limit (RLIMIT_AS is not honored
    on macOS) degrades to unlimited for that resource rather than failing the run.

    `cap_address_space` is False for languages whose runtime reserves far more
    address space than it uses (see `Language.address_space_capped`) — for them
    RLIMIT_AS blocks startup rather than bounding usage.

    IMPORTANT: this runs post-fork via `preexec_fn`, which CPython documents as
    unsafe in the presence of threads. `run_submission` therefore executes cases
    serially — do not reintroduce a thread pool around this without first moving
    the limits off `preexec_fn`.
    """
    limits = [(resource.RLIMIT_FSIZE, _OUTPUT_LIMIT_BYTES)]
    if cap_address_space:
        limits.append((resource.RLIMIT_AS, _MEM_LIMIT_BYTES))
    for res_id, cap in limits:
        if cap > 0:
            try:
                resource.setrlimit(res_id, (cap, cap))
            except (ValueError, OSError):
                pass


def _preexec_for(cap_address_space: bool):
    """Build the child's pre-exec hook, or None where rlimits don't exist.

    preexec_fn (not a shell wrapper) so subprocess still raises FileNotFoundError
    when the runtime is missing — that distinction drives the ERROR (inconclusive)
    verdict.
    """
    if resource is None:  # non-POSIX
        return None
    return lambda: _apply_limits(cap_address_space)

# Give each child its own session/process group so a timeout can kill the whole
# tree (see `_kill_tree`). POSIX-only, like the rlimits above.
_NEW_SESSION = os.name == "posix"


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child *and everything it spawned*.

    `Popen.kill` signals only the direct child, so a submission that forks would
    leave its children running on the worker after the case was scored. The child
    leads its own process group (`start_new_session`), so one killpg takes the
    whole tree. Falls back to killing just the child where process groups aren't
    available (non-POSIX) or the group is already gone.
    """
    if _NEW_SESSION:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def _run_case(
    run_cmd: list[str],
    workdir: Path,
    tc: TestCase,
    limit: float,
    *,
    cap_address_space: bool = True,
) -> TestOutcome | str:
    """Run one test case. Returns a TestOutcome, or an infra-error string when the
    runtime itself is missing (inconclusive — the caller must not treat it as a
    candidate failure).

    stdout/stderr go to temp files (not pipes) so RLIMIT_FSIZE caps a runaway
    print without the parent buffering it in memory; a child that exceeds the cap
    is killed (SIGXFSZ) and surfaces as a failing case, never as a worker OOM.

    Uses Popen rather than `subprocess.run` because `run`'s timeout path kills
    only the direct child; we need the whole process group (see `_kill_tree`).
    """
    start = time.perf_counter()
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        try:
            proc = subprocess.Popen(
                run_cmd,
                cwd=workdir,
                stdin=subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
                preexec_fn=_preexec_for(cap_address_space),
                start_new_session=_NEW_SESSION,
            )
        except FileNotFoundError as exc:
            return f"runtime not installed: {exc}"
        try:
            proc.communicate(input=tc.stdin.encode(), timeout=limit)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.communicate()  # reap, so the child can't linger as a zombie
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
        returncode = proc.returncode
        out_f.seek(0)
        err_f.seek(0)
        stdout = out_f.read().decode(errors="replace")
        stderr = err_f.read().decode(errors="replace")

    error = stderr.strip() if returncode != 0 else None
    if returncode != 0 and not error:
        # A signal (e.g. SIGXFSZ from the output cap, or OOM) leaves no stderr.
        error = f"runtime error (exit code {returncode})"
    passed = error is None and _normalize(stdout) == _normalize(tc.expected)
    return TestOutcome(
        tc.name,
        tc.stdin,
        tc.expected,
        stdout.strip(),
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

        # Cases run one at a time, in order. Two independent reasons, either of
        # which alone would force it:
        #   - performance cases must be isolated, so their measured duration (which
        #     drives the TLE gate) is uncontended and a fast solution can't be
        #     falsely timed out under load;
        #   - the per-child rlimits go on via `preexec_fn`, which CPython documents
        #     as unsafe from a multithreaded parent. Running correctness cases in a
        #     thread pool risked a fork/exec deadlock hanging a candidate's grade —
        #     a rare, unreproducible failure on the one thing that must be reliable.
        # Correctness inputs are small, so the throughput cost is minor. To make
        # them concurrent again, first move the limits off `preexec_fn` (e.g. a
        # process pool), don't just re-add threads.
        # The workdir is shared across cases, but only one child runs at a time, so
        # a candidate writing fixed-name files can't race itself.
        outcomes: list[TestOutcome] = []
        for tc in test_cases:
            result = _run_case(
                run_cmd, workdir, tc, limit, cap_address_space=lang.address_space_capped
            )
            if isinstance(result, str):  # runtime missing — inconclusive
                return ExecutionReport(language, None, [], infra_error=result)
            outcomes.append(result)

        return ExecutionReport(language, None, outcomes)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_once(
    source: str,
    language: str,
    stdin: str = "",
    *,
    time_limit_s: float = 2.0,
) -> RunResult:
    """Execute `source` once against ad-hoc `stdin` and report what it printed.

    This backs the candidate's "Run" button: they supply their own input and see
    their own output. Nothing is compared against an expected value and no
    verdict is produced — grading stays in `assess`.

    Implemented on top of `run_submission` with a single throwaway case so that
    compilation, the language-scaled time limit and the per-child resource caps
    behave exactly as they do in a graded run. The case's `expected` is unused
    (there is nothing to be right or wrong about), so its `passed` flag is
    ignored here.

    Note the deliberate exception to `questions.validate_question`, which forbids
    an empty `expected`: that rule protects *graded* questions, where an empty
    expected means a broken oracle. This case is never graded and never leaves
    this function, so it doesn't go through validation.
    """
    report = run_submission(
        source,
        language,
        (TestCase(name="__run__", stdin=stdin, expected=""),),
        time_limit_s=time_limit_s,
    )
    if report.infra_error is not None:
        return RunResult("", None, 0.0, False, infra_error=report.infra_error)
    if report.compile_error is not None:
        return RunResult("", None, 0.0, False, compile_error=report.compile_error)
    outcome = report.outcomes[0]
    return RunResult(
        stdout=outcome.actual,
        stderr=outcome.error,
        duration_s=outcome.duration_s,
        timed_out=outcome.timed_out,
    )
