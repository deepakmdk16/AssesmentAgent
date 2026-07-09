from assessment_agent.languages import detect_language


def test_detects_common_extensions():
    assert detect_language("solution.py") == "python"
    assert detect_language("Main.java") == "java"
    assert detect_language("a.cpp") == "cpp"
    assert detect_language("a.cc") == "cpp"
    assert detect_language("s.rs") == "rust"


def test_unknown_extension_returns_none():
    assert detect_language("notes.txt") is None
    assert detect_language("noext") is None
