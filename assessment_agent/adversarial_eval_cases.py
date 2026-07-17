"""Fixed anchors for calibrating the adversarial test-gen probe (``adversarial.probe_adversarial``).

Each anchor pairs a built-in question with a **known-correct** reference solution (reused
from [eval_cases.py] so the sources stay in one place). The probe generates edge-case
inputs and reports only Tier-1 failures — a crash/timeout on a *valid* input — so on a
correct, robust solution it must find NONE. A regression where the generator emits a
malformed input (the false-positive ``[CRASH]`` bug fixed during the live smoke) would
surface here as a finding against a solution that is actually correct.

Like the drafting eval, this REQUIRES a live model — there is no offline heuristic — so
``assess-adversarial-eval`` skips every case without ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .eval_cases import EVAL_CASES


@dataclass(frozen=True)
class AdversarialEvalCase:
    id: str
    question_id: str
    language: str
    source: str
    note: str = ""


_CORRECT = {c.id: c for c in EVAL_CASES}

ADVERSARIAL_EVAL_CASES: tuple[AdversarialEvalCase, ...] = tuple(
    AdversarialEvalCase(
        id=eid,
        question_id=_CORRECT[eid].question_id,
        language=_CORRECT[eid].language,
        source=_CORRECT[eid].source,
        note=note,
    )
    for eid, note in (
        ("strong", "correct Kadane — the probe must report no false crash/timeout"),
        ("knapsack_good", "correct O(N*W) knapsack — no false positives"),
    )
)
