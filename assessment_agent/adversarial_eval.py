"""Eval harness for the adversarial test-gen probe (``adversarial.probe_adversarial``).

    ASSESSMENT_MODEL=claude-sonnet-4-6 uv run assess-adversarial-eval

The probe has no offline heuristic, so without ``ANTHROPIC_API_KEY`` every case reports
``SKIP`` and the harness exits 0. With a key, each anchor runs the probe against a
**known-correct** reference and checks:

  - the probe actually generated and ran >= 1 input (``probed`` >= 1);
  - it reported ZERO findings — a correct solution must not crash or time out on a valid
    input, so any finding here is a false positive (the generator emitting a malformed
    input), the exact regression the live smoke caught and fixed.
"""

from __future__ import annotations

import os
import sys

from .adversarial import probe_adversarial
from .adversarial_eval_cases import ADVERSARIAL_EVAL_CASES, AdversarialEvalCase
from .llm import provider
from .questions import QUESTIONS


def _check(case: AdversarialEvalCase) -> tuple[str, str]:
    """Probe one correct reference. Returns (status, detail); status is OK/FAIL/SKIP."""
    report = probe_adversarial(
        question=QUESTIONS[case.question_id],
        language=case.language,
        source=case.source,
    )
    if report.probed == 0:
        # SKIP means "no backend was configured", not "the backend failed" — see
        # the same guard in draft_eval.py.
        if provider() == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
            return "SKIP", "no ANTHROPIC_API_KEY"
        # A distinct failure from a false positive: the probe ran 0 cases, so the
        # generator produced nothing usable (a timeout or unparseable output), NOT
        # a finding on correct code. Kept separate so the summary points at the
        # right thing (see main()).
        return (
            "EMPTY",
            f"generated/ran 0 cases (a timeout or unparseable generation): {report.summary}",
        )
    if report.findings:
        kinds = ", ".join(f"{f.kind}:{f.name}" for f in report.findings)
        return (
            "FINDING",
            f"{len(report.findings)} false positive(s) on a correct solution ({kinds})",
        )
    return "OK", f"probed {report.probed}, no crash/timeout"


def main(argv: list[str] | None = None) -> int:
    rows = []
    finding_fails = 0
    empty_fails = 0
    skipped = 0
    for case in ADVERSARIAL_EVAL_CASES:
        status, detail = _check(case)
        display = status
        if status == "FINDING":
            finding_fails += 1
            display = "FAIL"
        elif status == "EMPTY":
            empty_fails += 1
            display = "FAIL"
        elif status == "SKIP":
            skipped += 1
        rows.append((case.id, case.language, display, detail))

    print()
    for cid, lang, status, detail in rows:
        print(f"{cid:<16} {lang:<11} {status:<5} {detail}")
    print()

    if skipped == len(rows):
        print("All cases skipped — set ANTHROPIC_API_KEY to actually exercise the probe.")
        return 0

    failed = finding_fails + empty_fails
    passed = len(rows) - failed - skipped
    tail = f", {skipped} skipped" if skipped else ""
    print(f"Adversarial anchors: {passed}/{len(rows) - skipped} passed{tail}.")
    # Point at the actual cause: a false positive and a 0-case generation are
    # different bugs (the old summary always blamed the former).
    if finding_fails:
        print("A correct solution drew a finding (false positive) — investigate the generator.")
    if empty_fails:
        print(
            "A case generated/ran 0 inputs (a timeout or unparseable generation, not a "
            "finding) — check ASSESS_LLM_TIMEOUT_S and the model's structured output."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
