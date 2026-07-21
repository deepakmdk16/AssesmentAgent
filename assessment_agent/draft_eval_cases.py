"""Fixed briefs for calibrating the question-drafting endpoint (``authoring.draft_question``).

Unlike the judge eval ([eval_cases.py]), drafting has **no offline heuristic** — it
requires a live model — so ``assess-draft-eval`` reports every case as skipped without
``ANTHROPIC_API_KEY``. Each case names the structural properties a usable draft must
have; the harness's strongest check is that the drafted reference solution aces its own
drafted suite (the exact failure class the live smokes kept catching: a reference that
won't compile or doesn't pass).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DraftEvalCase:
    id: str
    brief: str
    language: str
    difficulty: str | None = None
    target_complexity: str | None = None
    # A usable question needs real edge coverage, not just one case. The prompt
    # asks for >= 6 and aims at 8-10; measured counts are 5-8 locally and 7-10 on
    # Sonnet. The floor sits at 4 deliberately: high enough to catch the "several"
    # regression (a vague quantifier let a weaker model ship 3), low enough to
    # absorb one dropped case plus normal run-to-run variance.
    min_correctness_cases: int = 4
    note: str = ""


DRAFT_EVAL_CASES: tuple[DraftEvalCase, ...] = (
    DraftEvalCase(
        id="two_sum",
        brief=(
            "Given an array of integers and a target value, return the indices of the "
            "two distinct elements that sum to the target. Exactly one solution exists."
        ),
        language="python",
        difficulty="easy",
        target_complexity="O(n)",
        note="classic hashmap problem; the reference must beat the O(n^2) perf case",
    ),
    DraftEvalCase(
        id="reverse_words",
        brief=(
            "Read a line of text and print its words in reverse order, collapsing runs "
            "of whitespace to a single space and trimming leading/trailing space."
        ),
        language="python",
        difficulty="easy",
        note="string manipulation; edges: empty line, single word, extra spaces",
    ),
    DraftEvalCase(
        id="count_islands",
        brief=(
            "Given a grid of 0s and 1s, count the number of connected groups of 1s, "
            "where cells connect 4-directionally (up/down/left/right)."
        ),
        language="python",
        difficulty="medium",
        target_complexity="O(n*m)",
        note="flood-fill over a 2D input — stresses drafting on grid-shaped stdin",
    ),
)
