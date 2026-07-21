"""Eval harness for the question-drafting endpoint (``authoring.draft_question``).

    ASSESSMENT_MODEL=claude-sonnet-4-6 uv run assess-draft-eval

Drafting has no offline heuristic, so without ``ANTHROPIC_API_KEY`` every case reports
``SKIP`` and the harness exits 0 (there is nothing to assert). With a key, each brief is
drafted and checked:

  - a usable, validated question came back (``question`` is not ``None``);
  - it has >= ``min_correctness_cases`` correctness cases and >= 1 performance case;
  - the drafted reference solution, graded against its own drafted suite, PASSES 100%.

The last check is the important one: a draft whose own reference can't pass is broken —
exactly the failure the live cross-repo smokes kept surfacing (a reference split across a
header, a hand-computed wrong example). This makes that regression catchable without a
live smoke.
"""

from __future__ import annotations

import os
import sys

from .agent import assess
from .authoring import draft_question
from .draft_eval_cases import DRAFT_EVAL_CASES, DraftEvalCase
from .llm import provider
from .loader import question_from_dict


def _check(case: DraftEvalCase) -> tuple[str, str]:
    """Draft one brief and validate it. Returns (status, detail); status is OK/FAIL/SKIP."""
    result = draft_question(
        case.brief,
        language=case.language,
        difficulty=case.difficulty,
        target_complexity=case.target_complexity,
    )
    if result.question is None:
        # SKIP means "no backend was configured", not "the backend failed". With a
        # local provider selected there IS a backend, so an empty draft is a real
        # FAIL — otherwise a broken local model would report a clean sheet.
        if provider() == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
            return "SKIP", "no ANTHROPIC_API_KEY"
        return "FAIL", "no usable question: " + "; ".join(result.warnings)

    question = question_from_dict(result.question)
    n_corr = sum(1 for tc in question.test_cases if tc.category == "correctness")
    n_perf = sum(1 for tc in question.test_cases if tc.category == "performance")
    if n_corr < case.min_correctness_cases:
        return "FAIL", f"only {n_corr} correctness cases (< {case.min_correctness_cases})"
    if n_perf < 1:
        return "FAIL", "no performance case"
    if not result.reference_solution:
        return "FAIL", "no reference solution returned"

    graded = assess(
        result.reference_solution,
        result.reference_language or case.language,
        question,
    )
    if graded.verdict != "PASS":
        return "FAIL", f"reference graded {graded.verdict} {graded.score_pct:.0f}% on its own suite"
    return "OK", f"{n_corr} corr + {n_perf} perf, reference PASS {graded.score_pct:.0f}%"


def main(argv: list[str] | None = None) -> int:
    rows = []
    failed = 0
    skipped = 0
    for case in DRAFT_EVAL_CASES:
        status, detail = _check(case)
        if status == "FAIL":
            failed += 1
        elif status == "SKIP":
            skipped += 1
        rows.append((case.id, case.language, status, detail))

    print()
    for cid, lang, status, detail in rows:
        print(f"{cid:<16} {lang:<11} {status:<5} {detail}")
    print()

    if skipped == len(rows):
        print("All cases skipped — set ANTHROPIC_API_KEY to actually exercise drafting.")
        return 0

    passed = len(rows) - failed - skipped
    tail = f", {skipped} skipped" if skipped else ""
    print(f"Draft anchors: {passed}/{len(rows) - skipped} passed{tail}.")
    if failed:
        print("Some drafts were unusable — investigate before trusting this model/config.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
