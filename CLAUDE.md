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
- Run the intake API: `uv run assess-api` (FastAPI). It's a **stateless async
  worker**: `POST /assessments` with the full question **inline** plus the code —
  `{question:{...}, code, language, candidate?, callback_url?, email_to?}` — gets
  a `202 {job_id}`; the work runs in the background and the full result is POSTed
  to `callback_url` and/or emailed. Poll `GET /assessments/{job_id}` as a
  fallback; `GET /health`. No question storage lives here — the platform owns it
  and sends it inline.
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
5. **Open-items checkpoint** — before committing, confirm the "Status & next
   steps" block below still reflects reality: any item this change completes is
   moved out of **Open items**, and any new follow-up it creates is added there.
   This repo tracks status in CLAUDE.md, so a commit that shifts the roadmap must
   update it in the same commit — treat a stale open-items list as a failed gate.

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

**Phase 2 — stateless async intake worker (2026-07-14).** The agent is a
**stateless worker**: it owns no question storage and no database. A platform
posts a job to `POST /assessments` ([api.py](assessment_agent/api.py), FastAPI,
`uv run assess-api`) with the **full question inline** + the candidate's code;
the agent returns `202 {job_id}`, runs the existing `assess` pipeline in a
background task, and delivers the result by POSTing it to `callback_url` and/or
emailing the PDF (`email_to`). `GET /assessments/{job_id}` is a polling fallback
backed by an in-memory job map (transient run-state, not a datastore). The
inline question is validated by `loader.question_from_dict`. `--to` on the CLI
supplies the report recipient. **Design decisions (2026-07-14):** question
source = inline push (platform owns storage; no DB here); invocation = async +
callback; verdict stays deterministic (the "master" is scoring code, never an
LLM — Sonnet only writes the non-gating quality summary). Verified: full suite
green (ruff + mypy clean, 60 passed / 2 skipped) and a live end-to-end async
smoke test — POST → 202 → background assess → `GET` done (PASS) → callback
received the full result. The earlier ID-keyed file store (`store.py`,
`questions_store/`) was **removed** — inline push makes it obsolete.

**Parallel execution — done (2026-07-14).** `runner.py` runs correctness cases
concurrently (bounded `ThreadPoolExecutor`) and performance cases isolated in a
second phase, so CPU contention can't inflate the timing that drives the TLE
gate — a fast solution can't be falsely timed out under load. Outcome order is
preserved; the verdict is unaffected by parallelism. Verified: 62 passed, and a
real CLI run (4/4 correctness + 1/1 performance → PASS).

**API auth — done (2026-07-14).** Shared-secret bearer token in the
`X-Assess-Token` header, enforced only when the env var is set (backward-
compatible). `ASSESS_API_TOKEN` guards the agent's inbound `POST /assessments`;
`CALLBACK_TOKEN` is sent on the outbound callback (and required by the platform's
callback receiver — the platform side matches this exact contract). Verified with
a joint two-service smoke test: authenticated trigger→assess→callback→PASS, and
both sides return 401 to unauthenticated calls. HMAC body-signing is the noted
future hardening step.

**Hardening — Batch A done (2026-07-14).** Four production-hardening fixes found
in a codebase gap-scan (not on the old open-items list): (A1) the Phase-2 loader
now rejects unknown question-JSON keys (`extra="forbid"` on the specs) so an
authoring typo is a loud error, not a silently-dropped field; (A2) the inbound
auth token is compared with `secrets.compare_digest` (constant-time); (A3) the
in-memory `_JOBS` polling map is bounded (`OrderedDict`, FIFO eviction, cap
`ASSESS_MAX_JOBS`, default 1000) so a long-lived worker can't leak; (A4) the
email fallback recipient is env-configurable via `ASSESS_DEFAULT_RECIPIENT`
([mailer.py](assessment_agent/mailer.py) `default_recipient()`), the hard-coded
address only the ultimate fallback. Verified: 67 passed / 2 skipped, ruff + mypy
clean (offline path only — judge untouched, no live-key smoke needed).

**Hardening — Batch B done (2026-07-14).** (B1) candidate execution now gets
best-effort memory + output ceilings ([runner.py](assessment_agent/runner.py)
`_apply_limits`: `RLIMIT_AS` / `RLIMIT_FSIZE` via `preexec_fn`, tunable with
`ASSESS_MEM_LIMIT_MB` / `ASSESS_OUTPUT_LIMIT_MB`) so a submission can't OOM the
worker by allocating or printing without bound — stdout/stderr go to temp files
so the cap bites and a runaway is killed (SIGXFSZ) as a failing case, not a
worker crash; `RLIMIT_AS` is best-effort (unenforced on macOS), and the
missing-runtime→ERROR distinction is preserved (direct exec, not a shell). (B2)
`POST /assessments` validates `callback_url` ([api.py](assessment_agent/api.py)
`_validate_callback_url`): http(s) only, and literal loopback/private/link-local/
reserved IPs (incl. the cloud metadata address) and `localhost` are rejected —
no DNS, so it's offline-safe; name-based rebinding still needs egress controls.
Verified: 75 passed / 2 skipped, ruff + mypy clean, and a real CLI run (PASS,
100%, timing intact). Residual sandboxing gap unchanged — still run under a real
container sandbox in production.

**required_complexity in the report — done (2026-07-14).** The question's
advisory `required_complexity` is now surfaced next to the judge's measured
Big-O in both the CLI text report ([cli.py](assessment_agent/cli.py)) and the
PDF ([report.py](assessment_agent/report.py) §5), shown only when the question
sets it and clearly labelled "advisory — does not gate the verdict" (the verdict
stays score-based; complexity is reported, never gating). Verified: 75 passed / 2
skipped, ruff + mypy clean, and a real CLI + PDF run on `examples/sum_of_n.json`
(required `O(N)` rendered alongside the measured complexity). Offline-only change
(judge untouched) — no live-key re-smoke needed. Composite-score idea from the
same open item is **not** done (see #3 below).

**Open items (pick up here):**
1. **Multiple examples** (deferred) — `Question`/loader/report still hold a
   single example; the authoring vision wants a list. Extend when the authoring
   UI (a separate concern, not in this repo) needs it.
2. **Parked cost optimizations** (see README → Future cost optimizations):
   enum/coded judge output + repo-side prose catalog; Batch API on the email
   path (50% off, fits async email delivery); warm-cache cadence / 1-hour TTL.
   Revisit together, after intake.
3. Optional: composite score (weighted verdict-score + quality). The
   `required_complexity`-in-report half of this item is **done** (see above).
4. **Agentic direction (open discussion)** — adversarial test-gen (advisory) is
   the recommended place to add genuine agentic AI without touching the
   deterministic verdict; candidate-feedback agent once the platform can surface
   it. Not yet chosen.

**Good next tasks:** **#4a adversarial test-gen (advisory) is the chosen next feature** (decision 2026-07-14) — build it in a fresh session; #1/#2/#3 are deferred.

**Companion repo:** the stateful **Assessment Platform** (question/answer/result
storage + trigger + callback receiver) lives as a **separate repo** at
`../assessment-platform` (decision 2026-07-14). The agent stays a stateless
worker and must not absorb question storage. The platform triggers the agent and
persists what the callback returns; it never computes/overrides the grade.

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

## Phase 2 (delivery paths)

Two ways a question + submission reach the agent, both using the same `Question`
shape (the interviewer is the oracle — the question carries every `expected`;
use the `/add-question` skill's recipe to author one):

- **CLI**: `--question-file <json>` + a submission path (local/dev, and the eval
  harness). `--to` sets the report recipient, `--report`/`--email` the outputs.
- **API** (`assess-api`): a platform posts the question **inline** + the code to
  `POST /assessments` and gets the result via `callback_url` / `email_to`. The
  agent is stateless — the platform owns question storage.
