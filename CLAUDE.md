# CLAUDE.md — Assessment Agent

Project-specific guidance. Merge with the global `~/.claude/CLAUDE.md`; where
this file is silent, the global rules apply.

## What this is

An agent that grades a candidate's coding-interview submission: runs the code
(in whatever language it's written), checks output against expected results,
judges code quality with an LLM, and issues a PASS / FAIL / ERROR verdict.
See [README.md](README.md) for the full flow.

## Stack & how to run

- Python ≥ 3.10, managed with `uv`.
- Run an assessment: `uv run assess <file> [--language X]`
- Run the eval harness: `uv run assess-eval`
- Run tests: `uv run pytest`

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

**Current status (Phase 1 complete):** multi-language runner, weighted scoring
with a per-question pass threshold (default 90%), performance/TLE gate sized to
the constraints, LLM quality judge with offline fallback and Big-O reporting,
`--json` report export, an eval harness, and a passing test suite. The
`/add-question` and `/ship` skills are in `.claude/skills/`.

**Open items:**
1. **Live Claude judge path is unverified** — everything is tested against the
   *offline heuristic*; the real `client.messages.create` call (schema, effort,
   caching, complexity fields) has never run against the API. Smoke-test it with
   a real `ANTHROPIC_API_KEY` (`uv run assess submissions/good_solution.py` and
   `uv run assess-eval`) before trusting the quality/complexity output.
2. **Phase 2 not built** (below).

**Good next tasks:** run the live smoke-test (#1); build Phase 2; optionally add
a composite score (fold quality into the final % so it can affect PASS/FAIL);
generalize `questions._perf_case` to take an `oracle` callable so new questions
don't hardcode the max-subarray oracle.

## Phase 2 (planned, not built)

Interviewer supplies the question + expected I/O at runtime (same `Question`
shape — use the `/add-question` skill's recipe), and the agent emails the
interviewer the result. Still to design: interviewer intake and the email/
notification side (the question-authoring mechanics are covered by the skill).
