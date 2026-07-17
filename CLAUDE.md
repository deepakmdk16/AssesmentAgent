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
4. `uv run assess-eval` with a real key — the deterministic anchors
   (strong→PASS, buggy→FAIL) must hold before trusting a model/config.
5. **Open-items checkpoint** — before committing, confirm [ROADMAP.md](ROADMAP.md)
   still reflects reality: any item this change completes is moved out of **Open
   items**, and any new follow-up it creates is added there. This repo tracks
   status in ROADMAP.md, so a commit that shifts the roadmap must update it in the
   same commit — treat a stale open-items list as a failed gate.

## Guardrails specific to this repo

- **Never commit an `ANTHROPIC_API_KEY`** or any secret (incl. SMTP creds — the
  Gmail app password in `SMTP_USERNAME`/`SMTP_PASSWORD`). Keys come from the
  environment only.
- Executing candidate code is untrusted input — the runner protects only with a
  timeout. Do not weaken that, and note the sandboxing gap in any production work.
- The verdict is score-based: `PASS` iff the weighted test score meets the
  question's `pass_threshold`, else `FAIL` (`ERROR` only when the code couldn't
  be run). Code quality is reported but must **not** gate the verdict. A wrong
  answer or a TLE forfeits that case's points — it must never silently earn them.

## Status & roadmap

Current status, the slice-by-slice changelog, and the open-items backlog live in
[ROADMAP.md](ROADMAP.md) — moved out of this file so CLAUDE.md stays lean and
loads cheaply every session. **Pre-push checkpoint #5 applies to ROADMAP.md:**
update it in the same commit that shifts the work; trim merged slices to one line
(git history holds the detail).

## Phase 2 (delivery paths)

Two ways a question + submission reach the agent, both using the same `Question`
shape (the interviewer is the oracle — the question carries every `expected`;
use the `/add-question` skill's recipe to author one):

- **CLI**: `--question-file <json>` + a submission path (local/dev, and the eval
  harness). `--to` sets the report recipient, `--report`/`--email` the outputs.
- **API** (`assess-api`): a platform posts the question **inline** + the code to
  `POST /assessments` and gets the result via `callback_url` / `email_to`. The
  agent is stateless — the platform owns question storage.
