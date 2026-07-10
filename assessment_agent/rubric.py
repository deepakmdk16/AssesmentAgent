"""Assembles the judge's system prompt from the reusable prompt modules.

The modules live in ``prompts/`` as plain markdown so they can be edited and
version-controlled independently of the code (your "skills" as repo modules).
The assembled prompt is stable across candidates, which lets it be prompt-cached.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _read(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text().strip()


def build_system_prompt() -> str:
    examples = sorted((_PROMPTS_DIR / "examples").glob("*.md"))
    parts = [
        _read("review_procedure.md"),
        _read("scoring_scale.md"),
        _read("report_guidance.md"),
        "# Calibration examples\n\n" + "\n\n---\n\n".join(p.read_text().strip() for p in examples),
    ]
    return "\n\n".join(parts)


# Assembled once at import — stable prefix, cache-friendly.
SYSTEM_PROMPT = build_system_prompt()
