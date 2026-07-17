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
    HRFlowable,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .agent import AssessmentResult
from .constants import SKIPPED_ENGINE

# --- Palette --------------------------------------------------------------
_INK = colors.HexColor("#1f2328")
_MUTED = colors.HexColor("#57606a")
_RULE = colors.HexColor("#d0d7de")
_HEAD_BG = colors.HexColor("#f6f8fa")
_STRIPE = colors.HexColor("#f6f8fa")
_CODE_BG = colors.HexColor("#f6f8fa")

# Strong verdict colour plus a soft tint for the banner background.
_VERDICT = {
    "PASS": ("#1a7f37", "#dafbe1"),
    "FAIL": ("#b91c1c", "#ffebe9"),
    "ERROR": ("#9a6700", "#fff8c5"),
}
_STATUS_HEX = {"PASS": "#1a7f37", "FAIL": "#b91c1c", "TLE": "#9a6700"}

_MARGIN = 0.8 * inch
_TOP_MARGIN = 0.7 * inch
_CONTENT_W = LETTER[0] - 2 * _MARGIN
_MAX_CELL_CHARS = 160  # clip large I/O (e.g. the generated performance case)


def _clip(text: str, limit: int = _MAX_CELL_CHARS) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " …[truncated]"


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title",
            parent=base["Title"],
            fontSize=20,
            leading=24,
            textColor=_INK,
            spaceAfter=2,
            alignment=TA_LEFT,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["BodyText"],
            fontSize=10,
            leading=14,
            textColor=_MUTED,
            spaceAfter=2,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontSize=13,
            leading=16,
            textColor=_INK,
            spaceBefore=18,
            spaceAfter=2,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["BodyText"],
            fontSize=9.5,
            leading=14,
            textColor=_INK,
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base["BodyText"],
            fontSize=9.5,
            leading=13,
            textColor=_INK,
            leftIndent=14,
            bulletIndent=2,
            spaceAfter=3,
        ),
        "cell": ParagraphStyle(
            "cell",
            parent=base["BodyText"],
            fontSize=8,
            leading=10,
            textColor=_INK,
        ),
        "cellhdr": ParagraphStyle(
            "cellhdr",
            parent=base["BodyText"],
            fontSize=8,
            leading=10,
            textColor=_INK,
            fontName="Helvetica-Bold",
        ),
        "code": ParagraphStyle(
            "code",
            parent=base["Code"],
            fontSize=8,
            leading=11,
            textColor=_INK,
            alignment=TA_LEFT,
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
        """A section heading followed by a thin rule."""
        story.append(Paragraph(escape(text), s["h2"]))
        story.append(
            HRFlowable(width="100%", thickness=0.6, color=_RULE, spaceBefore=3, spaceAfter=8)
        )

    def body(text: str) -> None:
        story.append(Paragraph(escape(text), s["body"]))

    def bullet(text: str, marker: str = "•") -> None:
        """Unlike `body`/`h2`, this does NOT escape — callers pass markup (e.g.
        <b>…</b> around an escaped fragment). Every caller must therefore escape
        its own interpolated values."""
        story.append(Paragraph(text, s["bullet"], bulletText=marker))

    def code_box(text: str) -> None:
        """Monospace text in a light, padded box."""
        inner = Preformatted(text, s["code"])
        box = Table([[inner]], colWidths=[_CONTENT_W])
        box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _CODE_BG),
                    ("BOX", (0, 0), (-1, -1), 0.5, _RULE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(box)

    # --- Header + verdict banner ---------------------------------------------
    q = result.question
    strong_hex, tint_hex = _VERDICT.get(result.verdict, ("#1f2328", "#f6f8fa"))
    story.append(Paragraph("Assessment Report", s["title"]))
    story.append(Paragraph(f"{escape(candidate)} &nbsp;·&nbsp; {escape(q.title)}", s["subtitle"]))
    story.append(Spacer(1, 10))

    banner_text = (
        f'<font size="13"><b>{result.verdict}</b></font> &nbsp;·&nbsp; '
        f"{result.score_pct:.0f}% "
        f"({result.points_earned:g}/{result.points_total:g} pts, "
        f"pass ≥ {result.pass_threshold_pct:.0f}%)"
    )
    banner = Table(
        [
            [
                Paragraph(
                    banner_text,
                    ParagraphStyle(
                        "banner",
                        fontSize=11,
                        leading=15,
                        textColor=colors.HexColor(strong_hex),
                    ),
                )
            ]
        ],
        colWidths=[_CONTENT_W],
    )
    banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(tint_hex)),
                ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(strong_hex)),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(banner)
    story.append(Spacer(1, 4))
    body(result.reason)

    # --- 1. Question ----------------------------------------------------------
    h2("1. Question")
    body(q.prompt)
    story.append(
        Paragraph(
            "Constraints",
            ParagraphStyle(
                "sub",
                parent=s["body"],
                fontName="Helvetica-Bold",
                spaceBefore=4,
                spaceAfter=2,
            ),
        )
    )
    body(q.constraints)
    if q.example_input is not None or q.example_output is not None:
        story.append(
            Paragraph(
                "Example",
                ParagraphStyle(
                    "sub2",
                    parent=s["body"],
                    fontName="Helvetica-Bold",
                    spaceBefore=4,
                    spaceAfter=2,
                ),
            )
        )
        code_box(f"Input:\n{q.example_input or ''}\n\nOutput:\n{q.example_output or ''}")

    # --- 2. Candidate code ----------------------------------------------------
    h2(f"2. Candidate code ({result.language})")
    code_box(result.source.rstrip("\n"))

    # --- 3. Test cases --------------------------------------------------------
    h2("3. Test cases")
    header = ["Case", "Cat.", "Status", "Input", "Expected", "Actual", "Time"]
    rows = [[Paragraph(c, s["cellhdr"]) for c in header]]
    status_rows: list[str] = []
    for o in result.execution.outcomes:
        status = "PASS" if o.passed else ("TLE" if o.timed_out else "FAIL")
        status_rows.append(status)
        st_hex = _STATUS_HEX.get(status, "#1f2328")
        rows.append(
            [
                Paragraph(escape(o.name), s["cell"]),
                Paragraph(escape(o.category[:4]), s["cell"]),
                Paragraph(f'<font color="{st_hex}"><b>{status}</b></font>', s["cell"]),
                Paragraph(escape(_clip(o.stdin)), s["cell"]),
                Paragraph(escape(_clip(o.expected)), s["cell"]),
                Paragraph(escape(_clip(o.actual)), s["cell"]),
                Paragraph(f"{o.duration_s:.2f}s", s["cell"]),
            ]
        )
    if not result.execution.outcomes:
        body("No test cases were run.")
    else:
        table = Table(
            rows,
            colWidths=[
                0.85 * inch,
                0.45 * inch,
                0.6 * inch,
                1.65 * inch,
                1.1 * inch,
                1.1 * inch,
                0.55 * inch,
            ],
            repeatRows=1,
        )
        style = [
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, _RULE),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, _RULE),
            ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        # Zebra striping on the body rows for readability.
        for i in range(1, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), _STRIPE))
        table.setStyle(TableStyle(style))
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
        f"Correctness: {c_pass}/{len(correctness)} cases &nbsp;·&nbsp; "
        f"Performance: {p_pass}/{len(performance)} cases &nbsp;·&nbsp; "
        f"Weighted score: {result.score_pct:.0f}% &nbsp;·&nbsp; "
        f'Verdict: <font color="{strong_hex}"><b>{result.verdict}</b></font>'
    )

    # --- 5. Code quality ------------------------------------------------------
    h2("5. Code quality — strengths & weaknesses")
    quality = result.quality
    if result.quality_engine == SKIPPED_ENGINE:
        body("Not assessed — the submission did not execute (compile/runtime failure).")
    else:
        meets = "yes" if quality.meets_time_constraints else "no"
        required = (
            f"Required: <b>{escape(q.required_complexity)}</b> (advisory) &nbsp;·&nbsp; "
            if q.required_complexity
            else ""
        )
        body(
            f"Estimated time complexity: <b>{escape(quality.time_complexity)}</b> "
            f"(meets constraints: {meets}) &nbsp;·&nbsp; "
            f"{required}"
            f"Overall: <b>{quality.overall_score:g}/5</b> &nbsp;·&nbsp; "
            f"[engine: {escape(result.quality_engine)}]"
        )
        for c in quality.criteria:
            bullet(f"<b>{escape(c.name)}</b> — {c.score}/5: {escape(c.comment)}")
        if quality.strengths:
            story.append(
                Paragraph(
                    "Strengths",
                    ParagraphStyle(
                        "str",
                        parent=s["body"],
                        fontName="Helvetica-Bold",
                        spaceBefore=6,
                        spaceAfter=3,
                    ),
                )
            )
            for item in quality.strengths:
                bullet(escape(item), marker="+")
        if quality.weaknesses:
            story.append(
                Paragraph(
                    "Weaknesses",
                    ParagraphStyle(
                        "wk",
                        parent=s["body"],
                        fontName="Helvetica-Bold",
                        spaceBefore=6,
                        spaceAfter=3,
                    ),
                )
            )
            for item in quality.weaknesses:
                bullet(escape(item), marker="−")
        h2("Summary")
        body(quality.summary)

    # --- 6. Adversarial probes (advisory) -------------------------------------
    adv = result.adversarial
    if adv is not None:
        h2("6. Adversarial probes — advisory (does not affect the verdict)")
        body(f"{escape(adv.summary)} &nbsp;·&nbsp; [engine: {escape(adv.engine)}]")
        for f in adv.findings:
            bullet(
                f'<font color="{_STATUS_HEX.get("FAIL", "#b91c1c")}"><b>{escape(f.kind.upper())}</b></font> '
                f"— <b>{escape(f.name)}</b>: {escape(f.rationale)}",
                marker="•",
            )
            code_box(f"input: {_clip(f.stdin)}\n{_clip(f.detail)}")

    SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        title=f"Assessment Report — {candidate}",
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_TOP_MARGIN,
        bottomMargin=0.7 * inch,
    ).build(story)
    return out_path
