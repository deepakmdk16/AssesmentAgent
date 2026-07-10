---
name: add-question
description: Add or change a coding question in the Assessment Agent — defines the problem, constraints, weighted correctness + performance test cases (with an oracle), updates samples/eval/tests, and verifies. Use when adding a new question or changing the hard-coded one.
---

# Add a coding question

Adds a `Question` to the Assessment Agent following the project's conventions so
a new problem plugs into the existing scoring/verdict pipeline unchanged.

## When to use
- "add a question", "new coding problem", "change the question", or intake of an
  interviewer-supplied problem (Phase 2).

## Contract & conventions (do not deviate)
- Candidate programs read the test input from **stdin** and write to **stdout**.
- A `Question` ([questions.py](../../../assessment_agent/questions.py)) is:
  `id, title, prompt, constraints, test_cases, time_limit_s=2.0, pass_threshold=0.9`.
- `TestCase(name, stdin, expected, category="correctness"|"performance", weight=1.0)`.
- The verdict is **score-based**: PASS iff the weighted test score ≥
  `pass_threshold`. Code quality is reported, never gates. So the **test cases,
  weights, and threshold ARE the spec** — get them right.
- Weighting: small correctness cases weight 1; the large performance case weight
  ~6 (bigger input ⇒ more points). Threshold default 0.9 (90%).
- Time limit is per-language scaled (see `languages.py` multipliers) — size the
  performance input, not the limit, to reject the sub-optimal complexity.

## Steps
1. **Gather** (ask if missing): problem statement, constraints (max N + value
   ranges), one example input→output, and the pass threshold (default 90%).
2. **Reference (oracle) solution** — you MUST have a correct solution to compute
   `expected` for every case, especially to label the generated large input.
   Write it as a small Python function.
3. **Correctness cases** — small, hand-written `TestCase`s (weight 1) covering the
   edges the statement implies (empty/degenerate, single element, all-negative,
   ties, max/min). Compute each `expected` with the oracle.
4. **Performance case** — deterministically generate a large input (fixed seed)
   sized to the constraint so a sub-optimal solution TLEs (e.g. N=10^5 rejects
   O(n²)); label `expected` with the oracle; `category="performance"`, weight ~6.
   Mirror the `_perf_case` helper. **If the oracle differs from max-subarray,
   write a question-specific generator/oracle — or generalize `_perf_case` to
   take an `oracle` callable + an input generator** (preferred if more than one
   question will exist).
5. **Set** `constraints` (numeric limits + required-complexity hint; shown to the
   candidate and the judge), `time_limit_s`, and `pass_threshold` on the `Question`.
6. **If replacing `HARDCODED_QUESTION`**, also update:
   - `submissions/` — a correct+fast sample, a wrong sample, and a
     correct-but-slow (O(n²)) sample;
   - [eval_cases.py](../../../assessment_agent/eval_cases.py) — the sources and
     each `expected_verdict` (compute it: passed-weight / total vs threshold);
   - any tests that reference the old question;
   - the example question in `README.md` if it changed.
7. **Verify** — `uv run pytest`; then `uv run assess submissions/<good>` (expect
   PASS / 100%) and the wrong/slow samples (expect FAIL — wrong-answer and TLE
   respectively). Confirm the eval anchors still match (`uv run assess-eval`).

## Files
Always: `assessment_agent/questions.py`. When replacing the hard-coded question:
`submissions/*`, `assessment_agent/eval_cases.py`, `tests/*`, `README.md`.
