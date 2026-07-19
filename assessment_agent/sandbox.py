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

nsjail is the choice because a single argv wrap expresses all three requirements at
once — a fresh network namespace (no egress), all capabilities dropped, and
cgroup-v2 memory + pids ceilings — and it keeps the runner's per-case serial model
(and therefore the performance-case timing) intact.
"""

from __future__ import annotations

import logging
import os
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
) -> list[str]:
    """Build the nsjail invocation that runs `argv` in `workdir`.

    The host filesystem is mounted read-only (so the toolchain is visible) with the
    submission's temp dir bind-mounted read-write. Isolation comes from the fresh
    net/pid/mount namespaces, the cgroup ceilings, and nsjail dropping every
    capability by default — not from a uid switch: remapping to an unprivileged uid
    would collide with the root-owned (0700) temp workdir the runner created, so the
    jailed process could not write it. We pass ``--time_limit 0`` and let the
    runner's own wall-clock timeout + process-group kill govern lifetime, so nsjail
    (our direct child, in its own session) is torn down by the existing `_kill_tree`.

    nsjail owns *all* the resource limits when it wraps the child, so the runner
    skips its own preexec rlimits under the sandbox (see `is_active`); applying both
    fights — nsjail raising RLIMIT_AS back up hits EPERM against the runner's lower
    cap. Address space is left unbounded (``--rlimit_as inf``) on purpose: memory is
    bounded by the cgroup instead, which — unlike an address-space rlimit — actually
    holds for the JVM/Go. The output ceiling stays as ``--rlimit_fsize``.

    A `mem_bytes`/`pids_max`/`fsize_bytes` of 0 omits that ceiling (0 disables),
    matching the rlimit convention in runner.py.

    BRING-UP: the exact uid mapping and cgroup-v2 delegation are the two things to
    validate the first time this runs on a real host — see the Dockerfile notes and
    `test_sandbox_nsjail.py` (which SKIPs unless nsjail is installed).
    """
    cmd = [
        "nsjail",
        "--quiet",
        "--mode",
        "o",  # execute once, then exit
        "--chroot",
        "/",  # host FS, read-only by default
        "--bindmount",  # nsjail's -B: bind rw (--bindmount_ro/-R is the ro one)
        str(workdir),  # the candidate's temp dir stays writable
        "--cwd",
        str(workdir),
        # nsjail clears the environment by default — a feature here, since it keeps
        # host secrets (e.g. ANTHROPIC_API_KEY) out of untrusted code. But that
        # leaves no PATH to resolve a bare `python3`/`node`/…, and no writable HOME
        # for toolchains that cache there, so inject just those two.
        "--env",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--env",
        f"HOME={workdir}",
        "--time_limit",
        "0",  # runner enforces the timeout + killpg; don't double-govern
        "--rlimit_as",
        "inf",  # memory bounded by the cgroup below, not address space (JVM/Go-safe)
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

    if mem_bytes > 0 or pids_max > 0:
        cmd += ["--use_cgroupv2", "--cgroupv2_mount", "/sys/fs/cgroup"]
        if mem_bytes > 0:
            cmd += ["--cgroup_mem_max", str(mem_bytes)]
        if pids_max > 0:
            cmd += ["--cgroup_pids_max", str(pids_max)]

    # nsjail execve()s argv[0] literally — unlike execvp it does NOT search PATH —
    # so a bare command name ("python3", "node", "java") must be resolved to an
    # absolute path first. A path that already contains "/" (an absolute runtime, or
    # the compiled "./program" run relative to --cwd) is left untouched.
    argv = list(argv)
    if "/" not in argv[0]:
        argv[0] = shutil.which(argv[0]) or argv[0]

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
            )
        _warn_unsandboxed_once()
        return list(argv)
    raise SandboxUnavailableError(f"unknown ASSESS_SANDBOX backend {_BACKEND!r}")
