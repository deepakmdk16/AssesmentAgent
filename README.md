# Assessment Agent

An agent that grades a candidate's coding-interview submission. It runs the
candidate's code (in whatever language they wrote it), checks the output
against the expected results, judges the code quality, and issues a
**PASS / FAIL** verdict.

## How it works

```
candidate source ──▶ [1] execute per language ──▶ [2] correctness gate
                                                        │
                          all tests pass? ──────────────┤
                                                        ▼
                                     [3] code-quality judge (Claude)
                                                        │
                                                        ▼
                                     [4] PASS / FAIL verdict + summary
```

1. **Execute** — the submission reads a test case on stdin and writes to
   stdout. A per-language registry ([languages.py](assessment_agent/languages.py))
   knows how to compile/run Python, JavaScript, Ruby, Go, Java, C, C++, Rust.
2. **Correctness gate** — every test case must match expected output, or the
   verdict is FAIL regardless of quality.
3. **Quality judge** ([judge.py](assessment_agent/judge.py)) — Claude Opus 4.8
   scores robustness, readability, efficiency, and design against a rubric and
   returns a structured result. With **no API key**, a deterministic offline
   heuristic runs instead so you can build and test the pipeline now.
4. **Verdict** ([agent.py](assessment_agent/agent.py)) — PASS requires all
   tests to pass **and** overall quality ≥ 3.0/5.

## Usage

```bash
uv run assess submissions/good_solution.py          # language auto-detected
uv run assess submissions/good_solution.js
uv run assess path/to/file --language cpp           # override detection
```

Exit code is `0` on PASS, `1` on FAIL.

## Going live (real Claude review)

Set the key and re-run — the judge switches from the offline heuristic to
Claude automatically:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run assess submissions/good_solution.py
```

## The rubric ("skills" as repo modules)

The judge's instructions live as editable, version-controlled markdown in
[assessment_agent/prompts/](assessment_agent/prompts/):

- `review_procedure.md` — how to review and what the four criteria mean
- `scoring_scale.md` — what each 1–5 score means, per criterion
- `report_guidance.md` — what to write in each output field
- `examples/*.md` — calibration examples that anchor the scoring scale

[rubric.py](assessment_agent/rubric.py) composes these into one stable system
prompt that is prompt-cached across candidates. This detailed rubric is what
lets a cheaper model match Opus-with-thinking — the criteria and process are
handed to the model instead of being derived at runtime.

## Configuring the judge (model / cost)

The judge is configured via environment variables:

| Variable | Default | Notes |
|---|---|---|
| `ASSESSMENT_MODEL` | `claude-sonnet-4-6` | Any Claude model id |
| `ASSESSMENT_THINKING` | `off` | `off` or `adaptive` (the rubric usually replaces thinking) |
| `ASSESSMENT_EFFORT` | unset | `low`/`medium`/`high`/`max`; omit for cheapest |

Defaults target the cost/quality sweet spot: Sonnet 4.6, thinking off, letting
the rubric do the work.

## Eval harness (A/B models)

Run the judge over a fixed sample and check agreement with known-good verdicts:

```bash
ASSESSMENT_MODEL=claude-sonnet-4-6 uv run assess-eval
ASSESSMENT_MODEL=claude-opus-4-8 ASSESSMENT_THINKING=adaptive uv run assess-eval
```

Deterministic anchors (a strong solution → PASS, a buggy one → FAIL) are
asserted; borderline cases are report-only so you can eyeball scores and tune
`PASS_QUALITY_THRESHOLD`. Cases live in
[eval_cases.py](assessment_agent/eval_cases.py). (Without an API key the
offline heuristic runs, which is not reliable for calibration — use a real key.)

## Phase 1 vs Phase 2

- **Phase 1 (now):** one hard-coded question with test cases
  ([questions.py](assessment_agent/questions.py)).
- **Phase 2 (planned):** the interviewer supplies the question + expected
  input/output at runtime (same `Question` shape — the pipeline is unchanged),
  and the agent emails the interviewer the result. Not yet built.

## Security

Executing untrusted candidate code is protected here only by a timeout. For
production, run the runner inside a locked-down sandbox (container, no network,
dropped capabilities, resource limits).
