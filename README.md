# Assessment Agent

An agent that grades a candidate's coding-interview submission. It runs the
candidate's code (in whatever language they wrote it), checks the output
against the expected results **and against a time limit sized to the problem's
constraints**, judges the code quality (including time complexity), and issues a
**PASS / FAIL / ERROR** verdict.

## How it works

```
candidate source ──▶ [1] execute per language ──▶ [2] correctness gate
                                                        │
                                                        ▼
                                     [3] performance gate (time limit / TLE)
                                                        │
                                                        ▼
                                     [4] code-quality judge (Claude)
                                                        │
                                                        ▼
                             [5] PASS / FAIL / ERROR verdict + summary
```

1. **Execute** — the submission reads a test case on stdin and writes to
   stdout. A per-language registry ([languages.py](assessment_agent/languages.py))
   knows how to compile/run Python, JavaScript, Ruby, Go, Java, C, C++, Rust,
   each with a time-limit multiplier (interpreted languages get more slack).
2. **Weighted score** — every test case carries points, and **larger inputs are
   worth more**. Correctness cases are small (few points each); the large,
   generated *performance* case is worth the most. The candidate earns
   `passed points / total points` as a percentage.
3. **Performance = points, not a separate gate** — the performance case is sized
   to the constraints so a sub-optimal solution (e.g. O(n²) where O(n) is
   required) **exceeds the time limit (TLE)** and simply forfeits those (large)
   points — just like a CodeChef/Codeforces judge. A wrong answer forfeits its
   case's points too; both are shown distinctly in the report.
4. **Quality report** ([judge.py](assessment_agent/judge.py)) — Claude scores
   robustness, readability, efficiency, and design against a rubric, **states the
   Big-O time complexity**, and says whether it meets the constraints. Quality is
   always **reported but does not gate the verdict**. With **no API key**, a
   deterministic offline heuristic runs (it can't analyse Big-O, so it reports
   complexity as unknown — but the empirical TLE still costs points).
5. **Verdict** ([agent.py](assessment_agent/agent.py)) — **PASS if the score
   meets the question's pass threshold (default 90%), else FAIL.** ERROR means the
   submission couldn't be run (e.g. toolchain missing). The full report — score,
   every test case (input, expected, actual, duration, TLE), and the quality
   assessment — is produced regardless of verdict, and can be emitted as JSON.

## Usage

```bash
uv run assess submissions/good_solution.py          # language auto-detected
uv run assess submissions/good_solution.js
uv run assess path/to/file --language cpp           # override detection
uv run assess path/to/file --json                   # full report as JSON (store/email)
uv run assess submissions/knapsack_good.py --question knapsack_01   # pick the question
```

Two built-in questions ship today: `max_subarray_sum` (the default) and
`knapsack_01` (0/1 knapsack, where an O(2^N) brute force TLEs the performance
case and an O(N*W) DP is required). Select with `--question <id>`.

Exit code is `0` on PASS, `1` on FAIL, `2` on ERROR (submission couldn't be run).

## Constraints drive the performance gate

A TLE is **not** a judgement about the algorithm in the abstract — it is decided
empirically by running the submission on an input sized to the problem's
constraints, under a per-language time limit. So whether an O(n²) solution is
acceptable depends entirely on the constraints, exactly like CodeChef /
Codeforces / LeetCode / HackerRank:

- Small `N` (e.g. `N ≤ 10^4`) → an O(n²) solution finishes in time → **accepted**.
- Large `N` (e.g. `N ≥ 10^5`) → the same O(n²) solution TLEs → **rejected**;
  an O(n log n) / O(n) solution is required.

The interviewer expresses the *required* complexity implicitly, by choosing the
constraint size (`Question.constraints`), the time limit (`Question.time_limit_s`,
scaled per language), and how large the generated performance case is — not by
declaring "must be O(n)". To accept slower solutions, set a smaller `N` and/or a
more generous limit; nothing else changes. The quality judge additionally
*reports* the Big-O and whether it meets the constraints, so the interviewer sees
"O(n²), acceptable for N ≤ 10^4" and keeps the final policy call.

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

Verdicts are score-based, so every anchor is deterministic and holds regardless
of the quality model. To actually measure the **judge**, each case also carries
**labeled quality expectations** — the expected time complexity (e.g. `O(n)`,
`O(n^2)`, `O(2^n)`) and whether the solution meets the constraints — and the
harness reports agreement (`cx` / `meets` columns). These are reported, never
gated, mirroring how quality never gates a verdict. Complexity is scored only
when a real model ran (the offline heuristic reports it as unknown);
meets-constraints is empirically grounded and scored in both modes. Cases span
both questions (`max_subarray_sum`, `knapsack_01`) via each case's
`question_id`, and live in [eval_cases.py](assessment_agent/eval_cases.py).

## Phase 1 vs Phase 2

- **Phase 1 (now):** a small registry of built-in questions with test cases
  ([questions.py](assessment_agent/questions.py)), selected with `--question`.
- **Phase 2 (in progress):** the interviewer supplies the question + expected
  input/output at runtime as a JSON file (same `Question` shape — the pipeline
  is unchanged), loaded with `--question-file`
  ([loader.py](assessment_agent/loader.py), example
  [examples/sum_of_n.json](examples/sum_of_n.json)):

  ```bash
  uv run assess path/to/submission.py --question-file examples/sum_of_n.json
  ```

  The interviewer is the oracle — the file carries the `expected` output for
  every case, including the performance one.

  The result can be rendered as a **single PDF report** and **emailed**
  ([report.py](assessment_agent/report.py), [mailer.py](assessment_agent/mailer.py)) —
  the report bundles the question (prompt, constraints, example), the
  candidate's code, every test case (input/expected/actual), the coverage, and
  the code-quality strengths/weaknesses:

  ```bash
  uv run assess submission.py --question-file q.json --report out.pdf   # write the PDF
  uv run assess submission.py --question-file q.json --email-dry-run     # build the email, don't send
  uv run assess submission.py --question-file q.json --email             # send it (needs SMTP creds)
  ```

  Email goes over Gmail SMTP; credentials come from the environment
  (`SMTP_USERNAME` + `SMTP_PASSWORD`, a Gmail **app password**), and the
  recipient is currently hard-coded ([mailer.py](assessment_agent/mailer.py)).
  **Still to build:** the interviewer *intake* — how the question and submission
  arrive, and an interviewer-supplied recipient (vs. the hard-coded one).

### Future cost optimizations (parked)

Deferred until after Phase 2, in rough priority order:

- **Enum/coded judge output** — have the judge emit levels/enums per criterion
  and render the prose from a repo-side catalog, moving verbosity from expensive
  per-call *output* to cheap cached *input*. ~70–80% fewer output tokens (output
  is ~73% of the per-candidate cost), at the cost of *generic* (catalog-bucketed)
  report language.
- **Batch API** for the email path — grading is asynchronous (results are
  emailed), so the Message Batches API fits and gives a flat 50% discount.
- **Warm-cache cadence / 1-hour TTL** — process candidates in bursts so the
  cached rubric stays warm; only relevant for a trickle/interactive pattern.
- Already shipped: **skipping the LLM judge when the submission fails to execute**
  (a decided FAIL — no call made).

## Security

Executing untrusted candidate code is protected here only by a timeout. For
production, run the runner inside a locked-down sandbox (container, no network,
dropped capabilities, resource limits).
