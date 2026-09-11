import errno
import logging
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

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


def test_submissions_are_serialised_process_wide():
    # A03: two graders on two threads must not overlap — the performance case's
    # timing (which decides TLE, hence the verdict) is only meaningful
    # uncontended, and the passthrough path must not fork with preexec_fn from
    # several threads at once. Two 0.3 s programs serialised take >= 0.6 s.
    import threading

    src = "import time; time.sleep(0.3); print('ok')"
    cases = (TestCase(name="c", stdin="", expected="ok\n"),)
    ends: list[float] = []
    starts: list[float] = []

    def work() -> None:
        starts.append(time.perf_counter())
        report = run_submission(src, "python", cases, time_limit_s=5)
        assert report.infra_error is None and report.outcomes[0].passed
        ends.append(time.perf_counter())

    threads = [threading.Thread(target=work) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max(ends) - min(starts) >= 0.55


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc to reach the compile step")
def test_the_compile_step_is_jailed_with_only_the_pids_and_cpu_ceilings():
    """The compile wrap is deliberately unlike the run wrap, and nothing pinned it.

    No memory ceiling (compilers legitimately use a lot; `compile_timeout` bounds
    them) and no output ceiling (nsjail's own 1 MB default would truncate the binary
    gcc writes). Sharing the run step's kwargs — or dropping the wrap entirely, which
    has no runtime symptom at all — would break every compiled language in production
    while the rest of the suite stayed green.
    """
    calls: list[dict] = []
    real = runner.sandbox_wrap

    def record(argv, workdir, **kw):
        calls.append(kw)
        return real(argv, workdir, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner, "sandbox_wrap", record)
        run_submission("int main(void){return 0;}\n", "c", (TestCase("t", "", ""),))

    assert len(calls) == 2, f"expected a compile wrap then a run wrap, got {calls}"
    assert calls[0] == {"pids_max": runner._PIDS_MAX, "cpu_ms_per_sec": runner._CPU_MS_PER_SEC}, (
        calls[0]
    )
    assert calls[1] == {
        "mem_bytes": runner._MEM_LIMIT_BYTES,
        "pids_max": runner._PIDS_MAX,
        "fsize_bytes": runner._OUTPUT_LIMIT_BYTES,
        "cpu_ms_per_sec": runner._CPU_MS_PER_SEC,
    }, calls[1]


@pytest.mark.parametrize(
    "language,prefix", [("c", "compiler not installed: "), ("python", "runtime not installed: ")]
)
def test_a_toolchain_missing_from_the_jail_path_is_an_infra_error(monkeypatch, language, prefix):
    # sandbox.wrap raises this when argv[0] isn't on the jail's PATH. Exec'ing the
    # bare name instead would fail inside the jail and grade as the candidate's error.
    def missing(argv, workdir, **kw):
        raise FileNotFoundError(errno.ENOENT, "not on the jail PATH", argv[0])

    monkeypatch.setattr(runner, "sandbox_wrap", missing)
    report = run_submission("irrelevant\n", language, (tc("", "x"),))
    assert (report.infra_error or "").startswith(prefix), report.infra_error
    assert "not on the jail PATH" in report.infra_error
    assert report.compile_error is None and report.outcomes == []


def test_cleanup_removes_a_workdir_the_child_locked_without_chmodding_through_links(tmp_path):
    """The child is the worker's own uid, so it can chmod its workdir to 0 and leave
    the unprivileged worker unable to delete it; leftovers would pile up on /tmp.
    Cleanup restores the modes, but must not chmod through a planted symlink."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("k")
    outside.chmod(0o750)
    src = (
        "import os, sys\n"
        "os.symlink(sys.stdin.read().strip(), 'link')\n"
        "os.makedirs('d/e')\n"
        "open('d/f.txt', 'w').write('x')\n"
        "open('d/e/g.txt', 'w').write('y')\n"
        "os.chmod('d/e', 0)\n"
        "os.chmod('d', 0)\n"
        "print(os.getcwd())\n"
        "os.chmod('.', 0)\n"
    )
    report = run_submission(src, "python", (tc(str(outside), "irrelevant"),))
    assert report.outcomes[0].error is None, report.outcomes[0].error
    workdir = Path(report.outcomes[0].actual)
    assert workdir.name.startswith("assess_"), workdir
    assert not os.path.lexists(workdir), "the locked workdir outlived the submission"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o750
    assert (outside / "keep").read_text() == "k"


_DIR = os.O_RDONLY | os.O_DIRECTORY


def _dig(top: Path, names: list[str]) -> int:
    """mkdir a chain under `top` by name, fd-relatively (so the test itself never
    builds a path past PATH_MAX); return an fd for the deepest directory."""
    fd = os.open(top, _DIR)
    for name in names:
        os.mkdir(name, dir_fd=fd)
        child = os.open(name, _DIR, dir_fd=fd)
        os.close(fd)
        fd = child
    return fd


def test_cleanup_removes_a_locked_chain_longer_than_path_max(tmp_path):
    """Past PATH_MAX every path-based call fails with ENAMETOOLONG, so a path-based
    chmod pass never reached the locked leaf, rmtree couldn't list it, and its payload
    stayed on /tmp for good — +200 MB a submission, live."""
    workdir = tmp_path / "assess_long"
    workdir.mkdir()
    names = ["n" * 250] * 20
    assert len("/".join(names)) > os.pathconf(workdir, "PC_PATH_MAX")
    fd = _dig(workdir, names)
    try:
        os.mkdir("x", dir_fd=fd)
        x = os.open("x", _DIR, dir_fd=fd)
        for i in range(4):
            f = os.open(f"f{i}", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=x)
            os.write(f, b"payload")
            os.close(f)
        os.close(x)
        os.chmod("x", 0, dir_fd=fd)
    finally:
        os.close(fd)
    runner._remove_workdir(workdir)
    assert not os.path.lexists(workdir)


def _walk_one_frame_per_level(fd: int) -> None:
    # The shape of shutil.rmtree before 3.12: one Python frame per directory level.
    with os.scandir(fd) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                sub = os.open(entry.name, _DIR, dir_fd=fd)
                try:
                    _walk_one_frame_per_level(sub)
                finally:
                    os.close(sub)


def test_cleanup_removes_a_chain_deeper_than_the_recursion_limit(tmp_path):
    # shutil.rmtree recursed once per level on 3.11, so a candidate's 1000+-deep
    # chain raised RecursionError out of run_submission's finally, replacing the report.
    # The limit is lowered so a short chain crosses it (1500 levels at the default limit
    # took ~11s). 3.12's rmtree no longer recurses, so a walk with 3.11's shape is the
    # canary proving the chain is still deep enough to break a recursive cleanup.
    workdir = tmp_path / "assess_deep"
    workdir.mkdir()
    os.close(_dig(workdir, ["a"] * 300))
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(200)
    try:
        top = os.open(workdir, _DIR)
        try:
            with pytest.raises(RecursionError):
                _walk_one_frame_per_level(top)
        finally:
            os.close(top)
        runner._remove_workdir(workdir)
    finally:
        sys.setrecursionlimit(limit)
    assert not os.path.lexists(workdir)


def test_cleanup_lists_each_directory_once(tmp_path):
    # Linear in the tree: a walk that rescanned a directory after each subdirectory
    # would be quadratic in a wide one. The bound is generous, to stay unflaky.
    workdir = tmp_path / "assess_wide"
    workdir.mkdir()
    for i in range(20000):
        (workdir / f"f{i}").touch()
    for i in range(2000):
        (workdir / f"d{i}").mkdir()
    start = time.perf_counter()
    runner._remove_workdir(workdir)
    elapsed = time.perf_counter() - start
    assert not os.path.lexists(workdir)
    assert elapsed < 15, elapsed


@pytest.mark.parametrize("call", ["unlink", "rmdir"])
def test_cleanup_logs_one_escaped_line_for_what_it_cannot_remove(
    caplog, monkeypatch, tmp_path, call
):
    """One warning per workdir, however many entries fail: a line per entry let a
    candidate flood the log, with its names verbatim, so a newline forged a line."""
    workdir = tmp_path / "assess_stuck"
    workdir.mkdir()
    stuck = "stuck\nWARNING forged"
    (workdir / stuck).mkdir() if call == "rmdir" else (workdir / stuck).touch()
    for i in range(20):
        (workdir / f"f{i}").touch()
    real = getattr(os, call)

    def refuse(path, *args, **kwargs):
        if path == stuck:
            raise PermissionError(errno.EACCES, "Permission denied", path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, call, refuse)
    with caplog.at_level(logging.WARNING, logger=runner.__name__):
        runner._remove_workdir(workdir)
    assert len(caplog.records) == 1, caplog.records
    line = caplog.records[0].getMessage()
    # The stuck entry, then the workdir it keeps non-empty.
    assert "could not remove 2 entries" in line, line
    assert repr(stuck) in line and "\n" not in line, line
    assert os.listdir(workdir) == [stuck]


def test_cleanup_logs_a_leftover_instead_of_raising(caplog, tmp_path):
    gone = tmp_path / "assess_gone"  # vanished before cleanup: logged, not raised
    with caplog.at_level(logging.WARNING, logger=runner.__name__):
        runner._remove_workdir(gone)
    assert len(caplog.records) == 1 and str(gone) in caplog.text
