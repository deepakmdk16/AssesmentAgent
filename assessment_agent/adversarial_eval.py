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
from .questions import QUESTIONS


def _check(case: AdversarialEvalCase) -> tuple[str, str]:
    """Probe one correct reference. Returns (status, detail); status is OK/FAIL/SKIP."""
    report = probe_adversarial(
        question=QUESTIONS[case.question_id],
        language=case.language,
        source=case.source,
    )
    if report.probed == 0:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return "SKIP", "no ANTHROPIC_API_KEY"
        return "FAIL", f"generated/ran 0 cases: {report.summary}"
    if report.findings:
        kinds = ", ".join(f"{f.kind}:{f.name}" for f in report.findings)
        return "FAIL", f"{len(report.findings)} false positive(s) on a correct solution ({kinds})"
    return "OK", f"probed {report.probed}, no crash/timeout"


def main(argv: list[str] | None = None) -> int:
    rows = []
    failed = 0
    skipped = 0
    for case in ADVERSARIAL_EVAL_CASES:
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
        print("All cases skipped — set ANTHROPIC_API_KEY to actually exercise the probe.")
        return 0

    passed = len(rows) - failed - skipped
    tail = f", {skipped} skipped" if skipped else ""
    print(f"Adversarial anchors: {passed}/{len(rows) - skipped} passed{tail}.")
    if failed:
        print("A correct solution drew a finding (false positive) — investigate the generator.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
