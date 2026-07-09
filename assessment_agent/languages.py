"""Registry of how to compile/run a candidate's submission per language.

Contract with the candidate program: read the test-case input from standard
input, write the answer to standard output. This keeps a single, uniform
harness across every language.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    name: str
    source_filename: str
    run: list[str]
    # argv run once in the work dir before the test cases; None for interpreted langs.
    compile: list[str] | None = None


LANGUAGES: dict[str, Language] = {
    "python": Language("python", "main.py", ["python3", "main.py"]),
    "javascript": Language("javascript", "main.js", ["node", "main.js"]),
    "ruby": Language("ruby", "main.rb", ["ruby", "main.rb"]),
    "go": Language("go", "main.go", ["go", "run", "main.go"]),
    # Java's public class must match the file name, so submissions are compiled as Main.
    "java": Language("java", "Main.java", ["java", "Main"], ["javac", "Main.java"]),
    "c": Language("c", "main.c", ["./program"], ["gcc", "main.c", "-o", "program"]),
    "cpp": Language("cpp", "main.cpp", ["./program"], ["g++", "main.cpp", "-o", "program"]),
    "rust": Language("rust", "main.rs", ["./program"], ["rustc", "main.rs", "-o", "program"]),
}

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".rb": "ruby",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".rs": "rust",
}


def detect_language(filename: str) -> str | None:
    from pathlib import Path

    return EXTENSION_TO_LANGUAGE.get(Path(filename).suffix.lower())
