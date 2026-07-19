# CLAUDE.md — Assessment Agent

Project-specific guidance. Merge with the global `~/.claude/CLAUDE.md`; where
this file is silent, the global rules apply.

## Before you navigate code here

Global `CLAUDE.md` §7 says to reach for serena's `find_symbol` before Read/grep.
In this repo serena needs an explicit `activate_project("AssesmentAgent")` first
— the `ide-assistant` context does not auto-activate, so the *first* serena call
of a session errors and the natural recovery is exactly the whole-file Read that
§7 exists to prevent. Activate once, then navigate.

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

Every module is listed here, and `scripts/checkpoints.sh` enforces that — a new
module that isn't mentioned fails the gate. Keep the one-liners short; depth
belongs in the module docstring.

**Deterministic core** (never hand any of this to the model):
- `runner.py` — executes submissions per language. Cases run serially; per-child
  rlimits go on via `preexec_fn`, so do **not** wrap it in threads (see its
  docstring).
- `questions.py` — built-in questions + `validate_question` invariants.
- `loader.py` — validates an interviewer-supplied question JSON (Phase 2).
- `languages.py` — the per-language compile/run registry.
- `agent.py` — orchestration + the score-based verdict.
- `constants.py` — `Verdict` / `Category` / engine labels as `Literal`s.

**LLM surfaces** (all three report; none may gate a verdict):
- `judge.py` — quality judge, with an offline heuristic fallback.
- `adversarial.py` — advisory edge-case probe (opt-in).
- `authoring.py` — drafts a question from a brief; the oracle is the *executed*
  reference, never the model's arithmetic.
- `rubric.py` + `prompts/` — the judge's instructions as editable markdown
  modules ("skills as repo modules", not the Anthropic Skills feature).
- `llm.py` — shared call-site concerns: timeouts + untrusted-input fencing.

**Delivery & reporting:**
- `cli.py` — the `assess` CLI. `api.py` — the stateless HTTP intake worker.
- `ratelimit.py` — in-process fixed-window rate limiter for the API's
  code-execution / LLM-cost endpoints (`/run`, `/run/tests`, `/assessments`,
  `/questions/draft`).
- `signing.py` — HMAC-SHA256 body signing/verification for the platform↔agent
  link (mirrored verbatim in the platform repo; inbound requests + the outbound
  callback are signed when the signing secrets are configured).
- `report.py` — PDF rendering. `mailer.py` — Gmail SMTP delivery.
- `pricing.py` — token/cost estimation.

**Evals** (each has an anchored harness + a unit-tested logic half):
- `eval.py` / `eval_cases.py` — the judge (`assess-eval`).
- `draft_eval.py` / `draft_eval_cases.py` — authoring (`assess-draft-eval`).
- `adversarial_eval.py` / `adversarial_eval_cases.py` — the probe
  (`assess-adversarial-eval`).

## Pre-push checkpoints (in addition to the global §6 gates)

The deterministic gate (1) is `scripts/checkpoints.sh`, wired as the git
`pre-push` hook (run `bash scripts/install-hooks.sh` once per clone) — a failure
aborts the push. The judgment gates (2–5) are not scriptable; the `ship` skill
walks them. Before committing or pushing:

1. `uv run pytest` passes (report the actual result; don't claim done unverified).
   `uv run ruff check .` and `uv run mypy` are clean (or `pre-commit run
   --all-files`). Domain states (`Verdict`, `Category`, `OFFLINE_ENGINE`) live
   in `constants.py` as `Literal`s — use them, don't reintroduce raw string
   literals, so mypy keeps catching drift.
2. `/code-review` (or a self-review of the diff) has been run.
3. **The live Claude judge path has been smoke-tested with a real
   `ANTHROPIC_API_KEY`** before relying on it or moving to Phase 2 — the offline
   heuristic only exercises the pipeline, not the real model call.
4. The eval harnesses with a real key — re-run after **any** model/prompt change.
   There are three, one per LLM surface: `assess-eval` (judge; the deterministic
   anchors strong→PASS, buggy→FAIL must hold), `assess-draft-eval` (authoring),
   and `assess-adversarial-eval` (probe). Offline they SKIP, so a green `pytest`
   is **not** evidence any of them passed. Baselines live in STATUS.md.
5. **Open-items checkpoint** — before committing, confirm [STATUS.md](STATUS.md)
   still reflects reality: remove any item this change closes and add any new
   follow-up it opens. STATUS.md tracks pending work plus the eval baselines (the
   one reference exception — checkpoint #4 needs them to tell a regression from
   normal variance); history is `git log`, so a detailed commit message is the
   changelog. Treat a stale open-items list as a failed gate.

## Guardrails specific to this repo

- **Never commit an `ANTHROPIC_API_KEY`** or any secret (incl. SMTP creds — the
  Gmail app password in `SMTP_USERNAME`/`SMTP_PASSWORD`). Keys come from the
  environment only.
- Executing candidate code is untrusted input. The exact protections (and the
  gaps they do **not** close) are documented once, in `runner.py`'s module
  docstring — read it there rather than trusting a summary here; this line used
  to restate them and went stale. Do not weaken them, and note the sandboxing gap
  in any production work.
- Report delivery is a privacy surface: a report carries the candidate's code and
  verdict, so the recipient must be explicit. There is deliberately no built-in
  fallback address — see `mailer.py`.
- The verdict is score-based: `PASS` iff the weighted test score meets the
  question's `pass_threshold`, else `FAIL` (`ERROR` only when the code couldn't
  be run). Code quality is reported but must **not** gate the verdict. A wrong
  answer or a TLE forfeits that case's points — it must never silently earn them.

## Status & next

Pending / next work lives in [STATUS.md](STATUS.md) — a short **pending-work**
list, plus the eval baselines as its one reference section (checkpoint #4 reads
them). Feature *history* is `git log` (commits are per-slice and detailed), not a
changelog file. **Pre-push checkpoint #5 applies to STATUS.md:** update it in the
same commit that opens or closes an item.

Starting a fresh session? STATUS.md's first item is the recommended next task.

## Phase 2 (delivery paths)

Two ways a question + submission reach the agent, both using the same `Question`
shape (the interviewer is the oracle — the question carries every `expected`;
use the `/add-question` skill's recipe to author one):

- **CLI**: `--question-file <json>` + a submission path (local/dev, and the eval
  harness). `--to` sets the report recipient, `--report`/`--email` the outputs.
- **API** (`assess-api`): a platform posts the question **inline** + the code to
  `POST /assessments` and gets the result via `callback_url` / `email_to`. The
  agent is stateless — the platform owns question storage.
