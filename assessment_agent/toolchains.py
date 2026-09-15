"""Which toolchain grades each language (audit R2-105).

`toolchains.txt` beside this module is the pin: one line per language,
`<language> <tool> <version>`, e.g. `c gcc 14.2.0`. It is what `GET /toolchains`
serves — the platform shows it to the candidate before they write a line — and
what the built image is diffed against (`scripts/lang-smoke.sh`, CI's sandbox
job), so a base-image or apt bump that moves a compiler fails the build until the
pin, and with it the displayed version, is updated in the same commit.

The route serves the pin rather than a live probe: CI has already proven the image
matches it, and the pin costs no subprocess per request.

    python -m assessment_agent.toolchains          # print the live versions
    python -m assessment_agent.toolchains --check  # exit 1 on drift from the pin
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from .languages import LANGUAGES
from .sandbox import jail_path

PIN_FILE = Path(__file__).with_name("toolchains.txt")

_VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
_PROBE_TIMEOUT_S = 20


def pinned() -> dict[str, str]:
    """language -> "tool version", as recorded in toolchains.txt."""
    out: dict[str, str] = {}
    for raw in PIN_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        language, _, entry = line.partition(" ")
        out[language] = entry.strip()
    return out


def normalise(tool: str, stdout: str, stderr: str) -> str:
    """`"<tool> <x.y.z>"` from whatever the binary printed — gcc a bare number, go
    `go version go1.24.4 linux/arm64`, java to stderr — or `"<tool> unknown"` when
    no dotted number appears at all."""
    match = _VERSION_RE.search(stdout) or _VERSION_RE.search(stderr)
    return f"{tool} {match.group() if match else 'unknown'}"


def version_tuple(entry: str) -> tuple[int, ...]:
    """The numeric part of a pin or probe entry; `()` when there is none."""
    match = _VERSION_RE.search(entry)
    return tuple(int(part) for part in match.group().split(".")) if match else ()


def probe_one(language: str) -> str:
    """Ask the binary the candidate's code is compiled (or run) with for its
    version — resolved on the jail's PATH when the sandbox is active, exactly as
    the compile and run steps resolve it — or `"missing: <tool>"`."""
    argv = list(LANGUAGES[language].version)
    tool = argv[0]
    resolved = shutil.which(tool, path=jail_path())
    if resolved is None:
        return f"missing: {tool}"
    try:
        proc = subprocess.run(
            [resolved, *argv[1:]], capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired):
        return f"missing: {tool}"
    return normalise(tool, proc.stdout, proc.stderr)


def probe() -> dict[str, str]:
    return {language: probe_one(language) for language in sorted(LANGUAGES)}


def drift(live: dict[str, str], pin: dict[str, str]) -> list[str]:
    """One line per language whose live toolchain differs from the pin."""
    lines = []
    for language in sorted(set(live) | set(pin)):
        want = pin.get(language, "(not pinned)")
        have = live.get(language, "(not probed)")
        if want != have:
            lines.append(f"{language}: pinned {want!r}, image has {have!r}")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    live = probe()
    for language, entry in live.items():
        print(f"{language} {entry}")
    if "--check" not in args:
        return 0
    lines = drift(live, pinned())
    if lines:
        print("toolchain drift from assessment_agent/toolchains.txt:", file=sys.stderr)
        for line in lines:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("toolchains match the pin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
