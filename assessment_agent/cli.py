"""Command-line entry point: assess a candidate submission file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import AssessmentResult, assess
from .languages import LANGUAGES, detect_language
from .questions import HARDCODED_QUESTION


def format_report(result: AssessmentResult) -> str:
    q = result.question
    ex = result.execution
    qa = result.quality

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"ASSESSMENT REPORT — {q.title} ({q.id})")
    lines.append(f"Language: {result.language}")
    lines.append("=" * 70)

    lines.append("\n1. EXECUTION")
    if ex.compile_error:
        lines.append(f"   Compilation failed:\n     {ex.compile_error}")
    else:
        lines.append(f"   {ex.passed_count}/{len(ex.outcomes)} test cases passed")
        for o in ex.outcomes:
            status = "PASS" if o.passed else "FAIL"
            lines.append(f"     [{status}] {o.name}: expected {o.expected!r}, got {o.actual!r}")
            if o.error:
                lines.append(f"            error: {o.error}")

    lines.append(f"\n2. CODE QUALITY  (engine: {result.quality_engine})")
    for c in qa.criteria:
        lines.append(f"     {c.name:<12} {c.score}/5 — {c.comment}")
    lines.append(f"     {'overall':<12} {qa.overall_score}/5")
    if qa.strengths:
        lines.append("   Strengths:")
        lines.extend(f"     + {s}" for s in qa.strengths)
    if qa.weaknesses:
        lines.append("   Weaknesses:")
        lines.extend(f"     - {w}" for w in qa.weaknesses)
    lines.append("   Summary:")
    lines.append(f"     {qa.summary}")

    lines.append("\n3. VERDICT")
    lines.append(f"   >>> {result.verdict} <<<")
    lines.append(f"   {result.reason}")

    if result.usage is not None:
        u = result.usage
        cost = f"${u.cost_usd:.4f}" if u.priced else "n/a (unknown model)"
        lines.append("\n4. JUDGE COST")
        lines.append(
            f"   {u.input_tokens} in + {u.output_tokens} out"
            f" (cache read {u.cache_read_input_tokens}) tokens on {u.model} = {cost}"
        )

    lines.append("=" * 70)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assess a candidate's coding submission (Phase 1: hard-coded question)."
    )
    parser.add_argument("submission", help="Path to the candidate's source file.")
    parser.add_argument(
        "--language",
        choices=sorted(LANGUAGES),
        help="Override language detection (otherwise inferred from file extension).",
    )
    args = parser.parse_args(argv)

    path = Path(args.submission)
    if not path.is_file():
        parser.error(f"submission not found: {path}")

    language = args.language or detect_language(path.name)
    if language is None:
        parser.error(
            f"could not detect language from {path.name!r}; pass --language explicitly."
        )

    source = path.read_text()
    result = assess(source, language, HARDCODED_QUESTION)
    print(format_report(result))
    return {"PASS": 0, "FAIL": 1, "ERROR": 2}.get(result.verdict, 1)


if __name__ == "__main__":
    sys.exit(main())
