# CLAUDE.md — Assessment Agent

Project-specific guidance. Merge with the global `~/.claude/CLAUDE.md`; where
this file is silent, the global rules apply.

## What this is

An agent that grades a candidate's coding-interview submission: runs the code
(in whatever language it's written), checks output against expected results,
judges code quality with an LLM, and issues a PASS / FAIL / ERROR verdict.
See [README.md](README.md) for the full flow, and
[CONVENTIONS.md](CONVENTIONS.md) for the concrete, checkable design rules.

## Stack & how to run

- Python ≥ 3.10, managed with `uv`.
- Run an assessment: `uv run assess <file> [--language X | --question-file <json>]`
- Run the eval harness: `uv run assess-eval`
- Run tests: `uv run pytest`
- Lint / format / types: `uv run ruff check .`, `uv run ruff format .`, `uv run mypy`.
- Enable the pre-commit hooks once: `uv run pre-commit install` (the first run
  fetches the hook repos, so it needs network). `.pre-commit-config.yaml` is
  two-tier: a language-agnostic base (file hygiene + gitleaks secret scan) plus
  a swappable Python slice (ruff + mypy).

## Architecture (where things live)

- `runner.py` — executes submissions per language (deterministic; keep it that
  way — do **not** hand code execution to the model).
- `judge.py` — LLM quality judge (Claude) with an offline heuristic fallback.
- `rubric.py` + `prompts/` — the judge's instructions as editable markdown
  modules ("skills as repo modules", not the Anthropic Skills feature).
- `agent.py` — orchestration + verdict. `pricing.py` — token/cost estimation.

## Pre-push checkpoints (in addition to the global §6 gates)

Before committing or pushing:

1. `uv run pytest` passes (report the actual result; don't claim done unverified).
   `uv run ruff check .` and `uv run mypy` are clean (or `pre-commit run
   --all-files`). Domain states (`Verdict`, `Category`, `OFFLINE_ENGINE`) live
   in `constants.py` as `Literal`s — use them, don't reintroduce raw string
   literals, so mypy keeps catching drift.
2. `/code-review` (or a self-review of the diff) has been run.
3. **The live Claude judge path has been smoke-tested with a real
   `ANTHROPIC_API_KEY`** before relying on it or moving to Phase 2 — the offline
   heuristic only exercises the pipeline, not the real model call.
4. `uv run assess-eval` with a real key — the deterministic anchors
   (strong→PASS, buggy→FAIL) must hold before trusting a model/config.

## Guardrails specific to this repo

- **Never commit an `ANTHROPIC_API_KEY`** or any secret. Keys come from the
  environment only.
- Executing candidate code is untrusted input — the runner protects only with a
  timeout. Do not weaken that, and note the sandboxing gap in any production work.
- The verdict is score-based: `PASS` iff the weighted test score meets the
  question's `pass_threshold`, else `FAIL` (`ERROR` only when the code couldn't
  be run). Code quality is reported but must **not** gate the verdict. A wrong
  answer or a TLE forfeits that case's points — it must never silently earn them.

## Status & next steps

**Current status:** multi-language runner, weighted scoring with a per-question
pass threshold (default 90%), performance/TLE gate sized to the constraints, LLM
quality judge with offline fallback and Big-O reporting, `--json` report export,
and an eval harness. **Two built-in questions** (`max_subarray_sum`,
`knapsack_01`) selectable with `--question`. A **generic, registry-driven test
suite** ([tests/test_questions.py](tests/test_questions.py)) covers every
question automatically — structural `validate_question` invariants, an
independent oracle-vs-naive differential check (so `expected` is no longer
tautologically defined by the grading oracle), good-sample 100%, and a coverage
test that fails if a new question isn't registered. **Phase 2 loader is built**:
`--question-file <path>` loads an interviewer-supplied question JSON
([loader.py](assessment_agent/loader.py), example `examples/sum_of_n.json`),
with optional args-based `example_*` / advisory `required_complexity` fields.

**Live judge path — VERIFIED (2026-07-10).** The real `client.messages.create`
path was smoke-tested with a live key on `claude-sonnet-4-6`: `assess` and
`assess-eval` both ran clean. Structured output/params work, prompt caching is
active, and the labeled evals matched the model on **all** anchors — verdicts
7/7, complexity 7/7, meets-constraints 7/7. Measured cost ≈ $0.0094/candidate
(~$9.40 per 1,000). The judge/complexity output can now be trusted (re-run the
smoke-test when changing model/config, per the pre-push checkpoints).

**Open items:**
1. **Phase 2 intake + email not built** (below).
2. Optional: surface `required_complexity` in the judge report; add a composite
   score (fold quality into the final % so it can affect PASS/FAIL).

**Good next tasks:** build the Phase 2 interviewer intake + email/notification
side (the biggest remaining feature); the optional scoring/complexity items (#2).

## Phase 2 (loader built; intake + email pending)

The interviewer supplies the question + expected I/O at runtime as a JSON file
(`--question-file`, same `Question` shape — use the `/add-question` skill's
recipe). The interviewer is the oracle (the file carries every `expected`).
**Still to build:** interviewer intake (how the question/submission arrive) and
emailing the interviewer the result.
