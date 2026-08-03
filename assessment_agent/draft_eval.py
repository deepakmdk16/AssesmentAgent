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

The harness also runs the T3 *differentiation* cases: one brief drafted at easy /
medium / hard, asserting the calibration levers (`constraints` size bound,
`required_complexity`) actually diverge across tiers. The per-draft calibration guard
checks each draft against its own requested tier; this is the complementary check that
"hard" comes out harder than "easy" at all.
"""

from __future__ import annotations

import os
import sys

from .agent import assess
from .authoring import _complexity_rank, _parse_size_bound, draft_question
from .draft_eval_cases import (
    DIFFERENTIATION_CASES,
    DRAFT_EVAL_CASES,
    DifferentiationCase,
    DraftEvalCase,
)
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

    # Authoring/drafting path — the floor stays HARD here, so no degrade_authoring.
    question, _ = question_from_dict(result.question)
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


# Tier order for the differentiation check; also what makes the dict ordering
# below deterministic whatever order a case lists its tiers in.
_TIER_ORDER = {"easy": 0, "medium": 1, "hard": 2}

# The easy→hard size separation the differentiation verdict demands. 10× matches
# `_check_set_parity`'s drift threshold: siblings within 10× count as "the same
# difficulty" there, so tiers must clear at least that to count as different.
_SIZE_SEPARATION_FACTOR = 10.0


def _differentiation_verdict(
    levers: dict[str, tuple[float | None, int | None]],
) -> tuple[str, str]:
    """OK/FAIL for one brief's tier sweep; `levers` maps tier -> (size bound,
    complexity rank) as parsed off each tier's draft.

    OK needs at least one strict easy→hard separation: a higher complexity
    rank, a >=10x larger size bound, or hard stating a big bound (>=1e5) where
    easy states none the parser can see (bounds < 1e3 are unparseable by
    design). A size bound that *shrinks* as tiers rise is an inversion and
    fails outright. A complexity rank that shrinks is deliberately NOT an
    inversion — a hard problem's insight can BE a low bound at a huge N (the
    calibration guard says the same) — it just doesn't count as separation, so
    without size divergence it still fails as indistinguishable.
    """
    tiers = sorted(levers, key=lambda t: _TIER_ORDER.get(t, 99))
    if len(tiers) < 2:
        return "FAIL", "needs at least two tiers to compare"

    for lo, hi in zip(tiers, tiers[1:], strict=False):
        n_lo, n_hi = levers[lo][0], levers[hi][0]
        if n_lo is not None and n_hi is not None and n_hi < n_lo:
            return "FAIL", (
                f"inverted: {hi} size bound N≈{n_hi:g} is below {lo}'s N≈{n_lo:g}"
            )

    lo, hi = tiers[0], tiers[-1]
    (n_lo, r_lo), (n_hi, r_hi) = levers[lo], levers[hi]
    separations = []
    if r_lo is not None and r_hi is not None and r_hi > r_lo:
        separations.append(f"required_complexity rank rises {r_lo}→{r_hi}")
    if n_lo is not None and n_hi is not None and n_hi >= _SIZE_SEPARATION_FACTOR * n_lo:
        separations.append(f"size bound rises N≈{n_lo:g}→{n_hi:g}")
    if n_lo is None and n_hi is not None and n_hi >= 1e5:
        separations.append(f"{hi} states N≈{n_hi:g}; {lo} states nothing parseable")
    if separations:
        return "OK", f"{lo} vs {hi}: " + "; ".join(separations)
    return "FAIL", (
        f"indistinguishable: {lo} {levers[lo]} vs {hi} {levers[hi]} — "
        "no lever separates the tiers"
    )


def _check_differentiation(case: DifferentiationCase) -> tuple[str, str]:
    """Draft one brief at each tier and require the levers to diverge."""
    levers: dict[str, tuple[float | None, int | None]] = {}
    for tier in case.tiers:
        result = draft_question(case.brief, language=case.language, difficulty=tier)
        if result.question is None:
            # Same SKIP semantics as _check: only "no backend configured" skips.
            if provider() == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
                return "SKIP", "no ANTHROPIC_API_KEY"
            return "FAIL", f"{tier}: no usable question: " + "; ".join(result.warnings)
        levers[tier] = (
            _parse_size_bound(result.question.get("constraints") or ""),
            _complexity_rank(result.question.get("required_complexity")),
        )
    return _differentiation_verdict(levers)


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
    for diff_case in DIFFERENTIATION_CASES:
        status, detail = _check_differentiation(diff_case)
        if status == "FAIL":
            failed += 1
        elif status == "SKIP":
            skipped += 1
        rows.append((diff_case.id, diff_case.language, status, detail))

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
