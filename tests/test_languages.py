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
