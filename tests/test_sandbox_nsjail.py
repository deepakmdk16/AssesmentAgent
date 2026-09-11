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

import glob
import os
import platform
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

import pytest

from assessment_agent import runner, sandbox
from assessment_agent.languages import LANGUAGES
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


def _only_outcome(report):
    """The single outcome of a healthy run — asserts the jail itself did not fail."""
    assert report.infra_error is None, report.infra_error
    assert report.compile_error is None, report.compile_error
    assert report.outcomes, "the jail produced no outcome"
    return report.outcomes[0]


def _proc_status(pid="self"):
    """/proc/<pid>/status as a {field: value} dict."""
    fields = {}
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def _marker_in_proc(marker: str) -> bool:
    """True if any live process (this pid namespace) carries `marker` in its argv."""
    needle = marker.encode()
    for entry in glob.glob("/proc/*/cmdline"):
        try:
            with open(entry, "rb") as f:
                if needle in f.read():
                    return True
        except OSError:
            continue  # the process exited between glob and open
    return False


def _zombie_count() -> int:
    """Processes in state Z in this pid namespace. Their cmdline is empty, so
    `_marker_in_proc` cannot see them; the state is field 3 of /proc/<pid>/stat, the
    first field after the last ')' (the comm in between may itself contain one)."""
    count = 0
    for entry in glob.glob("/proc/[0-9]*/stat"):
        try:
            with open(entry) as f:
                count += f.read().rpartition(")")[2].split()[0] == "Z"
        except (OSError, IndexError):
            continue  # the process exited between glob and open
    return count


def test_correct_submission_still_passes_inside_the_jail():
    src = "import sys\nd = sys.stdin.read().split()\nprint(int(d[0]) + int(d[1]))\n"
    report = run_submission(src, "python", (TestCase("t", "2 3\n", "5"),))
    assert report.infra_error is None, report.infra_error
    assert report.all_passed


@pytest.mark.skipif(not _REQUIRED, reason="only the image job pins ASSESS_SANDBOX")
def test_the_image_selects_the_nsjail_backend_fail_closed():
    # Every other test here monkeypatches sandbox._BACKEND on, so all of them stay
    # green on an image that lost the Dockerfile's `ENV ASSESS_SANDBOX=nsjail`. That
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
    # success; and nsjail's own warning lines on stderr can't satisfy either.
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
    # sandbox.py mounts `--chroot /` read-only, with only the bind-mounted workdir (and
    # the per-jail tmpfs hides) writable — the property that keeps candidate code out
    # of the container it runs in, and the thing that makes --bindmount meaningful.
    #
    # A bare "some OSError" would prove nothing: the jailed child shares the worker's
    # non-root uid, so a write into root-owned /etc fails EACCES on ANY mount,
    # read-only or not. To probe read-ONLY-ness we need a path the child *could* write
    # if the mount allowed it: the worker pre-creates a worker-owned 0777 directory on
    # the container root fs (under /var/tmp, outside every hidden path and the workdir),
    # so only the read-only remount can stop the write — errno EROFS, not EACCES. The
    # workdir write first is the control: a jail where *everything* is read-only fails
    # there instead of passing on the wrong errno.
    probe_dir = tempfile.mkdtemp(dir="/var/tmp", prefix="ro_probe_")
    os.chmod(probe_dir, 0o777)
    try:
        src = (
            "import errno\n"
            "open('scratch', 'w').write('ok')\n"
            f"probe = {probe_dir + '/marker'!r}\n"
            "try:\n"
            "    open(probe, 'w').write('x')\n"
            "    print('ROOT_WRITABLE')\n"
            "except OSError as e:\n"
            "    print('ROOT_READ_ONLY' if e.errno == errno.EROFS\n"
            "          else 'errno:' + (errno.errorcode.get(e.errno) or str(e.errno)))\n"
        )
        report = run_submission(src, "python", (TestCase("fs", "", "ROOT_READ_ONLY"),))
        assert report.infra_error is None, report.infra_error
        assert report.all_passed, f"root fs not read-only: {report.outcomes[0].actual!r}"
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# A07's least-privilege posture (non-root worker, seccomp, hidden paths, cpu
# ceiling) and the holes it closed. Each hole-closing test FAILS on the pre-A07
# posture (root under --privileged, no seccomp, /tmp + /sys/fs/cgroup + /app +
# /dev/shm all visible) for the reason its comment states. The workdir-cleanup tests
# are different: they guard regressions the non-root posture itself introduced (a
# child that owns its workdir can lock the worker out of it), so root passed them.
# --------------------------------------------------------------------------- #

# a+b on stdin -> stdout, one program per language. Java's file/class name is derived
# from `public class Main`, so the source must declare exactly that.
_AB_SRC = {
    "python": "import sys\na, b = map(int, sys.stdin.read().split())\nprint(a + b)\n",
    "javascript": (
        "const d = require('fs').readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\n"
        "console.log(d[0] + d[1]);\n"
    ),
    "ruby": "a, b = STDIN.read.split.map(&:to_i)\nputs a + b\n",
    "go": (
        "package main\n"
        'import "fmt"\n'
        "func main() {\n"
        "\tvar a, b int\n"
        "\tfmt.Scan(&a, &b)\n"
        "\tfmt.Println(a + b)\n"
        "}\n"
    ),
    "java": (
        "import java.util.*;\n"
        "public class Main {\n"
        "    public static void main(String[] x) {\n"
        "        Scanner s = new Scanner(System.in);\n"
        "        System.out.println(s.nextInt() + s.nextInt());\n"
        "    }\n"
        "}\n"
    ),
    "c": (
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "    int a, b;\n"
        '    if (scanf("%d %d", &a, &b) != 2) return 1;\n'
        '    printf("%d\\n", a + b);\n'
        "    return 0;\n"
        "}\n"
    ),
    "cpp": (
        "#include <iostream>\n"
        "int main() {\n"
        "    long a, b;\n"
        "    std::cin >> a >> b;\n"
        '    std::cout << a + b << "\\n";\n'
        "}\n"
    ),
    "rust": (
        "use std::io::Read;\n"
        "fn main() {\n"
        "    let mut s = String::new();\n"
        "    std::io::stdin().read_to_string(&mut s).unwrap();\n"
        "    let v: Vec<i64> = s.split_whitespace().map(|x| x.parse().unwrap()).collect();\n"
        '    println!("{}", v[0] + v[1]);\n'
        "}\n"
    ),
}


@pytest.mark.parametrize("language", sorted(LANGUAGES))
def test_every_language_compiles_and_runs_in_the_jail(language):
    # Before A07, go and rust never ran in the jail at all: the read-only /tmp broke
    # their build dirs ("read-only file system") and go additionally exhausted
    # nsjail's default RLIMIT_NOFILE=32 ("pipe2: too many open files"). The tmpfs over
    # /tmp and --rlimit_nofile 1024 are what let every toolchain compile and run.
    src = _AB_SRC[language]
    report = run_submission(src, language, (TestCase("sum", "2 3\n", "5"),), time_limit_s=15.0)
    assert report.infra_error is None, report.infra_error  # toolchain missing from the image
    assert report.compile_error is None, report.compile_error  # jail refused the compiler
    outcome = report.outcomes[0]
    assert report.all_passed, outcome.error or outcome.actual


def test_the_child_cannot_read_other_workdirs_or_grader_source():
    # Two grading-integrity holes in a bare `--chroot /`: other candidates'
    # /tmp/assess_* workdirs are readable to a jail running as the worker's uid, and so
    # is the grader's questions.py — the built-in answer keys. The tmpfs over /tmp
    # (exposing only this run's bind-mounted workdir) and over the grader root close
    # both. The sibling dir is created by the worker, so without the tmpfs it lands on
    # the shared /tmp the jail can see.
    graders = [str(Path(sandbox.__file__).resolve().parent / "questions.py")]
    if _REQUIRED:
        # The image's literal path too. The path above comes from sandbox.__file__, the
        # same way sandbox.py derives the root it hides, so a bug there would move the
        # probe along with the hide. The worker can read it outside the jail — the
        # control that the path still exists.
        literal = "/app/assessment_agent/questions.py"
        assert os.access(literal, os.R_OK), f"{literal} moved; update this probe"
        graders.append(literal)
    sibling = tempfile.mkdtemp(prefix="assess_")
    try:
        with open(os.path.join(sibling, "secret.txt"), "w") as f:
            f.write("OTHER-CANDIDATE-SECRET")
        secret_path = os.path.join(sibling, "secret.txt")
        src = (
            "import os\n"
            f"secret, graders = {secret_path!r}, {graders!r}\n"
            "def readable(p):\n"
            "    try:\n"
            "        with open(p) as f:\n"
            "            f.read(1)\n"
            "        return True\n"
            "    except OSError:\n"
            "        return False\n"
            "try:\n"
            "    entries = os.listdir('/tmp')\n"
            "except OSError:\n"
            "    entries = []\n"
            "here = os.path.basename(os.getcwd())\n"
            "siblings = [e for e in entries if e.startswith('assess_') and e != here]\n"
            "print('secret=' + ('yes' if readable(secret) else 'no'))\n"
            "print('grader=' + ('yes' if any(readable(g) for g in graders) else 'no'))\n"
            "print('siblings=' + ('yes' if siblings else 'no'))\n"
        )
        expected = "secret=no\ngrader=no\nsiblings=no"
        report = run_submission(src, "python", (TestCase("iso", "", expected),))
        assert report.infra_error is None, report.infra_error
        assert report.all_passed, (
            f"jail leaked other workdirs / grader source: {report.outcomes[0].actual!r}"
        )
    finally:
        shutil.rmtree(sibling, ignore_errors=True)


# Same 384 MB resident allocation as _ALLOC_SRC, but the child first tries to lift its
# own cgroup ceiling — exactly the escape a writable /sys/fs/cgroup allows: it
# reads its leaf cgroup name from /proc/self/cgroup (nsjail's NSJAIL.<pid>) and writes
# 'max' into that leaf's memory.max / swap / pids.max, then allocates past the cap.
# With /sys/fs/cgroup hidden behind a per-jail tmpfs those writes land in an empty
# tmpfs (or fail), so the real ceiling — set by nsjail from outside the jail — bites.
_CGROUP_DEFEAT_SRC = (
    "print('go', flush=True)\n"
    "leaf = ''\n"
    "try:\n"
    # /proc/self/cgroup is '0::<path>'; the last component is nsjail's own leaf cgroup.
    "    leaf = open('/proc/self/cgroup').read().strip().split('/')[-1]\n"
    "except OSError:\n"
    "    pass\n"
    "for base in ('/sys/fs/cgroup', '/sys/fs/cgroup/' + leaf):\n"
    "    for name in ('memory.max', 'memory.swap.max', 'pids.max'):\n"
    "        try:\n"
    "            open(base + '/' + name, 'w').write('max')\n"
    "        except OSError:\n"
    "            pass\n"
    "chunks = []\n"
    "for _ in range(48):\n"
    "    chunks.append(b'x' * (8 * 1024 * 1024))\n"
    "print('allocated')\n"
)


def test_the_child_cannot_defeat_its_cgroup_ceilings(monkeypatch):
    # Differential like the memory test, but the payload actively attacks the ceiling
    # before allocating. Control arm (cap disabled) proves the allocation is reachable;
    # capped arm at 128 MB must still be stopped despite the child writing 'max' into
    # every memory/pids limit it can reach.
    case = (TestCase("defeat", "", "go\nallocated"),)

    monkeypatch.setattr(runner, "_MEM_LIMIT_BYTES", 0)
    uncapped = run_submission(_CGROUP_DEFEAT_SRC, "python", case)
    assert uncapped.infra_error is None, uncapped.infra_error
    assert uncapped.all_passed, uncapped.outcomes[0].error or uncapped.outcomes[0].actual

    monkeypatch.setattr(runner, "_MEM_LIMIT_BYTES", 128 * 1024 * 1024)
    capped = run_submission(_CGROUP_DEFEAT_SRC, "python", case)
    assert capped.infra_error is None, capped.infra_error
    outcome = capped.outcomes[0]
    assert outcome.timed_out is False, "the wall clock fired — this proves nothing about the cgroup"
    assert outcome.actual.startswith("go"), f"the jail never ran the child: {outcome.actual!r}"
    assert "allocated" not in outcome.actual, f"child defeated the memory cap: {outcome.actual!r}"


def test_the_jailed_child_is_unprivileged():
    # Before A07 the jailed child was root in the global user namespace (nsjail even
    # warned about it — see the stderr test below). Now the non-root worker makes nsjail
    # map its own uid through: the child is unprivileged with an empty capability set,
    # no_new_privs, and seccomp in filter mode. Seccomp == 2 alone would be satisfied
    # by the container-level filter the worker already carries, so the child must
    # carry more filters than the worker: nsjail's own kafel filter stacked on top.
    src = (
        "d = {}\n"
        "for line in open('/proc/self/status'):\n"
        "    k, _, v = line.partition(':')\n"
        "    d[k.strip()] = v.strip()\n"
        "uid = d['Uid'].split()[0]\n"
        "ok = (\n"
        "    uid != '0'\n"
        "    and d['CapEff'].strip('0') == ''\n"
        "    and d['CapBnd'].strip('0') == ''\n"
        "    and d['NoNewPrivs'] == '1'\n"
        "    and d['Seccomp'] == '2'\n"
        ")\n"
        "print('UNPRIV' if ok else f\"PRIV uid={uid} eff={d['CapEff']} bnd={d['CapBnd']} \"\n"
        "      f\"nnp={d['NoNewPrivs']} seccomp={d['Seccomp']}\")\n"
        "print(d.get('Seccomp_filters', '0'))\n"
    )
    outcome = _only_outcome(run_submission(src, "python", (TestCase("id", "", ""),)))
    verdict, _, child_filters = outcome.actual.partition("\n")
    assert verdict == "UNPRIV", outcome.actual
    worker_filters = int(_proc_status().get("Seccomp_filters", "0"))
    assert int(child_filters) > worker_filters, (
        f"no jail filter on top of the worker's: child {child_filters}, worker {worker_filters}"
    )


@pytest.mark.skipif(not _REQUIRED, reason="only the image job runs the shipping posture")
def test_the_worker_running_the_suite_is_not_root():
    # Proves CI exercises the posture that ships: the suite itself runs as the
    # unprivileged worker, not a privileged stand-in. If this reds, every child-level
    # assertion above was made from the wrong vantage point.
    assert os.geteuid() != 0, "the suite is running as root — not the posture that ships"
    status = _proc_status()
    assert status["CapEff"].strip("0") == "", (
        f"worker holds capabilities: CapEff={status['CapEff']}"
    )
    assert status["CapBnd"].strip("0") == "", (
        f"worker bounding set non-empty: CapBnd={status['CapBnd']}"
    )
    assert status["NoNewPrivs"] == "1", f"NoNewPrivs={status['NoNewPrivs']}"


# ctypes probes for four denied syscalls, numbers chosen by the container's arch.
# keyctl and clone run before ptrace(TRACEME): once traced, the clone child's SIGCHLD
# stops us for a tracer (our parent, nsjail) that never resumes us, and the allow-all
# arm hangs to a TLE. All three run before unshare, which would swap our user
# namespace.
_SECCOMP_PROBE = r"""
import ctypes, errno, os, platform
libc = ctypes.CDLL(None, use_errno=True)
NR = {
    "aarch64": {"unshare": 97, "ptrace": 117, "keyctl": 219, "clone": 220},
    "x86_64": {"unshare": 272, "ptrace": 101, "keyctl": 250, "clone": 56},
}[platform.machine()]


def tag(nr, *args):
    ctypes.set_errno(0)
    r = libc.syscall(ctypes.c_long(nr), *[ctypes.c_long(a) for a in args])
    if r == 0 and nr == NR["clone"]:
        os._exit(0)  # the clone child: a fork-style copy of us, with a zero stack arg
    if r > 0 and nr == NR["clone"]:
        os.waitpid(r, 0)
    return "OK" if r >= 0 else (errno.errorcode.get(ctypes.get_errno()) or "ERR")


k = tag(NR["keyctl"], 0, -3, 0)  # keyctl(GET_KEYRING_ID, KEY_SPEC_SESSION_KEYRING)
c = tag(NR["clone"], 0x10000000 | 17, 0, 0, 0, 0)  # clone(CLONE_NEWUSER | SIGCHLD)
p = tag(NR["ptrace"], 0, 0, 0, 0)  # ptrace(PTRACE_TRACEME)
u = tag(NR["unshare"], 0x10000000)  # unshare(CLONE_NEWUSER)
print(f"keyctl={k} clone={c} ptrace={p} unshare={u}")
"""


def test_seccomp_denies_privileged_syscalls():
    # No control arm is possible without disabling seccomp, so the control patches
    # nsjail's kafel policy builder to allow-all. Two syscalls are reachability probes:
    # the container-level Docker seccomp permits ptrace(PTRACE_TRACEME), and clone with
    # CLONE_NEWUSER (nsjail itself needs that to build the jail), so with the kafel
    # deny-list gone both succeed — proving the capped arm's EPERM for each is the kafel
    # layer at work, not an unreachable syscall. clone is the one that matters: it is
    # the second door to the new user namespace unshare is denied for, and kafel shuts
    # it by flag, not by name. unshare/keyctl are blocked at BOTH layers (the Docker
    # profile also denies them for a non-SYS_ADMIN caller), so they stay EPERM even
    # here and cannot serve as the control.
    with pytest.MonkeyPatch.context() as mp:
        # A scoped patch, not monkeypatch.undo(): undo would also revert the autouse
        # _BACKEND patch and run the capped arm below outside the jail.
        mp.setattr(sandbox, "_seccomp_policy", lambda: "DEFAULT ALLOW")
        allowed = _only_outcome(run_submission(_SECCOMP_PROBE, "python", (TestCase("sc", "", ""),)))
    assert "ptrace=OK" in allowed.actual, f"ptrace not reachable even allow-all: {allowed.actual!r}"
    assert "clone=OK" in allowed.actual, f"clone not reachable even allow-all: {allowed.actual!r}"

    denied = _only_outcome(run_submission(_SECCOMP_PROBE, "python", (TestCase("sc", "", ""),)))
    assert denied.actual == "keyctl=EPERM clone=EPERM ptrace=EPERM unshare=EPERM", denied.actual


# Each x32 call (nr | 0x40000000) runs in a forked child, so a bad-arch KILL action
# reads as BLOCKED instead of taking the output with it. The child exits with its
# errno, so the second line records the raw outcome (errno name or signal number).
_X32_PROBE = r"""
import ctypes, errno, os
libc = ctypes.CDLL(None, use_errno=True)
raw = []


def tag(nr, *args):
    pid = os.fork()
    if pid == 0:
        r = libc.syscall(ctypes.c_long(0x40000000 | nr), *[ctypes.c_long(a) for a in args])
        os._exit(0 if r >= 0 else (ctypes.get_errno() or 255))
    _, status = os.waitpid(pid, 0)
    if os.WIFSIGNALED(status):
        raw.append(f"signal {os.WTERMSIG(status)}")
    else:
        code = os.WEXITSTATUS(status)
        raw.append(errno.errorcode.get(code, str(code)) if code else "0")
    return "BYPASS" if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0 else "BLOCKED"


print(f"ptrace={tag(521, 0, 0, 0, 0)} unshare={tag(272, 0x10000000)}")
print("raw: " + " ".join(raw))
"""


@pytest.mark.skipif(platform.machine() != "x86_64", reason="x32 ABI is an x86_64-only bypass")
def test_the_x32_abi_cannot_bypass_the_seccomp_deny_list():
    # kafel matches by native syscall number only, so on an x86_64 kernel with the x32
    # ABI a candidate could re-issue a denied call as (0x40000000 | nr) and slip past
    # the jail's seccomp — unless the container-level Docker profile rejects the
    # non-native ABI. ptrace (x32 number 521) is the arm that proves the profile does:
    # Docker's default allows ptrace (the seccomp test's control arm shows it), so with
    # upstream's X32 sub-architecture still in the archMap an x32 PTRACE_TRACEME would
    # get through. unshare is denied at the Docker layer on every ABI anyway.
    #
    # Kill or errno both count as BLOCKED, since which one the profile picks is not the
    # point. But this can pass vacuously: a kernel built without x32 refuses the call
    # itself (ENOSYS) whatever the profile says, and this test does not check which
    # kernel the runner has. The raw line shows how each call was refused, but an
    # ENOSYS there does not say which layer refused it. The kernel-independent guard is
    # test_sandbox.py's unit test on deploy/seccomp.json's archMap. (aarch64 has no x32,
    # so the local run skips this; it runs on the x86_64 CI runner.)
    outcome = _only_outcome(run_submission(_X32_PROBE, "python", (TestCase("x32", "", ""),)))
    verdict = outcome.actual.splitlines()[0] if outcome.actual else ""
    assert verdict == "ptrace=BLOCKED unshare=BLOCKED", outcome.actual


def test_a_timed_out_submission_leaves_no_process_behind():
    # The runner's killpg SIGKILL of nsjail must reach through nsjail's PID namespace,
    # or a submission could fork a long-lived process that outlives its grade. The
    # payload spawns /bin/sleep with a unique marker arg and spins; after the TLE, no
    # live process in the container may still carry the marker.
    #
    # Dead is not enough, though: the killed jail's processes are orphaned to the
    # container's PID 1, and only a PID 1 that reaps (tini, from deploy/entrypoint.sh)
    # clears them. Otherwise each TLE leaves a zombie holding a slot in the container's
    # pids limit — one a marker scan cannot see, since a zombie's cmdline is empty. So
    # the zombie count must not grow either.
    zombies_before = _zombie_count()
    marker = "4242.4242"
    src = (
        f"import subprocess\nsubprocess.Popen(['/bin/sleep', {marker!r}])\nwhile True:\n    pass\n"
    )
    report = run_submission(src, "python", (TestCase("tle", "", "x"),), time_limit_s=0.5)
    assert report.infra_error is None, report.infra_error
    assert report.outcomes[0].timed_out is True, report.outcomes[0].error
    # The kernel kills and PID 1 reaps the tree asynchronously; give it a bounded moment.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and (
        _marker_in_proc(marker) or _zombie_count() > zombies_before
    ):
        time.sleep(0.2)
    assert not _marker_in_proc(marker), "a child survived the timeout kill"
    zombies_after = _zombie_count()
    assert zombies_after <= zombies_before, (
        f"the TLE left zombies nobody reaps: {zombies_before} -> {zombies_after}"
    )


def _workdir_and_rest(outcome) -> tuple[str, str]:
    """Split output whose first line is the child's os.getcwd(): its workdir.

    The jail binds the workdir at the worker's own path, so that is where mkdtemp put
    it, under the worker's TMPDIR. Asserted, so a bind that moved could not make a
    "the workdir is gone" check pass vacuously on a path that never existed here.
    """
    workdir, _, rest = outcome.actual.partition("\n")
    tmp = os.path.realpath(tempfile.gettempdir())
    assert os.path.dirname(workdir) == tmp, f"not a workdir under {tmp}: {outcome.actual[:300]!r}"
    return workdir, rest


def test_a_workdir_the_child_locked_is_still_removed():
    # The jailed child runs as the worker's own uid, so it owns its workdir and can
    # chmod it, or any subdir, to 0. An unprivileged rmtree(ignore_errors=True) can't
    # list either, and silently left every such workdir behind on the worker's disk.
    # The runner must restore access first, so the workdir the child reports is gone.
    src = (
        "import os\n"
        "print(os.getcwd())\n"
        "os.makedirs('d')\n"
        "for name in ('a.txt', 'b.txt', 'c.txt'):\n"
        "    open(os.path.join('d', name), 'w').write('x')\n"
        "os.chmod('d', 0)\n"
        "os.chmod('.', 0)\n"
        "print('LOCKED')\n"
    )
    outcome = _only_outcome(run_submission(src, "python", (TestCase("lock", "", ""),)))
    workdir, status = _workdir_and_rest(outcome)
    # The control: the child really locked its workdir, so the cleanup had work to do.
    assert status == "LOCKED", outcome.error or outcome.actual
    assert not os.path.exists(workdir), f"the locked workdir outlived the run: {workdir}"


# 17 names of 255 chars (the per-name maximum), reached by chdir, so the leaf's full
# path is far past PATH_MAX (4096): no path-based call can even name it. The leaf is
# mode 0 and holds 4 MiB, so the cleanup must both reach it and restore it.
_DEEP_NAMES = 17
_PAST_PATH_MAX_SRC = (
    "import os\n"
    "print(os.getcwd(), flush=True)\n"
    f"for _ in range({_DEEP_NAMES}):\n"
    "    os.mkdir('n' * 255)\n"
    "    os.chdir('n' * 255)\n"
    "os.mkdir('leaf')\n"
    "for i in range(4):\n"
    "    with open(os.path.join('leaf', str(i)), 'wb') as f:\n"
    "        f.write(b'x' * (1024 * 1024))\n"
    "os.chmod('leaf', 0)\n"
    "print('BUILT')\n"
)

# 1200 levels of 'a': short enough to stay under PATH_MAX, deep enough that a
# recursive tree walk overflows Python's default recursion limit (1000).
_RECURSION_DEEP_SRC = (
    "import os\n"
    "print(os.getcwd(), flush=True)\n"
    "for _ in range(1200):\n"
    "    os.mkdir('a')\n"
    "    os.chdir('a')\n"
    "print('BUILT')\n"
)


def test_a_locked_leaf_past_path_max_is_still_removed():
    # The chmod-then-rmtree cleanup works by path. Past PATH_MAX every such call fails
    # ENAMETOOLONG, so the mode-0 leaf was never restored, rmtree could not empty it,
    # and the whole workdir, 4 MiB and all, stayed behind on the worker's disk.
    outcome = _only_outcome(
        run_submission(_PAST_PATH_MAX_SRC, "python", (TestCase("deep", "", ""),), time_limit_s=10.0)
    )
    workdir, status = _workdir_and_rest(outcome)
    assert status == "BUILT", outcome.error or outcome.actual  # the tree really exists
    assert len(workdir) + _DEEP_NAMES * 256 > 4096, "the chain no longer crosses PATH_MAX"
    assert not os.path.exists(workdir), f"the deep workdir outlived the run: {workdir}"


def test_a_very_deep_workdir_neither_raises_nor_survives():
    # A recursive walk (shutil.rmtree is one on the image's Python 3.11) dies with
    # RecursionError here. That is not an OSError, so no onerror catches it: it escaped
    # run_submission and turned a clean grade into a crash — and left the tree behind.
    outcome = _only_outcome(
        run_submission(
            _RECURSION_DEEP_SRC, "python", (TestCase("deep", "", ""),), time_limit_s=10.0
        )
    )
    workdir, status = _workdir_and_rest(outcome)
    assert status == "BUILT", outcome.error or outcome.actual  # the tree really exists
    assert not os.path.exists(workdir), f"the deep workdir outlived the run: {workdir}"


_OUTPUT_SRC = (
    "import sys\nsys.stdout.write('x' * (4 * 1024 * 1024))\nsys.stdout.flush()\nprint('DONE')\n"
)


def test_the_output_ceiling_is_live(monkeypatch):
    # Only the bytes->MB conversion was unit-pinned; nothing proved a real run is
    # actually stopped. Differential on the output cap: 4 MiB completes uncapped and
    # is cut off (SIGXFSZ, no 'DONE') at a 1 MiB cap.
    monkeypatch.setattr(runner, "_OUTPUT_LIMIT_BYTES", 0)
    uncapped = _only_outcome(run_submission(_OUTPUT_SRC, "python", (TestCase("out", "", ""),)))
    assert "DONE" in uncapped.actual, "control arm did not finish writing 4 MiB uncapped"

    monkeypatch.setattr(runner, "_OUTPUT_LIMIT_BYTES", 1024 * 1024)
    capped = _only_outcome(run_submission(_OUTPUT_SRC, "python", (TestCase("out", "", ""),)))
    assert capped.timed_out is False, "the wall clock fired — this proves nothing about the cap"
    assert "DONE" not in capped.actual, "output not capped: the program ran to completion"


def test_nsjail_does_not_leak_into_candidate_stderr():
    # Under the pre-A07 root-in-global-userns posture nsjail printed
    # "[W] ... UID/EUID=0 in the global user namespace" to the child's stderr, which
    # reached the candidate and polluted stderr comparisons. Under the non-root worker
    # that warning is gone: a program that writes exactly 'boom' to stderr and exits
    # non-zero must surface exactly 'boom' as the case error, nothing more.
    src = "import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n"
    report = run_submission(src, "python", (TestCase("err", "", ""),))
    assert report.infra_error is None, report.infra_error
    assert report.outcomes[0].error == "boom", repr(report.outcomes[0].error)


_MARKER = "persist_probe_marker"


def _walk_and_act(action: str) -> str:
    # Walk the jail's filesystem (skip /proc and /sys — huge, and cgroupfs/procfs
    # reject arbitrary file creation anyway), not following symlinked dirs. `action`
    # is either dropping a marker into every writable directory, or counting the
    # markers a later, fresh jail can still see.
    return (
        "import os\n"
        f"marker = {_MARKER!r}\n"
        "hits = 0\n"
        "for root, dirs, files in os.walk('/', topdown=True):\n"
        "    if root == '/proc' or root.startswith('/proc/') or root == '/sys' or root.startswith('/sys/'):\n"
        "        dirs[:] = []\n"
        "        continue\n"
        "    dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]\n"
        f"    {action}\n"
        "print('HITS', hits)\n"
    )


_PERSIST_DROP = _walk_and_act(
    "if os.access(root, os.W_OK):\n"
    "        try:\n"
    "            open(os.path.join(root, marker), 'w').close()\n"
    "            hits += 1\n"
    "        except OSError:\n"
    "            pass"
)
_PERSIST_FIND = _walk_and_act("hits += 1 if marker in files else 0")


def test_no_persistent_cross_submission_channel():
    # Every writable directory a candidate can reach must be per-jail, or one candidate
    # can leave state (or exfiltrated data) for the next. Unhidden, /dev/shm is a shared
    # writable mount that survives between jails; here /dev/shm, /dev/mqueue, /tmp,
    # /home, /sys/fs/cgroup and the grader root are all per-jail tmpfs, so run 2 finds
    # nothing run 1 left. Raise the case limit — walking / takes a few seconds.
    dropped = _only_outcome(
        run_submission(_PERSIST_DROP, "python", (TestCase("drop", "", ""),), time_limit_s=20.0)
    )
    assert dropped.actual.startswith("HITS "), dropped.actual  # the walk completed

    found = _only_outcome(
        run_submission(_PERSIST_FIND, "python", (TestCase("find", "", ""),), time_limit_s=20.0)
    )
    assert found.actual == "HITS 0", f"a marker from a previous jail survived: {found.actual!r}"


_CPU_SPIN_SRC = (
    "import time\n"
    "end = time.time() + 2.0\n"
    "n = 0\n"
    "while time.time() < end:\n"
    "    n += 1\n"
    "print(round(time.process_time(), 3))\n"
)


def test_the_cpu_ceiling_throttles_cpu_time(monkeypatch):
    # --cgroup_cpu_ms_per_sec caps CPU time per wall second. A single-threaded spinner
    # bounded by wall clock (~2 s) accrues ~2 s of CPU uncapped; at 500 ms/s the cgroup
    # throttles it to markedly less. Differential on the runner's ceiling.
    monkeypatch.setattr(runner, "_CPU_MS_PER_SEC", 0)
    uncapped = _only_outcome(
        run_submission(_CPU_SPIN_SRC, "python", (TestCase("cpu", "", ""),), time_limit_s=10.0)
    )
    uncapped_cpu = float(uncapped.actual)
    assert uncapped_cpu > 1.0, f"control arm barely used CPU: {uncapped_cpu}"

    monkeypatch.setattr(runner, "_CPU_MS_PER_SEC", 500)
    capped = _only_outcome(
        run_submission(_CPU_SPIN_SRC, "python", (TestCase("cpu", "", ""),), time_limit_s=10.0)
    )
    capped_cpu = float(capped.actual)
    assert capped_cpu < 0.75 * uncapped_cpu, (
        f"cpu not throttled: capped={capped_cpu} uncapped={uncapped_cpu}"
    )
