import json
import re
from pathlib import Path

import pytest

from assessment_agent import sandbox
from assessment_agent.sandbox import SandboxUnavailableError, is_active, wrap

# argv[0] has a "/", so nsjail's absolute-path resolution leaves it untouched and
# these assertions stay deterministic regardless of the test host's PATH.
ARGV = ["/usr/bin/python3", "main.py"]
WORKDIR = Path("/tmp/assess_x")


def test_none_backend_is_passthrough(monkeypatch):
    monkeypatch.setattr(sandbox, "_BACKEND", "none")
    assert wrap(ARGV, WORKDIR, mem_bytes=512, pids_max=64) == ARGV


def test_auto_falls_back_to_passthrough_without_nsjail(monkeypatch):
    monkeypatch.setattr(sandbox, "_BACKEND", "auto")
    monkeypatch.setattr(sandbox, "_nsjail_available", lambda: False)
    assert wrap(ARGV, WORKDIR) == ARGV


def test_forced_nsjail_raises_when_unavailable(monkeypatch):
    monkeypatch.setattr(sandbox, "_BACKEND", "nsjail")
    monkeypatch.setattr(sandbox, "_nsjail_available", lambda: False)
    with pytest.raises(SandboxUnavailableError):
        wrap(ARGV, WORKDIR)


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setattr(sandbox, "_BACKEND", "bogus")
    with pytest.raises(SandboxUnavailableError):
        wrap(ARGV, WORKDIR)


@pytest.mark.parametrize(
    "backend,available,expected",
    [
        ("none", True, False),
        ("nsjail", True, True),
        ("nsjail", False, True),  # forced: active; wrap() raises later if missing
        ("auto", True, True),
        ("auto", False, False),
        ("bogus", True, False),
    ],
)
def test_is_active(monkeypatch, backend, available, expected):
    monkeypatch.setattr(sandbox, "_BACKEND", backend)
    monkeypatch.setattr(sandbox, "_nsjail_available", lambda: available)
    assert is_active() is expected


def _nsjail(monkeypatch, argv=ARGV, **kw):
    monkeypatch.setattr(sandbox, "_BACKEND", "nsjail")
    monkeypatch.setattr(sandbox, "_nsjail_available", lambda: True)
    return wrap(argv, WORKDIR, **kw)


def test_nsjail_wraps_and_preserves_argv_after_separator(monkeypatch):
    cmd = _nsjail(monkeypatch, mem_bytes=512 * 1024 * 1024, pids_max=64)
    assert cmd[0] == "nsjail"
    # the real command follows the `--` separator, untouched and in order
    assert cmd[cmd.index("--") + 1 :] == ARGV
    assert str(WORKDIR) in cmd  # bound in as the working dir


def test_nsjail_isolates_network_by_default(monkeypatch):
    cmd = _nsjail(monkeypatch, mem_bytes=1, pids_max=1)
    assert "--iface_no_lo" in cmd
    assert "--disable_clone_newnet" not in cmd


def test_nsjail_allows_network_when_requested(monkeypatch):
    cmd = _nsjail(monkeypatch, network=True)
    assert "--disable_clone_newnet" in cmd
    assert "--iface_no_lo" not in cmd


def test_nsjail_applies_cgroup_ceilings(monkeypatch):
    cmd = _nsjail(monkeypatch, mem_bytes=123456, pids_max=64)
    assert "--use_cgroupv2" in cmd
    assert cmd[cmd.index("--cgroup_mem_max") + 1] == "123456"
    assert cmd[cmd.index("--cgroup_pids_max") + 1] == "64"
    # Without this the ceiling is escapable by swapping — see _nsjail_wrap.
    assert cmd[cmd.index("--cgroup_mem_swap_max") + 1] == "0"


def test_nsjail_omits_disabled_ceilings(monkeypatch):
    # 0 disables, mirroring the rlimit convention.
    cmd = _nsjail(monkeypatch, mem_bytes=0, pids_max=0)
    assert "--cgroup_mem_max" not in cmd
    assert "--cgroup_mem_swap_max" not in cmd
    assert "--cgroup_pids_max" not in cmd
    assert "--cgroup_cpu_ms_per_sec" not in cmd
    assert "--use_cgroupv2" not in cmd


def test_nsjail_leaves_address_space_to_the_cgroup(monkeypatch):
    # RLIMIT_AS is deliberately unbounded (memory is bounded by the cgroup) so the
    # JVM/Go can start; see the module docstring.
    cmd = _nsjail(monkeypatch, mem_bytes=1)
    assert cmd[cmd.index("--rlimit_as") + 1] == "inf"


def test_nsjail_output_cap_in_mb_or_inf(monkeypatch):
    capped = _nsjail(monkeypatch, fsize_bytes=64 * 1024 * 1024)
    assert capped[capped.index("--rlimit_fsize") + 1] == "64"
    uncapped = _nsjail(monkeypatch, fsize_bytes=0)  # 0 => don't cap (compiler writes)
    assert uncapped[uncapped.index("--rlimit_fsize") + 1] == "inf"


def test_nsjail_injects_path_and_home_without_leaking_host_env(monkeypatch):
    cmd = _nsjail(monkeypatch)
    envs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--env"]
    assert any(e.startswith("PATH=") for e in envs)
    assert f"HOME={WORKDIR}" in envs
    assert "--keep_env" not in cmd  # host env (incl. secrets) stays out of the jail


def test_nsjail_resolves_bare_command_against_the_jail_path(monkeypatch):
    # Against the jail's PATH, not the worker's: under `uv run` the worker's PATH
    # leads with /app/.venv/bin, which the jail hides, so a worker-resolved python3
    # would be a path the child cannot exec (EACCES).
    seen = {}

    def which(name, path=None):
        seen["path"] = path
        return f"/usr/bin/{name}"

    monkeypatch.setattr(sandbox.shutil, "which", which)
    cmd = _nsjail(monkeypatch, argv=["python3", "main.py"])
    assert cmd[cmd.index("--") + 1 :] == ["/usr/bin/python3", "main.py"]
    assert seen["path"] == sandbox._JAIL_PATH
    # ...and it is the same PATH the child is handed.
    envs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--env"]
    assert f"PATH={sandbox._JAIL_PATH}" in envs


def test_nsjail_raises_when_a_bare_command_is_not_on_the_jail_path(monkeypatch):
    # Falling back to the bare name fails inside the jail and grades as the candidate's
    # compile/runtime error; FileNotFoundError is what the runner maps to infra_error.
    monkeypatch.setattr(sandbox.shutil, "which", lambda name, path=None: None)
    with pytest.raises(FileNotFoundError, match="not on the jail PATH") as exc:
        _nsjail(monkeypatch, argv=["python3", "main.py"])
    assert exc.value.filename == "python3"


def test_nsjail_leaves_pathful_argv0_untouched(monkeypatch):
    # "./program" (compiled binary, run relative to --cwd) must not be PATH-resolved.
    cmd = _nsjail(monkeypatch, argv=["./program"])
    assert cmd[cmd.index("--") + 1 :] == ["./program"]


def test_nsjail_pins_the_isolation_flags_with_no_runtime_symptom(monkeypatch):
    # These have no observable effect on a submission, so no live test can catch
    # their removal: dropping --chroot / or --mode o weakens isolation silently, and
    # --bindmount demoted to --bindmount_ro breaks only compiled languages. The
    # workdir assertion above is satisfied by the --cwd value alone, so --bindmount
    # could be deleted today with the whole suite staying green.
    cmd = _nsjail(monkeypatch, mem_bytes=1, pids_max=1)
    assert cmd[cmd.index("--mode") + 1] == "o"
    assert cmd[cmd.index("--chroot") + 1] == "/"
    assert cmd[cmd.index("--bindmount") + 1] == str(WORKDIR)  # -B (rw), not -R
    assert cmd[cmd.index("--cwd") + 1] == str(WORKDIR)
    # 0 = don't self-govern: the runner's timeout + killpg own the child's lifetime.
    assert cmd[cmd.index("--time_limit") + 1] == "0"
    assert cmd[cmd.index("--cgroupv2_mount") + 1] == "/sys/fs/cgroup"


def _tmpfs_targets(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "--tmpfsmount"]


def test_nsjail_hides_shared_paths_before_the_workdir_bind(monkeypatch):
    # nsjail mounts in argv order and the workdir lives under /tmp, so a tmpfs over
    # /tmp placed after the bind would bury the workdir. Like the flags above, a
    # reordering has no symptom a candidate would report, so pin it here.
    cmd = _nsjail(monkeypatch, mem_bytes=1, pids_max=1)
    hiding = [i for i, a in enumerate(cmd) if a in ("--mount", "--tmpfsmount")]
    assert hiding and max(hiding) < cmd.index("--bindmount")
    # /tmp is a sized tmpfs (other candidates' workdirs vanish; the own one is bound
    # back in on top).
    assert cmd[cmd.index("--mount") + 1] == f"none:/tmp:tmpfs:size={64 * 1024 * 1024}"
    hidden = _tmpfs_targets(cmd)
    # Always emitted, whatever the host: /sys/fs/cgroup is where a child could raise
    # its own memory.max / pids.max, and /dev/shm is a channel that outlives the jail.
    assert "/sys/fs/cgroup" in hidden
    assert "/dev/shm" in hidden
    # The grader's own source (questions.py carries the answer keys) exists wherever
    # this runs, so it is hidden here too.
    assert str(sandbox._GRADER_ROOT) in hidden


def test_nsjail_hides_host_dependent_paths_only_where_they_exist(monkeypatch):
    # A missing mount point would make nsjail mkdir it on the host root, so only the
    # paths every Linux host has are emitted unconditionally.
    monkeypatch.setattr(sandbox, "_HIDDEN", ("/sys/fs/cgroup", "/no/such/dir/assess"))
    assert _tmpfs_targets(_nsjail(monkeypatch)) == ["/sys/fs/cgroup"]


def test_nsjail_raises_the_fd_limit_go_needs(monkeypatch):
    # nsjail's default RLIMIT_NOFILE of 32 fails `go run` ("pipe2: too many open files").
    cmd = _nsjail(monkeypatch)
    assert cmd[cmd.index("--rlimit_nofile") + 1] == "1024"


_X86_64_ONLY = {"iopl", "ioperm", "kexec_file_load", "umount"}


def _policy(monkeypatch, machine):
    monkeypatch.setattr(sandbox.platform, "machine", lambda: machine)
    cmd = _nsjail(monkeypatch)
    return cmd[cmd.index("--seccomp_string") + 1]


@pytest.mark.parametrize("machine", ["aarch64", "x86_64"])
def test_nsjail_installs_the_seccomp_deny_list(monkeypatch, machine):
    policy = _policy(monkeypatch, machine)
    names = set(re.findall(r"\w+", policy))
    assert {"ptrace", "unshare", "setns", "mount", "bpf", "keyctl", "userfaultfd"} <= names
    assert policy.startswith("ERRNO(1)") and policy.endswith("DEFAULT ALLOW")
    # kafel rejects the x86-only names on aarch64 ("Undefined identifier"), which
    # would fail every jail — so they appear on x86_64 and nowhere else.
    if machine == "x86_64":
        assert _X86_64_ONLY <= names
    else:
        assert not _X86_64_ONLY & names


def test_nsjail_seccomp_closes_clone_and_enosys_clone3(monkeypatch):
    policy = _policy(monkeypatch, "aarch64")
    # CLONE_NEWNS|NEWCGROUP|NEWUTS|NEWIPC|NEWUSER|NEWPID|NEWNET: clone() must not be
    # a second door to the namespaces unshare() is denied.
    mask = 0x00020000 | 0x02000000 | 0x04000000 | 0x08000000 | 0x10000000 | 0x20000000
    mask |= 0x40000000
    assert f"clone {{ (clone_flags & {mask:#x}) != 0 }}" in policy
    # clone3 (435) must be ENOSYS, not EPERM: glibc falls back to clone only on
    # ENOSYS, so EPERM would fail every pthread_create.
    eperm, enosys = policy.split("ERRNO(38)")
    assert "SYSCALL[435]" in enosys
    assert "SYSCALL[435]" not in eperm


_SECCOMP_PROFILE = Path(__file__).resolve().parent.parent / "deploy" / "seccomp.json"


def test_container_seccomp_profile_is_native_abi_only_and_not_sys_admin_gated():
    profile = json.loads(_SECCOMP_PROFILE.read_text())
    # Kafel matches native syscall numbers only (see _seccomp_policy), so the container
    # profile must refuse x32/i386/arm32 outright. That holds whatever the host kernel
    # enables, which no live test can show.
    assert profile["archMap"]
    assert all(a["subArchitectures"] == [] for a in profile["archMap"]), profile["archMap"]
    # The container starts with CAP_SYS_ADMIN for its entrypoint and Docker resolves cap
    # conditions against that start set, so a SYS_ADMIN-gated allow would outlive the drop.
    rules = profile["syscalls"]
    gated = [r["names"] for r in rules if "CAP_SYS_ADMIN" in r.get("includes", {}).get("caps", [])]
    assert not gated
    # ENOSYS, not EPERM: glibc falls back from clone3 to clone only on ENOSYS.
    clone3 = [r for r in rules if "clone3" in r["names"]]
    assert clone3 and all(r["action"] == "SCMP_ACT_ERRNO" and r["errnoRet"] == 38 for r in clone3)


def test_nsjail_cpu_ceiling_only_when_set(monkeypatch):
    capped = _nsjail(monkeypatch, cpu_ms_per_sec=2000)
    assert capped[capped.index("--cgroup_cpu_ms_per_sec") + 1] == "2000"
    # The cpu ceiling alone still needs the cgroup-v2 machinery.
    assert "--use_cgroupv2" in capped
    assert "--cgroup_mem_max" not in capped
    assert "--cgroup_pids_max" not in capped
    uncapped = _nsjail(monkeypatch, cpu_ms_per_sec=0, mem_bytes=1, pids_max=1)
    assert "--cgroup_cpu_ms_per_sec" not in uncapped


def test_nsjail_passes_no_uid_mapping(monkeypatch):
    # The non-root worker makes nsjail's default mapping (caller uid onto itself)
    # the right one; an explicit mapping would need privilege the worker lacks.
    cmd = _nsjail(monkeypatch)
    flags = set(cmd[: cmd.index("--")])
    mapping = {"--user", "-u", "--group", "-g", "--uid_mapping", "-U", "--gid_mapping", "-G"}
    assert not flags & mapping
