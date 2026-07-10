---
name: add-question
description: Add or change a coding question in the Assessment Agent — defines the problem, constraints, weighted correctness + performance test cases (with an oracle), registers it so the generic test suite covers it automatically, and verifies. Use when adding a new question, changing an existing one, or authoring an interviewer-supplied (Phase 2) question file.
---

# Add a coding question

Adds a `Question` so a new problem plugs into the existing scoring/verdict
pipeline unchanged. The test suite is **generic and registry-driven**: you
register the question (plus an independent oracle cross-check and a reference
good sample) and the parameterized tests in
[tests/test_questions.py](../../../tests/test_questions.py) exercise it — you do
**not** write per-question test code. A coverage test fails if you forget a
registration, so a question cannot slip in unvalidated.

## When to use
- "add a question", "new coding problem", "change the question", or intake of an
  interviewer-supplied problem (Phase 2).

## Two ways to add a question
- **Built-in** (ships in the repo) — a `Question` literal in
  [questions.py](../../../assessment_agent/questions.py), added to the
  `QUESTIONS` registry. Selected with `assess --question <id>`. Use this path
  for questions we own; you provide the oracle.
- **Interviewer-supplied (Phase 2)** — a JSON file loaded at runtime with
  `assess --question-file <path>` via [loader.py](../../../assessment_agent/loader.py)
  (schema + `validate_question`). The interviewer is the oracle: the file must
  carry the `expected` output for **every** case, including the performance one.
  See [examples/sum_of_n.json](../../../examples/sum_of_n.json) for the format.

## Contract & conventions (do not deviate)
- Candidate programs read the test input from **stdin** and write to **stdout**.
- A `Question` is `id, title, prompt, constraints, test_cases,
  time_limit_s=2.0, pass_threshold=0.9`, plus optional args-based extras
  `example_input, example_output, required_complexity`. `required_complexity`
  is **advisory only** — it labels intent for the judge report / evals and
  **never gates**; the performance gate stays empirical (the TLE).
- `TestCase(name, stdin, expected, category="correctness"|"performance", weight=1.0)`.
- The verdict is **score-based**: PASS iff weighted test score ≥ `pass_threshold`.
  Quality is reported, never gates. So the **test cases, weights, and threshold
  ARE the spec** — get them right.
- Weighting: small correctness cases weight 1; the large performance case weight
  ~6. Threshold default 0.9. `validate_question` requires ≥1 performance case,
  weights > 0, threshold in (0,1], unique case names, non-empty fields.
- Time limit is per-language scaled (`languages.py`) — size the performance
  **input**, not the limit, to reject the sub-optimal complexity.

## Steps (built-in question)
1. **Gather** (ask if missing): problem statement, constraints (max N + value
   ranges), one example input→output, required complexity, pass threshold.
2. **Oracle** — a correct Python function that computes `expected`. Use a case
   builder that labels `expected` **via the oracle** (like `_knapsack_case`) so
   stdin and expected can never drift.
3. **Correctness cases** — small `TestCase`s (weight 1) covering the edges the
   statement implies (empty/degenerate, single element, all-negative, ties,
   min/max, zero-capacity, none-fit). Edges are problem-specific — enumerate
   them deliberately; the harness will not invent them for you.
4. **Performance case** — deterministically generate a large input (fixed seed)
   sized to the constraint so a sub-optimal solution TLEs; label with the
   oracle; `category="performance"`, weight ~6. Mirror `_perf_case` /
   `_knapsack_perf_case`.
5. **Set** `constraints`, `time_limit_s`, `pass_threshold`, and (optionally)
   `required_complexity` / example fields on the `Question`; add it to
   `QUESTIONS`.
6. **Register for the generic suite** (both required — the coverage test enforces
   it) in [tests/test_questions.py](../../../tests/test_questions.py):
   - `ORACLE_CHECKS[id]` — an **independent** naive reference (a *different*
     implementation) + a small-input generator. This differentially validates
     your oracle; without it "expected matches oracle" is tautological.
   - `GOOD_SAMPLES[id]` — path to a correct+fast reference submission that must
     score 100%. Add that submission under `submissions/`.
7. **Optional but recommended** — add a correct-but-slow sample under
   `submissions/` (proves the perf case discriminates), and eval anchors in
   [eval_cases.py](../../../assessment_agent/eval_cases.py) with
   `expected_verdict` (and, when adding labeled quality expectations, the
   expected complexity / meets-constraints).
8. **If replacing an existing question**, also update its samples, eval anchors,
   any tests that reference it, and the example in `README.md`.

## Verify
- `uv run pytest` — the generic suite now covers the new question: structural
  validity, oracle-vs-naive agreement, good-sample 100%, and registration
  coverage.
- `uv run assess submissions/<good> --question <id>` → PASS/100%; the slow
  sample → FAIL (TLE). For Phase 2: `uv run assess <good> --question-file <path>`.
- `uv run assess-eval` — anchors still match.

## Files
Always: `assessment_agent/questions.py`, `tests/test_questions.py`
(`ORACLE_CHECKS` + `GOOD_SAMPLES`), a `submissions/<good>` sample. Phase 2
instead: a question JSON file (+ `examples/` if it should ship). When replacing
a question: also `submissions/*`, `eval_cases.py`, other `tests/*`, `README.md`.
