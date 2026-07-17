import pytest

from assessment_agent import runner
from assessment_agent.languages import LANGUAGES, detect_language


def test_detects_common_extensions():
    assert detect_language("solution.py") == "python"
    assert detect_language("Main.java") == "java"
    assert detect_language("a.cpp") == "cpp"
    assert detect_language("a.cc") == "cpp"
    assert detect_language("s.rs") == "rust"


def test_unknown_extension_returns_none():
    assert detect_language("notes.txt") is None
    assert detect_language("noext") is None


def test_java_resolve_derives_names_from_public_class():
    resolve = LANGUAGES["java"].resolve
    assert resolve is not None
    fname, compile_cmd, run_cmd = resolve("public class Solution { }")
    assert fname == "Solution.java"
    assert compile_cmd == ["javac", "Solution.java"]
    assert run_cmd == ["java", "Solution"]


def test_java_resolve_defaults_to_main_without_public_class():
    fname, _, run_cmd = LANGUAGES["java"].resolve("class Helper {}\nclass Main {}")
    # No `public class`, so the first bare class is used.
    assert fname == "Helper.java"
    assert run_cmd == ["java", "Helper"]


def test_non_java_languages_have_no_resolver():
    assert LANGUAGES["python"].resolve is None


@pytest.mark.skipif(runner.resource is None, reason="POSIX resource limits unavailable")
@pytest.mark.parametrize("name", sorted(LANGUAGES))
def test_managed_runtimes_are_exempt_from_the_address_space_cap(name, monkeypatch):
    """RLIMIT_AS caps address space, not memory in use.

    The JVM and Go reserve GBs of virtual space at startup and touch almost none
    of it, so the cap doesn't bound their memory — it stops them booting. That is
    invisible on macOS (which ignores RLIMIT_AS) and only shows up on Linux,
    where it cost a green CI run: java compiled fine, then every case died at VM
    init in 9ms with exit 1. Assert the routing here, since the failure itself
    can't be reproduced on a dev Mac.
    """
    attempted = []
    monkeypatch.setattr(
        runner.resource, "setrlimit", lambda res_id, val: attempted.append(res_id)
    )
    runner._apply_limits(LANGUAGES[name].address_space_capped)

    # Everyone gets the output cap; only the managed runtimes skip the AS cap.
    assert runner.resource.RLIMIT_FSIZE in attempted
    expect_as = name not in {"java", "go"}
    assert (runner.resource.RLIMIT_AS in attempted) is expect_as
    assert LANGUAGES[name].address_space_capped is expect_as
