"""Registry of how to compile/run a candidate's submission per language.

Contract with the candidate program: read the test-case input from standard
input, write the answer to standard output. This keeps a single, uniform
harness across every language.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    name: str
    source_filename: str
    run: list[str]
    # argv run once in the work dir before the test cases; None for interpreted langs.
    compile: list[str] | None = None
    # Per-language slack on the time limit (interpreted/VM languages are slower),
    # mirroring how competitive-programming judges scale limits by language.
    time_multiplier: float = 1.0
    # Whether the runner's RLIMIT_AS ceiling applies to this language.
    #
    # RLIMIT_AS caps *address space*, not memory in use. Managed runtimes reserve
    # enormous virtual regions up front and touch almost none of it — a JVM
    # reserves a heap sized from total RAM (GBs on a CI box) the instant it
    # starts. So for them the cap doesn't mean "don't use too much memory", it
    # means "don't start": the JVM dies during VM init, before main(), having
    # allocated essentially nothing. Set False for those; they are bounded by the
    # timeout and, in production, by the sandbox's cgroup memory limit — which is
    # the only thing that can express the actual intent anyway.
    address_space_capped: bool = True
    # Only for languages whose file name / entrypoint depends on the *source*
    # (e.g. Java, where the file must match the public class name). When set, it
    # returns (source_filename, compile, run) derived from the submission; the
    # runner calls it uniformly so it never needs to special-case a language.
    resolve: Callable[[str], tuple[str, list[str] | None, list[str]]] | None = None


def _java_entrypoint(source: str) -> str:
    """Java requires the file name to match the public class, so derive it."""
    match = re.search(r"public\s+class\s+([A-Za-z_]\w*)", source) or re.search(
        r"\bclass\s+([A-Za-z_]\w*)", source
    )
    return match.group(1) if match else "Main"


def _java_resolve(source: str) -> tuple[str, list[str], list[str]]:
    cls = _java_entrypoint(source)
    return f"{cls}.java", ["javac", f"{cls}.java"], ["java", cls]


LANGUAGES: dict[str, Language] = {
    "python": Language("python", "main.py", ["python3", "main.py"], time_multiplier=3.0),
    "javascript": Language("javascript", "main.js", ["node", "main.js"], time_multiplier=2.0),
    "ruby": Language("ruby", "main.rb", ["ruby", "main.rb"], time_multiplier=3.0),
    # `go run` compiles *and* runs, and the Go runtime reserves large virtual
    # arenas up front — same address-space story as the JVM below.
    "go": Language(
        "go",
        "main.go",
        ["go", "run", "main.go"],
        time_multiplier=2.0,
        address_space_capped=False,
    ),
    # Java's file name must match the public class, so it derives names from source.
    "java": Language(
        "java",
        "Main.java",
        ["java", "Main"],
        ["javac", "Main.java"],
        time_multiplier=2.0,
        resolve=_java_resolve,
        address_space_capped=False,
    ),
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
