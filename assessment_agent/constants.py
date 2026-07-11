"""Shared domain vocabulary.

The pipeline passes a few states around as strings — test-case categories, the
final verdict, and the offline-judge label. Centralising them as named
constants with `Literal` types means a typo or a stale copy (e.g. an eval
comparing against an out-of-date engine label) becomes a type-checker error
instead of a silent runtime mismatch.
"""

from __future__ import annotations

from typing import Literal

# Test-case categories: correctness gates the answer; performance is the large,
# constraint-sized case whose timing catches too-slow solutions.
Category = Literal["correctness", "performance"]
CORRECTNESS: Category = "correctness"
PERFORMANCE: Category = "performance"

# Final assessment verdict.
Verdict = Literal["PASS", "FAIL", "ERROR"]
PASS: Verdict = "PASS"
FAIL: Verdict = "FAIL"
ERROR: Verdict = "ERROR"

# Quality-judge engine label used when no ANTHROPIC_API_KEY is set.
OFFLINE_ENGINE = "offline-heuristic"
# Quality-judge engine label when the judge was skipped entirely because the
# submission did not execute (compile/runtime failure) — a decided FAIL, so the
# LLM call would add nothing. See agent.assess.
SKIPPED_ENGINE = "skipped"
