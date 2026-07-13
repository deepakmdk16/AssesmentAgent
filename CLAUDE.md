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
- Phase 2 report/email: add `--report out.pdf`, `--email-dry-run`, or `--email`
  (email needs `SMTP_USERNAME`/`SMTP_PASSWORD` — a Gmail app password). Use
  `--candidate NAME` to set the report title / email subject (defaults to the
  file stem).
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

**Phase 2 — loader + PDF report + email built and VERIFIED (2026-07-11).**
`--question-file` loads an interviewer-supplied question; `--report <path>`
renders a single PDF ([report.py](assessment_agent/report.py): question, code,
test cases, coverage, strengths/weaknesses); `--email` / `--email-dry-run` send
it ([mailer.py](assessment_agent/mailer.py), Gmail SMTP, creds from
`SMTP_USERNAME`/`SMTP_PASSWORD` — a Gmail **app password**, recipient
**hard-coded** to `mailer.RECIPIENT`). The full `--email` path was run live and
the send completed (appears in the sender's Sent folder), but a later check
found it did **not** arrive at the recipient — see the "Email delivery (open)"
note below. The LLM judge
is **skipped when the submission fails to execute** (compile/runtime failure) —
`quality_engine == "skipped"`, no API call.

**Open items (pick up here):**
1. **Phase 2 intake + real recipient** — the biggest remaining feature. Right
   now the interviewer supplies the question via `--question-file` and the
   submission via a CLI path, and the recipient is hard-coded. Still to design:
   how question + submission actually arrive (queue? upload? git webhook?), and
   an interviewer-supplied recipient (a `--to` flag and/or per-question field)
   instead of the hard-coded `mailer.RECIPIENT`.
2. **Parked cost optimizations** (see README → Future cost optimizations):
   enum/coded judge output + repo-side prose catalog; Batch API on the email
   path (50% off, fits async email delivery); warm-cache cadence / 1-hour TTL.
   Revisit together, after intake.
3. Optional: surface `required_complexity` in the judge report; composite score.

**Good next tasks:** the Phase 2 intake + real recipient (#1); then the parked
cost optimizations (#2).

**Secrets note:** email uses a Gmail app password from `SMTP_USERNAME` /
`SMTP_PASSWORD` — env only, never committed. The previously-exposed app password
was rotated (2026-07-11); use the fresh one, set via `export`.

**Email delivery — VERIFIED (2026-07-12):** after rotating the app password, a
live `--email` send from `deepakmadire@gmail.com` was received at
`deepakmdk16@gmail.com`. The earlier non-delivery was tied to the pre-rotation
app password.

**Report styling (done, 2026-07-11):** `report.py` got a typographic pass —
title/subtitle hierarchy, a tinted verdict banner, section rules, boxed
monospace code, and a styled test-case table (header fill, coloured status,
zebra striping). The candidate name is now interviewer-supplied via
`--candidate NAME` (falls back to the file stem), so the report title and email
subject no longer show the raw filename.

## Phase 2 (loader + report + email built; intake pending)

The interviewer supplies the question + expected I/O at runtime as a JSON file
(`--question-file`, same `Question` shape — use the `/add-question` skill's
recipe; the interviewer is the oracle, the file carries every `expected`). The
result renders to a PDF and emails to a hard-coded recipient. **Still to build:**
interviewer *intake* (how the question/submission arrive) and a non-hard-coded
recipient.
