"""Eval harness: run the judge over a fixed sample and report agreement.

Run it with different models to A/B them (the rubric should let a cheaper model
match a bigger one):

    ASSESSMENT_MODEL=claude-sonnet-4-6 uv run assess-eval
    ASSESSMENT_MODEL=claude-opus-4-8 ASSESSMENT_THINKING=adaptive uv run assess-eval

Verdicts are score-based (weighted tests vs. the pass threshold), so every
anchor is deterministic and holds regardless of the quality model — the A/B is
about the quality report (complexity, criteria, cost), not the verdict.
"""

from __future__ import annotations

import sys

from .agent import assess
from .constants import OFFLINE_ENGINE
from .eval_cases import EVAL_CASES
from .questions import QUESTIONS


def _norm_cx(s: str) -> str:
    """Normalize a complexity string for comparison: keep only alphanumerics and
    lowercase, so 'O(N*W)', 'O(n*w)' and 'O(nW)' all collapse to 'onw'."""
    return "".join(ch for ch in s.lower() if ch.isalnum())


def main(argv: list[str] | None = None) -> int:
    rows = []
    asserted = 0
    matched = 0
    engine = ""

    # Labeled quality-agreement tallies (reported, not gated): complexity is only
    # checked when a real model ran; meets-constraints is checked always.
    cx_checked = cx_matched = 0
    mt_checked = mt_matched = 0

    total_cost = 0.0
    total_in = 0
    total_out = 0
    total_cache_read = 0
    priced_cases = 0

    for case in EVAL_CASES:
        result = assess(case.source, case.language, QUESTIONS[case.question_id])
        engine = result.quality_engine
        real_model = engine != OFFLINE_ENGINE
        expected = case.expected_verdict

        if expected is None:
            status = "report"
        elif result.verdict == expected:
            status = "OK"
            asserted += 1
            matched += 1
        else:
            status = "MISMATCH"
            asserted += 1

        # Complexity label — meaningful only with a real judge.
        if case.expected_complexity is not None and real_model:
            cx_checked += 1
            if _norm_cx(case.expected_complexity) == _norm_cx(result.quality.time_complexity):
                cx_cell = "OK"
                cx_matched += 1
            else:
                cx_cell = "x"
        else:
            cx_cell = "-"

        # Meets-constraints label — empirically grounded, checked in both modes.
        if case.expected_meets_constraints is not None:
            mt_checked += 1
            if result.quality.meets_time_constraints == case.expected_meets_constraints:
                mt_cell = "OK"
                mt_matched += 1
            else:
                mt_cell = "x"
        else:
            mt_cell = "-"

        if result.usage is not None:
            u = result.usage
            cost_cell = f"${u.cost_usd:.4f}" if u.priced else "n/a"
            if u.priced:
                total_cost += u.cost_usd
                total_in += u.input_tokens
                total_out += u.output_tokens
                total_cache_read += u.cache_read_input_tokens
                priced_cases += 1
        else:
            cost_cell = "-"

        rows.append(
            (
                case.id,
                case.language,
                result.verdict,
                f"{result.score_pct:.0f}%",
                expected or "-",
                status,
                cx_cell,
                mt_cell,
                cost_cell,
                case.note,
            )
        )

    print(f"\nEval engine: {engine}\n")
    header = ("case", "lang", "verdict", "test%", "expect", "status", "cx", "meets", "cost")
    widths = (20, 11, 8, 6, 7, 9, 4, 5, 9)
    line = "  ".join(f"{header[i]:<{widths[i]}}" for i in range(len(widths))) + "  note"
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(f"{r[i]:<{widths[i]}}" for i in range(len(widths))) + "  " + r[-1])

    print(f"\nAsserted verdicts: {matched}/{asserted} matched.")
    if asserted != matched:
        print("Some anchors mismatched — investigate before trusting this config.")

    print(
        f"Quality labels (reported, not gated): complexity {cx_matched}/{cx_checked} matched"
        + ("" if cx_checked else " (real model only)")
        + f", meets-constraints {mt_matched}/{mt_checked} matched."
    )

    if priced_cases:
        avg = total_cost / priced_cases
        print(
            f"\nCost ({engine}): ${total_cost:.4f} over {priced_cases} judged"
            f" ({total_in} in / {total_out} out tokens, {total_cache_read} cache-read)."
        )
        print(f"Average per candidate: ${avg:.4f}  →  ~${avg * 1000:.2f} per 1,000 candidates.")
    else:
        print("\nNo priced judge calls (offline heuristic) — set ANTHROPIC_API_KEY for real cost.")

    return 0 if matched == asserted else 1


if __name__ == "__main__":
    sys.exit(main())
