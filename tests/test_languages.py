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
    assert compile_cmd == ["javac", "-d", ".", "Solution.java"]
    assert run_cmd == ["java", "-cp", ".", "Solution"]


def test_java_resolve_defaults_to_main_without_public_class():
    fname, _, run_cmd = LANGUAGES["java"].resolve("class Helper {}\nclass Main {}")
    # No `public class`, so the first bare class is used.
    assert fname == "Helper.java"
    assert run_cmd == ["java", "-cp", ".", "Helper"]


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

    Node joined them in S03, for the same reason and found the same way: V8 asks
    for a multi-GB pointer-compression cage, so under the cap it aborts with
    "Failed to reserve virtual memory for CodeRange" before running a line. It had
    always been capped; nothing executed JavaScript through the runner on Linux
    until the language smoke suite did.
    """
    attempted = []
    monkeypatch.setattr(
        runner.resource, "setrlimit", lambda res_id, val: attempted.append(res_id)
    )
    runner._apply_limits(LANGUAGES[name].address_space_capped)

    # Everyone gets the output cap; only the managed runtimes skip the AS cap.
    assert runner.resource.RLIMIT_FSIZE in attempted
    expect_as = name not in {"java", "go", "javascript"}
    assert (runner.resource.RLIMIT_AS in attempted) is expect_as
    assert LANGUAGES[name].address_space_capped is expect_as


# --- S03: the compile lines a correct solution needs (R2-007, R2-008, R2-092) --


def test_c_links_the_maths_library_and_optimises():
    # R2-007: without -lm every correct C solution using sqrt/pow/log was a link
    # error and scored 0. R2-008: an unoptimised build carries the tightest time
    # multiplier (1.0), so a correct C solution TLEd where the Python reference passed.
    compile_cmd = LANGUAGES["c"].compile
    assert compile_cmd is not None
    assert "-lm" in compile_cmd
    assert "-O2" in compile_cmd
    # -lm must come AFTER the source: the linker resolves left to right.
    assert compile_cmd.index("-lm") > compile_cmd.index("main.c")


def test_c_keeps_pre_gcc14_code_compiling():
    # gcc 14 (trixie) promoted implicit-function-declaration and three siblings from
    # warnings to errors: without this a C submission missing an #include, which
    # compiled and scored under gcc 12, becomes a compile error and a 0%.
    compile_cmd = LANGUAGES["c"].compile
    assert compile_cmd is not None
    assert "-fpermissive" in compile_cmd


def test_cpp_optimises():
    compile_cmd = LANGUAGES["cpp"].compile
    assert compile_cmd is not None
    assert "-O2" in compile_cmd


def test_rust_builds_in_release_mode():
    # R2-008: rustc without -O is a debug build — overflow panics and 10-50x slower.
    compile_cmd = LANGUAGES["rust"].compile
    assert compile_cmd is not None
    assert "-O" in compile_cmd


def test_go_has_a_real_compile_step():
    # R2-092: `go run` per case compiled inside the run cgroup and the first case's
    # time limit, and a Go compile error was graded as N runtime errors.
    go = LANGUAGES["go"]
    assert go.compile is not None
    assert go.compile[:2] == ["go", "build"]
    assert go.run == ["./program"]


def test_every_language_can_report_its_toolchain_version():
    # The pin in toolchains.txt is diffed against these in the built image (R2-105).
    for name, lang in LANGUAGES.items():
        assert lang.version, f"{name} has no version argv"
        assert lang.version[0] == (lang.compile or lang.run)[0], (
            f"{name}: the version probe must ask the binary the candidate's code uses"
        )


# --- S03: a Java `package` declaration compiles and runs (R2-094) -----------------


def test_java_resolve_honours_a_package_declaration():
    from assessment_agent.languages import _java_resolve

    src = "package com.example.sub;\n\npublic class Solver {\n  public static void main(String[] a) {}\n}\n"
    filename, compile_cmd, run_cmd = _java_resolve(src)
    assert filename == "Solver.java"
    # -d . writes com/example/sub/Solver.class under the workdir, whatever the
    # source file is called; the class then has to be launched by its full name.
    assert compile_cmd == ["javac", "-d", ".", "Solver.java"]
    assert run_cmd == ["java", "-cp", ".", "com.example.sub.Solver"]


def test_java_resolve_without_a_package_is_unchanged_in_shape():
    from assessment_agent.languages import _java_resolve

    filename, compile_cmd, run_cmd = _java_resolve("public class Main {}")
    assert filename == "Main.java"
    assert compile_cmd == ["javac", "-d", ".", "Main.java"]
    assert run_cmd == ["java", "-cp", ".", "Main"]


def test_java_resolve_ignores_a_package_mentioned_in_a_comment():
    from assessment_agent.languages import _java_resolve

    src = "// package not.this.one;\npublic class Main {}\n"
    assert _java_resolve(src)[2] == ["java", "-cp", ".", "Main"]
