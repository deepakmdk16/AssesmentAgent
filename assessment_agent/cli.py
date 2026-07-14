"""Command-line entry point: assess a candidate submission file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import AssessmentResult, assess, result_to_dict
from .constants import CORRECTNESS, ERROR, FAIL, PASS, PERFORMANCE
from .languages import LANGUAGES, detect_language
from .questions import HARDCODED_QUESTION, QUESTIONS


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
    elif ex.infra_error:
        lines.append(f"   Could not run: {ex.infra_error}")
    else:
        correctness = ex.by_category(CORRECTNESS)
        performance = ex.by_category(PERFORMANCE)
        c_pass = sum(1 for o in correctness if o.passed)
        lines.append(f"   Correctness: {c_pass}/{len(correctness)} cases passed")
        for o in correctness:
            status = "PASS" if o.passed else ("TLE" if o.timed_out else "FAIL")
            lines.append(
                f"     [{status}] {o.name} (weight {o.weight:g}, {o.duration_s:.3f}s): "
                f"expected {o.expected!r}, got {o.actual!r}"
            )
            if o.error:
                lines.append(f"            {o.error}")
        if performance:
            p_pass = sum(1 for o in performance if o.passed)
            lines.append(
                f"   Performance: {p_pass}/{len(performance)} cases passed "
                f"(large input sized to the constraints)"
            )
            for o in performance:
                status = "PASS" if o.passed else ("TLE" if o.timed_out else "FAIL")
                detail = f" — {o.error}" if o.error else f" in {o.duration_s:.3f}s"
                lines.append(f"     [{status}] {o.name} (weight {o.weight:g}){detail}")
        lines.append(
            f"   SCORE: {result.score_pct:.0f}% "
            f"({result.points_earned:g}/{result.points_total:g} points, "
            f"pass threshold {result.pass_threshold_pct:.0f}%)"
        )

    lines.append(f"\n2. CODE QUALITY  (engine: {result.quality_engine})")
    meets = "yes" if qa.meets_time_constraints else "NO"
    lines.append(f"     time complexity: {qa.time_complexity}  (meets constraints: {meets})")
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
        description="Assess a candidate's coding submission against a built-in question."
    )
    parser.add_argument("submission", help="Path to the candidate's source file.")
    parser.add_argument(
        "--candidate",
        metavar="NAME",
        help="Candidate name for the report title and email subject "
        "(defaults to the submission file's stem).",
    )
    parser.add_argument(
        "--question",
        choices=sorted(QUESTIONS),
        default=HARDCODED_QUESTION.id,
        help="Which built-in question to grade against (default: %(default)s).",
    )
    parser.add_argument(
        "--question-file",
        help="Grade against an interviewer-supplied question JSON file (Phase 2) "
        "instead of a built-in --question.",
    )
    parser.add_argument(
        "--language",
        choices=sorted(LANGUAGES),
        help="Override language detection (otherwise inferred from file extension).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full report as JSON (for storing/emailing) instead of text.",
    )
    parser.add_argument(
        "--report",
        metavar="PATH",
        help="Write the assessment as a PDF report to PATH (Phase 2).",
    )
    parser.add_argument(
        "--email",
        action="store_true",
        help="Email the PDF report to the configured recipient (Phase 2); "
        "needs SMTP_USERNAME/SMTP_PASSWORD in the environment.",
    )
    parser.add_argument(
        "--email-dry-run",
        action="store_true",
        help="Build the report email but do not send it (no SMTP credentials needed).",
    )
    parser.add_argument(
        "--to",
        metavar="EMAIL",
        help="Recipient for the emailed report (Phase 2); defaults to the built-in recipient.",
    )
    args = parser.parse_args(argv)

    path = Path(args.submission)
    if not path.is_file():
        parser.error(f"submission not found: {path}")

    language = args.language or detect_language(path.name)
    if language is None:
        parser.error(f"could not detect language from {path.name!r}; pass --language explicitly.")

    if args.question_file:
        from .loader import load_question

        question = load_question(args.question_file)
    else:
        question = QUESTIONS[args.question]

    source = path.read_text()
    result = assess(source, language, question)
    if args.json:
        print(json.dumps(result_to_dict(result), indent=2))
    else:
        print(format_report(result))

    if args.report or args.email or args.email_dry_run:
        candidate = args.candidate or path.stem
        _emit_report(result, candidate, args, parser)

    return {PASS: 0, FAIL: 1, ERROR: 2}.get(result.verdict, 1)


def _emit_report(
    result: AssessmentResult,
    candidate: str,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> None:
    """Render the PDF and optionally email it (Phase 2 side effects)."""
    import os
    import tempfile

    from .report import build_report_pdf

    if args.report:
        report_path = Path(args.report)
    else:
        fd, tmp = tempfile.mkstemp(prefix="assess_", suffix=".pdf")
        os.close(fd)
        report_path = Path(tmp)

    build_report_pdf(result, report_path, candidate=candidate)
    if args.report:
        print(f"Wrote PDF report: {report_path}")

    if args.email or args.email_dry_run:
        from .mailer import RECIPIENT, send_report

        recipient = args.to or RECIPIENT
        try:
            msg = send_report(
                report_path,
                candidate=candidate,
                verdict=result.verdict,
                score_pct=result.score_pct,
                recipient=recipient,
                dry_run=args.email_dry_run,
            )
        except RuntimeError as exc:
            parser.error(str(exc))
        if args.email_dry_run:
            print(
                f"[dry-run] Would email {candidate}'s report to {recipient} "
                f"(subject: {msg['Subject']!r}); not sent."
            )
        else:
            print(f"Emailed {candidate}'s report to {recipient}.")


if __name__ == "__main__":
    sys.exit(main())
