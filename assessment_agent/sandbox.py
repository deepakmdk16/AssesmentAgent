"""Wrap an untrusted child's argv in an OS sandbox before it is exec'd.

`runner.py` builds an argv (compile step, then one run per test case) and hands it
to `subprocess`. This module is the single seam where that argv is wrapped in a
jail, so nothing else in the pipeline (scoring, the TLE gate, the process-group
kill, the temp workdir) has to change.

Why this exists: the per-child rlimits in `runner.py` are defense-in-depth, not a
sandbox. They cannot bound a fork bomb, network egress, or memory on runtimes that
reserve address space wholesale (the JVM, Go). Only a cgroup can say "this
submission gets N megabytes / M processes", and only a network namespace can say
"no egress". See `runner.py`'s docstring and STATUS.md for the full rationale and
the two rlimits already tried and rejected.

Backends, selected by the `ASSESS_SANDBOX` env var (read at import):

- ``none`` — passthrough: the argv is returned unchanged. This is exactly today's
  behavior; the rlimits + killpg underneath still apply. Use on macOS/dev and in
  the unit-test suite, where no Linux jail exists.
- ``nsjail`` — force the nsjail backend; raise `SandboxUnavailableError` at wrap time if
  the binary or platform is missing, so a misconfigured production fails loudly
  rather than silently running untrusted code wide open.
- ``auto`` (default) — nsjail when it is available (Linux + on PATH), else
  passthrough with a one-time warning. Lets the same image run sandboxed in prod
  and unsandboxed on a dev laptop without config.

nsjail is the choice because a single argv wrap expresses every requirement at
once — a fresh network namespace (no egress), all capabilities dropped, a seccomp
deny-list, cgroup-v2 memory + pids + CPU ceilings, and a read-only view of the
container with everything shared hidden — and it keeps the runner's per-case serial
model (and therefore the performance-case timing) intact.

The jail is half the boundary. The other half is the container it runs in: the
worker itself runs as an unprivileged user with no capabilities, which is what makes
the jail's defaults (see `_nsjail_wrap`) correct. That posture — the entrypoint that
drops root, the AppArmor and seccomp profiles, and the `docker run` flags — lives in
the Dockerfile and deploy/, with deploy/docker-run.flags as the source of truth.
"""

from __future__ import annotations

import errno
import logging
import os
import platform
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

log = logging.getLogger(__name__)


class SandboxUnavailableError(RuntimeError):
    """A sandbox backend was explicitly requested but cannot run here."""


# Read once at import, mirroring runner.py's rlimit env convention. Tests monkeypatch
# this attribute (and `_nsjail_available`) rather than re-reading the environment.
_BACKEND = os.environ.get("ASSESS_SANDBOX", "auto").lower()

_warned_unsandboxed = False

# The only PATH the jailed child gets (nsjail clears the environment), and therefore
# the only PATH argv[0] may be resolved against — see `_nsjail_wrap`.
_JAIL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# The grader's own install root (/app in the image): its questions.py carries the
# built-in answer keys, so the child must not be able to read it.
_GRADER_ROOT = Path(__file__).resolve().parent.parent

# Paths replaced by an empty private tmpfs inside the jail (/tmp gets its own sized
# one, see `_nsjail_wrap`). Only /dev/shm and /sys/fs/cgroup can be assumed on a
# Linux host; the rest are hidden only where they exist, so nsjail never has to
# mkdir a mount point on the host root.
_HIDDEN = ("/dev/shm", "/dev/mqueue", "/sys/fs/cgroup", "/home", str(_GRADER_ROOT))
_HIDDEN_ASSUMED = frozenset({"/dev/shm", "/sys/fs/cgroup"})
# Headroom for compiler temp files and Go's build dir; the pages are charged to the
# jail's memory cgroup, so this cannot be used to exceed the memory ceiling.
_TMP_TMPFS_BYTES = 64 * 1024 * 1024
# nsjail's default RLIMIT_NOFILE is 32, which `go run` exhausts ("pipe2: too many
# open files").
_NOFILE = 1024

# CLONE_NEWNS|NEWCGROUP|NEWUTS|NEWIPC|NEWUSER|NEWPID|NEWNET: clone() must not be a
# second door to what `unshare` is denied for.
_CLONE_NS_MASK = 0x7E020000
# Denied with EPERM. Every name is in kafel's table for both x86_64 and aarch64.
_SECCOMP_DENY = (
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "mount",
    "pivot_root",
    "chroot",
    "unshare",
    "setns",
    "keyctl",
    "add_key",
    "request_key",
    "bpf",
    "perf_event_open",
    "userfaultfd",
    "kexec_load",
    "init_module",
    "finit_module",
    "delete_module",
    "reboot",
    "swapon",
    "swapoff",
    "acct",
    "name_to_handle_at",
    "open_by_handle_at",
)
# x86_64 only: kafel rejects these names on aarch64 ("Undefined identifier"), which
# would fail every jail. "umount" is kafel's name for x86_64's umount2.
_SECCOMP_DENY_X86_64 = ("iopl", "ioperm", "kexec_file_load", "umount")
# Missing from kafel's tables, so by number (the same on x86_64 and aarch64):
# io_uring_setup/enter/register, open_tree, move_mount, fsopen, fsconfig, fsmount,
# fspick, clone3, mount_setattr. ENOSYS, not EPERM: glibc falls back from clone3 to
# clone only on ENOSYS, so EPERM there would fail every pthread_create.
_SECCOMP_ENOSYS_NRS = (425, 426, 427, 428, 429, 430, 431, 432, 433, 435, 442)


def _seccomp_policy() -> str:
    """The kafel policy nsjail installs on the child (compile and run steps).

    A deny-list, not an allow-list: the eight toolchains' syscall footprints differ
    and an allow-list miss fails an honest submission in a way no candidate can
    debug. What it denies is the kernel attack surface a grader never needs —
    namespace creation, ptrace, keyrings, bpf, module/kexec, the new mount API,
    io_uring. ERRNO rather than KILL so a candidate sees a legible EPERM instead of
    an exit 159 plus nsjail's violation lines in their stderr.

    Kafel matches native-ABI syscall numbers only; on an x86_64 kernel with x32
    enabled, ``nr | 0x40000000`` would slip past this list. The container's seccomp
    profile (deploy/seccomp.json) rejects non-native ABIs, which closes that.
    """
    deny = list(_SECCOMP_DENY)
    if platform.machine() == "x86_64":
        deny += _SECCOMP_DENY_X86_64
    deny.append(f"clone {{ (clone_flags & {_CLONE_NS_MASK:#x}) != 0 }}")
    enosys = ", ".join(f"SYSCALL[{n}]" for n in _SECCOMP_ENOSYS_NRS)
    return f"ERRNO(1) {{ {', '.join(deny)} }} ERRNO(38) {{ {enosys} }} DEFAULT ALLOW"


def _nsjail_available() -> bool:
    """nsjail needs a Linux host and the binary on PATH; it is Linux-only."""
    return sys.platform.startswith("linux") and shutil.which("nsjail") is not None


def _nsjail_wrap(
    argv: Sequence[str],
    workdir: Path,
    *,
    network: bool,
    mem_bytes: int,
    pids_max: int,
    fsize_bytes: int,
    cpu_ms_per_sec: int,
) -> list[str]:
    """Build the nsjail invocation that runs `argv` in `workdir`.

    The host filesystem is mounted read-only (so the toolchain is visible) with the
    submission's temp dir bind-mounted read-write. Isolation comes from the fresh
    net/pid/mount namespaces, the cgroup ceilings, the seccomp policy, and nsjail
    dropping every capability. There are no uid-mapping flags on purpose: the worker
    is not root (see the Dockerfile), and nsjail run by a non-root caller maps the
    caller's uid onto itself in the jail's user namespace — so the child is the
    worker's uid inside and out, holds no capability outside its own namespaces, and
    can write the 0700 workdir the worker created. We pass ``--time_limit 0`` and let
    the runner's own wall-clock timeout + process-group kill govern lifetime, so
    nsjail (our direct child, in its own session) is torn down by `_kill_tree`.

    Because the child shares the worker's uid outside the jail, everything the worker
    owns or can write has to be hidden, not merely permission-checked: /tmp (other
    candidates' workdirs), the grader's install root (the answer keys), /home, the
    shared writable /dev/shm and /dev/mqueue (they would outlive the jail), and
    /sys/fs/cgroup (the delegated tree, where the child could raise its own
    memory.max / pids.max). The ``--chroot /`` read-only remount is not recursive, so
    a rw submount of the container stays rw inside unless hidden like this. The
    hiding mounts precede the workdir bind: nsjail mounts in argv order and the
    workdir lives under /tmp, so the bind must land on top of the fresh tmpfs.

    nsjail owns *all* the resource limits when it wraps the child, so the runner
    skips its own preexec rlimits under the sandbox (see `is_active`); applying both
    fights — nsjail raising RLIMIT_AS back up hits EPERM against the runner's lower
    cap. Address space is left unbounded (``--rlimit_as inf``) on purpose: memory is
    bounded by the cgroup instead, which — unlike an address-space rlimit — actually
    holds for the JVM/Go. The output ceiling stays as ``--rlimit_fsize``.

    A `mem_bytes`/`pids_max`/`fsize_bytes`/`cpu_ms_per_sec` of 0 omits that ceiling
    (0 disables), matching the rlimit convention in runner.py.

    All of this is exercised by `.github/workflows/sandbox.yml`, which builds the
    image and runs `test_sandbox_nsjail.py` inside it with the flags in
    deploy/docker-run.flags — the posture that ships. It runs on PRs from this repo
    and on push to main; fork PRs are skipped (A35).
    """
    cmd = [
        "nsjail",
        "--quiet",
        "--mode",
        "o",  # execute once, then exit
        "--chroot",
        "/",  # host FS, read-only by default
        # Hidden paths — BEFORE the workdir bind (see the docstring).
        "--mount",
        f"none:/tmp:tmpfs:size={_TMP_TMPFS_BYTES}",
    ]
    for path in _HIDDEN:
        if path in _HIDDEN_ASSUMED or os.path.isdir(path):
            cmd += ["--tmpfsmount", path]
    cmd += [
        "--bindmount",  # nsjail's -B: bind rw (--bindmount_ro/-R is the ro one)
        str(workdir),  # the candidate's temp dir stays writable
        "--cwd",
        str(workdir),
        # nsjail clears the environment by default — a feature here, since it keeps
        # host secrets (e.g. ANTHROPIC_API_KEY) out of untrusted code. But that
        # leaves no PATH to resolve a bare `python3`/`node`/…, and no writable HOME
        # for toolchains that cache there, so inject just those two.
        "--env",
        f"PATH={_JAIL_PATH}",
        "--env",
        f"HOME={workdir}",
        "--time_limit",
        "0",  # runner enforces the timeout + killpg; don't double-govern
        "--rlimit_as",
        "inf",  # memory bounded by the cgroup below, not address space (JVM/Go-safe)
        "--rlimit_nofile",
        str(_NOFILE),
        "--seccomp_string",
        _seccomp_policy(),
    ]
    # Output ceiling (0 disables). nsjail's own default is 1 MB, which would also
    # cap a compiler writing its binary, so callers that don't want a cap pass 0.
    if fsize_bytes > 0:
        cmd += ["--rlimit_fsize", str(max(1, fsize_bytes // (1024 * 1024)))]
    else:
        cmd += ["--rlimit_fsize", "inf"]
    # Isolate the network unless explicitly allowed. Without --disable_clone_newnet
    # nsjail puts the child in a fresh net namespace; --iface_no_lo drops loopback
    # too, so there is no reachable interface at all == no egress.
    cmd.append("--disable_clone_newnet" if network else "--iface_no_lo")

    if mem_bytes > 0 or pids_max > 0 or cpu_ms_per_sec > 0:
        cmd += ["--use_cgroupv2", "--cgroupv2_mount", "/sys/fs/cgroup"]
        if mem_bytes > 0:
            cmd += ["--cgroup_mem_max", str(mem_bytes)]
            # memory.max alone only pushes anonymous pages out to swap wherever the
            # host has any (GitHub's runners ship a 4 GB swapfile), so a submission
            # over the ceiling gets slow rather than stopped. Capping swap at 0 is
            # what makes --cgroup_mem_max mean what this module's docstring says.
            cmd += ["--cgroup_mem_swap_max", "0"]
        if pids_max > 0:
            cmd += ["--cgroup_pids_max", str(pids_max)]
        if cpu_ms_per_sec > 0:
            # nsjail writes cpu.max as "<N*1000> 1000000": N ms of CPU per 1 s period.
            cmd += ["--cgroup_cpu_ms_per_sec", str(cpu_ms_per_sec)]

    # nsjail execve()s argv[0] literally — unlike execvp it does NOT search PATH —
    # so a bare command name ("python3", "node", "java") must be resolved to an
    # absolute path first, against the jail's PATH rather than the worker's: under
    # `uv run` the worker's PATH leads with /app/.venv/bin, which the jail hides, so
    # `python3` would resolve to a path the child cannot exec. A path that already
    # contains "/" (the compiled "./program" run relative to --cwd) is left untouched.
    # A name that doesn't resolve raises the same FileNotFoundError subprocess would,
    # so the runner reports a missing toolchain (infra_error), not a candidate failure.
    argv = list(argv)
    if "/" not in argv[0]:
        resolved = shutil.which(argv[0], path=_JAIL_PATH)
        if resolved is None:
            raise FileNotFoundError(errno.ENOENT, "not on the jail PATH", argv[0])
        argv[0] = resolved

    cmd.append("--")
    cmd += argv
    return cmd


def _warn_unsandboxed_once() -> None:
    global _warned_unsandboxed
    if not _warned_unsandboxed:
        _warned_unsandboxed = True
        if sys.platform.startswith("linux"):
            log.warning(
                "ASSESS_SANDBOX=auto but nsjail is not on PATH; running untrusted "
                "submissions WITHOUT an OS sandbox (rlimits + killpg only). Install "
                "nsjail or set ASSESS_SANDBOX=nsjail to require it."
            )


def is_active() -> bool:
    """Whether a real jail will wrap the child (vs. a passthrough).

    The runner uses this to decide whether to skip its own preexec rlimits — under
    the sandbox nsjail owns every resource limit, and applying both fights (see
    `_nsjail_wrap`).
    """
    if _BACKEND == "nsjail":
        return True  # forced; wrap() raises later if the binary is actually missing
    if _BACKEND == "auto":
        return _nsjail_available()
    return False  # "none" or unknown


def wrap(
    argv: Sequence[str],
    workdir: Path,
    *,
    network: bool = False,
    mem_bytes: int = 0,
    pids_max: int = 0,
    fsize_bytes: int = 0,
    cpu_ms_per_sec: int = 0,
) -> list[str]:
    """Return the argv to actually exec — wrapped in the selected sandbox, or the
    argv unchanged when the backend is passthrough.

    Raises `SandboxUnavailableError` when ``ASSESS_SANDBOX`` names a backend that cannot
    run here (so production surfaces a misconfiguration instead of silently
    executing untrusted code unsandboxed), or names an unknown backend.
    """
    if _BACKEND == "none":
        return list(argv)
    if _BACKEND == "nsjail":
        if not _nsjail_available():
            raise SandboxUnavailableError(
                "ASSESS_SANDBOX=nsjail but nsjail is unavailable "
                f"(platform={sys.platform!r}, on PATH={shutil.which('nsjail') is not None})"
            )
        return _nsjail_wrap(
            argv,
            workdir,
            network=network,
            mem_bytes=mem_bytes,
            pids_max=pids_max,
            fsize_bytes=fsize_bytes,
            cpu_ms_per_sec=cpu_ms_per_sec,
        )
    if _BACKEND == "auto":
        if _nsjail_available():
            return _nsjail_wrap(
                argv,
                workdir,
                network=network,
                mem_bytes=mem_bytes,
                pids_max=pids_max,
                fsize_bytes=fsize_bytes,
                cpu_ms_per_sec=cpu_ms_per_sec,
            )
        _warn_unsandboxed_once()
        return list(argv)
    raise SandboxUnavailableError(f"unknown ASSESS_SANDBOX backend {_BACKEND!r}")
