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
    # argv that prints the toolchain's version — the binary the candidate's code is
    # compiled (or, interpreted, run) with. toolchains.py normalises the output and
    # diffs it against the pin in toolchains.txt (audit R2-105).
    version: tuple[str, ...] = ()


_JAVA_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def _java_entrypoint(source: str) -> str:
    """Java requires the file name to match the public class, so derive it."""
    match = re.search(r"public\s+class\s+([A-Za-z_]\w*)", source) or re.search(
        r"\bclass\s+([A-Za-z_]\w*)", source
    )
    return match.group(1) if match else "Main"


def _java_resolve(source: str) -> tuple[str, list[str], list[str]]:
    """File, compile and run argv for a Java source.

    A `package` declaration (audit R2-094) used to make every case a runtime error:
    the class was compiled beside the source but had to be launched by its
    qualified name from the package root. `javac -d .` writes the class under
    `a/b/Cls.class` for `package a.b` — and plainly `./Cls.class` without one —
    so the launch is uniform: the qualified name on a classpath of the workdir.
    Comments are stripped first so a `// package ...` note cannot redirect it.
    """
    code = _JAVA_COMMENT.sub("", source)
    cls = _java_entrypoint(code)
    pkg = re.search(r"^\s*package\s+([\w.]+)\s*;", code, re.M)
    qualified = f"{pkg.group(1)}.{cls}" if pkg else cls
    return f"{cls}.java", ["javac", "-d", ".", f"{cls}.java"], ["java", "-cp", ".", qualified]


LANGUAGES: dict[str, Language] = {
    "python": Language(
        "python",
        "main.py",
        ["python3", "main.py"],
        time_multiplier=3.0,
        version=("python3", "--version"),
    ),
    "javascript": Language(
        "javascript",
        "main.js",
        ["node", "main.js"],
        time_multiplier=2.0,
        version=("node", "--version"),
    ),
    "ruby": Language(
        "ruby", "main.rb", ["ruby", "main.rb"], time_multiplier=3.0, version=("ruby", "--version")
    ),
    # A real compile step (audit R2-092): `go run` per case rebuilt inside the run
    # cgroup and the first case's time limit, and reported a compile error as a
    # runtime error on every case. The Go runtime reserves large virtual arenas up
    # front — same address-space story as the JVM below.
    "go": Language(
        "go",
        "main.go",
        ["./program"],
        ["go", "build", "-o", "program", "main.go"],
        time_multiplier=2.0,
        address_space_capped=False,
        version=("go", "version"),
    ),
    # Java's file name must match the public class, so it derives names from source.
    "java": Language(
        "java",
        "Main.java",
        ["java", "-cp", ".", "Main"],
        ["javac", "-d", ".", "Main.java"],
        time_multiplier=2.0,
        resolve=_java_resolve,
        address_space_capped=False,
        version=("javac", "-version"),
    ),
    # Compiled languages build optimised (audit R2-008): an -O0 build with the
    # tightest time multiplier TLEd where the Python reference passed, and a debug
    # rustc build additionally panics on integer overflow. C links libm (R2-007):
    # without -lm every correct solution using sqrt/pow/log was a link error.
    "c": Language(
        "c",
        "main.c",
        ["./program"],
        # -fpermissive: gcc 14 (trixie) made implicit-function-declaration,
        # implicit-int, incompatible-pointer-types and return-mismatch hard errors.
        # A C submission that forgets an #include compiled and scored under gcc 12
        # and would now be a compile error and a 0% — the R2-007 failure again, from
        # a base-image bump rather than a missing flag. This keeps them warnings.
        ["gcc", "-O2", "-fpermissive", "main.c", "-o", "program", "-lm"],
        version=("gcc", "-dumpfullversion"),
    ),
    "cpp": Language(
        "cpp",
        "main.cpp",
        ["./program"],
        ["g++", "-O2", "main.cpp", "-o", "program"],
        version=("g++", "-dumpfullversion"),
    ),
    "rust": Language(
        "rust",
        "main.rs",
        ["./program"],
        ["rustc", "-O", "main.rs", "-o", "program"],
        version=("rustc", "--version"),
    ),
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
