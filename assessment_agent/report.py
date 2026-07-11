"""Render an assessment result as a single PDF report (Phase 2).

One document per candidate, containing everything an interviewer needs to
review the submission in isolation:

1. the question — prompt, constraints, and the worked example;
2. the candidate's code, verbatim;
3. every test case — input, expected, actual, and status (PASS/TLE/FAIL);
4. coverage — cases passed, weighted score, and the PASS/FAIL/ERROR verdict;
5. the code-quality summary — strengths, weaknesses, and per-criterion scores
   (omitted with a note when the judge was skipped because the code didn't run).

Rendering is deterministic and dependency-light (reportlab, pure Python).
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .agent import AssessmentResult
from .constants import SKIPPED_ENGINE

_VERDICT_HEX = {"PASS": "#1a7f37", "FAIL": "#b91c1c", "ERROR": "#b45309"}
_MAX_CELL_CHARS = 160  # clip large I/O (e.g. the generated performance case)


def _clip(text: str, limit: int = _MAX_CELL_CHARS) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " …[truncated]"


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": base["Title"],
        "h2": ParagraphStyle("h2", parent=base["Heading2"], spaceBefore=14),
        "body": base["BodyText"],
        "small": ParagraphStyle("small", parent=base["BodyText"], fontSize=8, leading=10),
        "code": ParagraphStyle(
            "code", parent=base["Code"], fontSize=8, leading=10, alignment=TA_LEFT
        ),
    }


def build_report_pdf(
    result: AssessmentResult,
    out_path: str | Path,
    candidate: str = "Candidate",
) -> Path:
    """Render `result` to a PDF at `out_path`; returns the written path."""
    out_path = Path(out_path)
    s = _styles()
    story: list = []

    def h2(text: str) -> None:
        story.append(Paragraph(escape(text), s["h2"]))

    def body(text: str) -> None:
        story.append(Paragraph(escape(text), s["body"]))

    # --- Header ---------------------------------------------------------------
    q = result.question
    color = _VERDICT_HEX.get(result.verdict, "#000000")
    story.append(Paragraph(f"Assessment Report — {escape(candidate)}", s["title"]))
    story.append(
        Paragraph(
            f"{escape(q.title)} &nbsp;·&nbsp; "
            f'<font color="{color}"><b>{result.verdict}</b></font> &nbsp;·&nbsp; '
            f"{result.score_pct:.0f}% "
            f"({result.points_earned:g}/{result.points_total:g} pts, "
            f"pass ≥ {result.pass_threshold_pct:.0f}%)",
            s["body"],
        )
    )
    body(result.reason)

    # --- 1. Question ----------------------------------------------------------
    h2("1. Question")
    body(q.prompt)
    h2("Constraints")
    body(q.constraints)
    if q.example_input is not None or q.example_output is not None:
        h2("Example")
        story.append(Preformatted(f"Input:\n{q.example_input or ''}", s["code"]))
        story.append(Preformatted(f"Output:\n{q.example_output or ''}", s["code"]))

    # --- 2. Candidate code ----------------------------------------------------
    h2(f"2. Candidate code ({result.language})")
    story.append(Preformatted(result.source.rstrip("\n"), s["code"]))

    # --- 3. Test cases --------------------------------------------------------
    h2("3. Test cases")
    header = ["Case", "Cat.", "Status", "Input", "Expected", "Actual", "Time"]
    rows = [[Paragraph(f"<b>{escape(c)}</b>", s["small"]) for c in header]]
    for o in result.execution.outcomes:
        status = "PASS" if o.passed else ("TLE" if o.timed_out else "FAIL")
        rows.append(
            [
                Paragraph(escape(o.name), s["small"]),
                Paragraph(escape(o.category[:4]), s["small"]),
                Paragraph(escape(status), s["small"]),
                Paragraph(escape(_clip(o.stdin)), s["small"]),
                Paragraph(escape(_clip(o.expected)), s["small"]),
                Paragraph(escape(_clip(o.actual)), s["small"]),
                Paragraph(f"{o.duration_s:.2f}s", s["small"]),
            ]
        )
    if not result.execution.outcomes:
        body("No test cases were run.")
    else:
        table = Table(
            rows,
            colWidths=[
                0.9 * inch,
                0.4 * inch,
                0.5 * inch,
                1.5 * inch,
                1.1 * inch,
                1.1 * inch,
                0.5 * inch,
            ],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d0d0")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
    if result.execution.compile_error:
        body(f"Compile error: {result.execution.compile_error}")
    if result.execution.infra_error:
        body(f"Could not run: {result.execution.infra_error}")

    # --- 4. Coverage ----------------------------------------------------------
    h2("4. Coverage & verdict")
    ex = result.execution
    correctness = ex.by_category("correctness")
    performance = ex.by_category("performance")
    c_pass = sum(1 for o in correctness if o.passed)
    p_pass = sum(1 for o in performance if o.passed)
    body(
        f"Correctness: {c_pass}/{len(correctness)} cases · "
        f"Performance: {p_pass}/{len(performance)} cases · "
        f"Weighted score: {result.score_pct:.0f}% · Verdict: {result.verdict}"
    )

    # --- 5. Code quality ------------------------------------------------------
    h2("5. Code quality — strengths & weaknesses")
    quality = result.quality
    if result.quality_engine == SKIPPED_ENGINE:
        body("Not assessed — the submission did not execute (compile/runtime failure).")
    else:
        meets = "yes" if quality.meets_time_constraints else "no"
        body(
            f"Estimated time complexity: {quality.time_complexity} "
            f"(meets constraints: {meets}). Overall: {quality.overall_score:g}/5. "
            f"[engine: {result.quality_engine}]"
        )
        for c in quality.criteria:
            body(f"• {c.name} — {c.score}/5: {c.comment}")
        if quality.strengths:
            h2("Strengths")
            for item in quality.strengths:
                body(f"+ {item}")
        if quality.weaknesses:
            h2("Weaknesses")
            for item in quality.weaknesses:
                body(f"− {item}")
        h2("Summary")
        body(quality.summary)

    spaced: list = []
    for element in story:
        spaced.append(element)
        spaced.append(Spacer(1, 4))

    SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        title=f"Assessment Report — {candidate}",
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
    ).build(spaced)
    return out_path
