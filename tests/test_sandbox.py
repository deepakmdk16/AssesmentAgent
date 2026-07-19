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


def test_nsjail_omits_disabled_ceilings(monkeypatch):
    # 0 disables, mirroring the rlimit convention.
    cmd = _nsjail(monkeypatch, mem_bytes=0, pids_max=0)
    assert "--cgroup_mem_max" not in cmd
    assert "--cgroup_pids_max" not in cmd
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


def test_nsjail_resolves_bare_command_to_absolute_path(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    cmd = _nsjail(monkeypatch, argv=["python3", "main.py"])
    assert cmd[cmd.index("--") + 1 :] == ["/usr/bin/python3", "main.py"]


def test_nsjail_leaves_pathful_argv0_untouched(monkeypatch):
    # "./program" (compiled binary, run relative to --cwd) must not be PATH-resolved.
    cmd = _nsjail(monkeypatch, argv=["./program"])
    assert cmd[cmd.index("--") + 1 :] == ["./program"]
