"""Live nsjail integration — the argv-shape unit tests live in test_sandbox.py.

These actually build the jail, so on macOS (and any Linux without nsjail on PATH)
they SKIP. A skip is a green lie anywhere the jail is supposed to exist, so
.github/workflows/sandbox.yml runs them inside the production image with
ASSESS_REQUIRE_NSJAIL=1, which turns "cannot run" into a collection error — the same
fail-loudly-rather-than-green posture as evals.yml's ANTHROPIC_API_KEY guard.

Each ceiling is a differential pair: the same submission through the same
`run_submission`, differing only in the flag under test. The control arm proves the
jail is healthy and the effect is reachable, so a broken jail turns the pair red
instead of letting the capped arm pass for the wrong reason. What the flags mean is
in sandbox.py; what is and is not protected is in runner.py's docstring.
"""

import os
import shutil
import socket
import sys

import pytest

from assessment_agent import runner, sandbox
from assessment_agent.questions import TestCase
from assessment_agent.runner import run_submission

# The backend ASSESS_SANDBOX actually produced at import, captured before the autouse
# fixture below overwrites it — the only way to see what the image configured.
_IMPORTED_BACKEND = sandbox._BACKEND

_REQUIRED = os.environ.get("ASSESS_REQUIRE_NSJAIL") == "1"
_UNAVAILABLE = (
    "nsjail is Linux-only"
    if not sys.platform.startswith("linux")
    else "nsjail not installed"
    if shutil.which("nsjail") is None
    else None
)

# Fail, don't skip, where the jail was promised. Not CI=1 as the audit worded it:
# GitHub sets CI=true in the checkpoints job too, where there is legitimately no
# nsjail and skipping is correct — keying on it would red the main gate forever.
if _UNAVAILABLE and _REQUIRED:
    raise RuntimeError(
        f"ASSESS_REQUIRE_NSJAIL=1 but the live sandbox suite cannot run: {_UNAVAILABLE}"
    )

pytestmark = pytest.mark.skipif(_UNAVAILABLE is not None, reason=_UNAVAILABLE or "")


@pytest.fixture(autouse=True)
def force_nsjail(monkeypatch):
    monkeypatch.setattr(sandbox, "_BACKEND", "nsjail")


def test_correct_submission_still_passes_inside_the_jail():
    src = "import sys\nd = sys.stdin.read().split()\nprint(int(d[0]) + int(d[1]))\n"
    report = run_submission(src, "python", (TestCase("t", "2 3\n", "5"),))
    assert report.infra_error is None, report.infra_error
    assert report.all_passed


@pytest.mark.skipif(not _REQUIRED, reason="only the image job pins ASSESS_SANDBOX")
def test_the_image_selects_the_nsjail_backend_fail_closed():
    # Every other test here monkeypatches sandbox._BACKEND on, so all of them stay
    # green on an image that lost `ENV ASSESS_SANDBOX=nsjail` (Dockerfile:70). That
    # would not run untrusted code unsandboxed — "auto" still finds nsjail on PATH —
    # but it flips the posture from fail-closed to fail-open: a broken nsjail would
    # then pass through with a warning instead of raising. Nothing else notices.
    assert _IMPORTED_BACKEND == "nsjail", _IMPORTED_BACKEND


def test_the_jail_hands_the_child_only_path_and_home():
    # Doubles as the control for everything below: in passthrough the child inherits
    # pytest's whole environment, so an exact match is the live proof that nsjail
    # wrapped this child at all. It is also the only check on sandbox.py's claim that
    # host secrets (ANTHROPIC_API_KEY) stay out of untrusted code.
    #
    # LC_CTYPE is subtracted because it is the interpreter's, not the jail's: CPython
    # coerces the C locale and sets LC_CTYPE=C.UTF-8 in its own environ after exec
    # (PEP 538). Verified in the image — `nsjail ... -- /usr/bin/env` prints exactly
    # PATH and HOME, while python3 under the identical jail prints those plus
    # LC_CTYPE. Subtracting one known name keeps the match exact, so any variable
    # that really did survive the jail still fails this.
    src = "import os\nprint(' '.join(sorted(set(os.environ) - {'LC_CTYPE'})))\n"
    report = run_submission(src, "python", (TestCase("env", "", "HOME PATH"),))
    assert report.infra_error is None, report.infra_error
    assert report.all_passed, f"jail env is not just PATH+HOME: {report.outcomes[0].actual!r}"


def test_the_net_namespace_has_no_route_out():
    # The old version connected to 1.1.1.1 and took any OSError as proof, so it was
    # green on a runner with no egress at all — the jail contributing nothing. Same
    # probe, run twice against a listener in this process: with network=True nsjail
    # shares the container's netns (--disable_clone_newnet) and MUST reach it; by
    # default it gets a fresh, empty netns and must not. The fresh namespace is what
    # blocks — --iface_no_lo only takes down the jail's own loopback.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)  # connect() completes off the backlog; no accept loop needed
    port = listener.getsockname()[1]
    try:
        src = (
            "import socket\n"
            "try:\n"
            f"    socket.create_connection(('127.0.0.1', {port}), timeout=2)\n"
            "    print('NET_OK')\n"
            "except OSError:\n"
            "    print('NET_BLOCKED')\n"
        )
        real = sandbox._nsjail_wrap
        with pytest.MonkeyPatch.context() as mp:
            # Patch sandbox's own builder, not runner's imported alias: the seam stays
            # inside sandbox.py, the layer test_sandbox.py already patches.
            mp.setattr(
                sandbox,
                "_nsjail_wrap",
                lambda argv, workdir, **kw: real(argv, workdir, **{**kw, "network": True}),
            )
            allowed = run_submission(src, "python", (TestCase("net", "", "NET_OK"),))
        assert allowed.infra_error is None, allowed.infra_error
        assert allowed.all_passed, f"control arm never reached it: {allowed.outcomes[0].actual!r}"

        blocked = run_submission(src, "python", (TestCase("net", "", "NET_BLOCKED"),))
        assert blocked.infra_error is None, blocked.infra_error
        assert blocked.all_passed, f"egress not blocked: {blocked.outcomes[0].actual!r}"
    finally:
        listener.close()


def test_c_source_compiles_and_runs_inside_the_jail():
    # The compile step is a separate wrap (pids ceiling only) that no other live test
    # touches. gcc must write `program` into the bind-mounted workdir (proving
    # --bindmount is nsjail's rw -B, not -R), --rlimit_fsize inf must let the linker
    # write a whole binary, and './program' must resolve against --cwd.
    src = (
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "    int a, b;\n"
        '    if (scanf("%d %d", &a, &b) != 2) return 1;\n'
        '    printf("%d\\n", a + b);\n'
        "    return 0;\n"
        "}\n"
    )
    report = run_submission(src, "c", (TestCase("sum", "2 3\n", "5"),))
    assert report.infra_error is None, report.infra_error  # gcc missing from the image
    assert report.compile_error is None, report.compile_error  # jail refused the compiler
    assert report.all_passed, report.outcomes[0].error or report.outcomes[0].actual


# 384 MB of *resident* pages: b'x' * n memsets, where bytearray(n) would calloc and
# never fault the pages in, so the cgroup would never be charged.
_ALLOC_SRC = (
    # Flushed before the first allocation, so it survives a kill and the capped arm
    # can assert the child really started — otherwise an nsjail that failed to launch
    # at all (a malformed ceiling flag) would satisfy every negative assertion below.
    "print('go', flush=True)\n"
    "chunks = []\n"
    "for _ in range(48):\n"
    "    chunks.append(b'x' * (8 * 1024 * 1024))\n"
    "print('allocated')\n"
)


def test_the_memory_cgroup_kills_a_runaway_allocation(monkeypatch):
    # Arm A drops --cgroup_mem_max only (0 disables; --use_cgroupv2 and
    # --cgroup_pids_max stay, so the cgroup machinery is still exercised) and must
    # succeed; arm B caps at 128 MB and must not. Under an active sandbox the runner
    # applies no rlimits of its own, so the cgroup is the only thing that can stop
    # this — which makes the pair a test of cgroup-v2 delegation, not of RLIMIT_AS.
    case = (TestCase("alloc", "", "go\nallocated"),)

    monkeypatch.setattr(runner, "_MEM_LIMIT_BYTES", 0)
    uncapped = run_submission(_ALLOC_SRC, "python", case)
    assert uncapped.infra_error is None, uncapped.infra_error
    assert uncapped.all_passed, uncapped.outcomes[0].error

    monkeypatch.setattr(runner, "_MEM_LIMIT_BYTES", 128 * 1024 * 1024)
    capped = run_submission(_ALLOC_SRC, "python", case)
    assert capped.infra_error is None, capped.infra_error
    assert capped.compile_error is None, capped.compile_error
    # Deliberately not an exit code and not `error is not None`: whether the kernel
    # kills nsjail or the grandchild, and whether CPython prints a MemoryError first,
    # is not something the code promises (A22 is the open item about a cgroup kill
    # being indistinguishable). timed_out rules out the wall clock; actual rules out
    # success; and A21's leaked nsjail warnings can't satisfy either.
    outcome = capped.outcomes[0]
    assert outcome.timed_out is False, "the wall clock fired — this proves nothing about the cgroup"
    assert outcome.passed is False
    assert outcome.actual.startswith("go"), f"the jail never ran the child: {outcome.actual!r}"
    assert "allocated" not in outcome.actual, outcome.actual


# A bounded 64 attempts, never a real fork bomb. The program reports the verdict
# itself, so a crash produces no output and fails rather than passing quietly.
# Children are left unreaped on purpose: zombies hold pid slots, so the ceiling bites
# in milliseconds. os._exit skips the flush, so no child duplicates the output.
_FORK_SRC = (
    "import os\n"
    "kids = 0\n"
    "for _ in range(64):\n"
    "    try:\n"
    "        pid = os.fork()\n"
    "    except OSError:\n"
    "        print('CAPPED')\n"
    "        break\n"
    "    if pid == 0:\n"
    "        os._exit(0)\n"
    "    kids += 1\n"
    "else:\n"
    "    print(f'UNCAPPED {kids}')\n"
)


def test_the_pids_cgroup_stops_unbounded_forking(monkeypatch):
    # This is the only brake there is: runner.py deliberately never sets RLIMIT_NPROC
    # (it counts per-UID, not per-tree), so the passthrough path cannot bound a
    # process tree at all — exactly the gap the cgroup closes.
    monkeypatch.setattr(runner, "_PIDS_MAX", 0)
    uncapped = run_submission(_FORK_SRC, "python", (TestCase("fork", "", "UNCAPPED 64"),))
    assert uncapped.infra_error is None, uncapped.infra_error
    assert uncapped.all_passed, f"control arm did not fork freely: {uncapped.outcomes[0].actual!r}"

    monkeypatch.setattr(runner, "_PIDS_MAX", 8)
    capped = run_submission(_FORK_SRC, "python", (TestCase("fork", "", "CAPPED"),))
    assert capped.infra_error is None, capped.infra_error
    assert capped.outcomes[0].timed_out is False, "the fork loop hung instead of being refused"
    assert capped.all_passed, f"pids ceiling not enforced: {capped.outcomes[0].actual!r}"


def test_the_jailed_root_filesystem_is_read_only():
    # sandbox.py claims `--chroot /` gives the host FS read-only with only the
    # bind-mounted workdir writable — the property that keeps candidate code out of
    # the container it runs in, and the thing that makes --bindmount meaningful.
    # Nothing has ever asserted it. The workdir write comes first and is unguarded, so
    # a jail where *everything* is read-only fails here instead of passing on the
    # wrong OSError. EROFS applies regardless of uid, so this survives A07's uid remap.
    src = (
        "open('scratch', 'w').write('ok')\n"
        "try:\n"
        "    open('/etc/assess_probe', 'w').write('x')\n"
        "    print('ROOT_WRITABLE')\n"
        "except OSError:\n"
        "    print('ROOT_READ_ONLY')\n"
    )
    report = run_submission(src, "python", (TestCase("fs", "", "ROOT_READ_ONLY"),))
    assert report.infra_error is None, report.infra_error
    assert report.all_passed, f"jail root was writable: {report.outcomes[0].actual!r}"
