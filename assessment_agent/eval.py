"""Eval harness: run the judge over a fixed sample and report agreement.

Run it with different models to A/B them (the rubric should let a cheaper model
match a bigger one):

    ASSESSMENT_MODEL=claude-sonnet-4-6 uv run assess-eval
    ASSESSMENT_MODEL=claude-opus-4-8 ASSESSMENT_THINKING=adaptive uv run assess-eval

Asserted cases (strong/buggy) check the verdict; borderline cases are
report-only so you can eyeball scores and tune PASS_QUALITY_THRESHOLD.
Without an API key it runs the offline heuristic (useful only for the
deterministic anchors, not for real calibration).
"""

from __future__ import annotations

import sys

from .agent import assess
from .eval_cases import EVAL_CASES


def main(argv: list[str] | None = None) -> int:
    rows = []
    asserted = 0
    matched = 0
    engine = ""

    total_cost = 0.0
    total_in = 0
    total_out = 0
    total_cache_read = 0
    priced_cases = 0

    for case in EVAL_CASES:
        result = assess(case.source, case.language)
        engine = result.quality_engine
        overall = result.quality.overall_score
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

        rows.append((case.id, case.language, result.verdict, f"{overall:.1f}",
                     expected or "-", status, cost_cell, case.note))

    print(f"\nEval engine: {engine}\n")
    header = ("case", "lang", "verdict", "score", "expect", "status", "cost")
    widths = (20, 11, 8, 6, 7, 9, 9)
    line = "  ".join(f"{header[i]:<{widths[i]}}" for i in range(len(widths))) + "  note"
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(f"{r[i]:<{widths[i]}}" for i in range(len(widths))) + "  " + r[7])

    print(f"\nAsserted verdicts: {matched}/{asserted} matched.")
    if asserted != matched:
        print("Some anchors mismatched — investigate before trusting this config.")

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
