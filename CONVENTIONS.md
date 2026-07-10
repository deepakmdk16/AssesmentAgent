# CONVENTIONS.md — Assessment Agent

Concrete, local design rules for this codebase. These are the *specific* checkable
conventions that back the mechanical hooks (ruff/mypy/gitleaks) and the design
review. When a rule here is silent, the global + repo `CLAUDE.md` apply.

Rules are phrased as "do X, not Y" on purpose — specific and local beats abstract
("follow SOLID"), because they can actually be checked in review.

## 1. Determinism boundary (the most important rule)
- Code execution, output comparison, scoring, and the oracles are **deterministic
  Python** — see `runner.py`, `ExecutionReport.score`, `questions._*`. **Never
  hand any of these to the model.** The model (`judge.py`) only ever *reports*
  quality; it must not decide correctness, timing, or the verdict.
- The verdict is **score-based**: `PASS` iff weighted score ≥ `pass_threshold`,
  else `FAIL`; `ERROR` only when the code could not run. Quality is reported and
  **must never gate** the verdict. A wrong answer or a TLE forfeits that case's
  points — it must never silently earn them.

## 2. Domain vocabulary — use the typed constants
- `Category`, `Verdict`, and `OFFLINE_ENGINE` live in `constants.py` as `Literal`s
  / named constants. **Use them; do not reintroduce raw string literals**
  (`"PASS"`, `"performance"`, `"offline-heuristic"`). This is what lets mypy catch
  a typo or a stale copy instead of it becoming a silent runtime mismatch.
- Per-case *display* status (`"PASS"/"TLE"/"FAIL"` in reports) is a separate axis
  from `Verdict` — keep them distinct; don't conflate.

## 3. Extend via registries, don't branch
- New languages, questions, and model prices are **data**, added to `LANGUAGES`,
  `QUESTIONS`, and `PRICING` — not new `if language == ...` branches. Adding a
  question goes through the `/add-question` skill; every question needs an
  independent oracle cross-check and a reference good sample (the coverage test
  enforces this).
- Source-derived per-language behavior belongs in `Language.resolve` (a callable
  that returns `(source_filename, compile, run)` from the submission), not in a
  branch in `run_submission`. Java uses it (the file must match the public
  class); the runner stays language-agnostic. Follow that pattern for any future
  language that needs source-derived naming — don't add an `if language == ...`.

## 4. Data modeling
- Model state with **frozen dataclasses** (`Question`, `TestCase`, `Language`,
  `Usage`); immutability is the default. Pydantic models are for **external input
  validation** only (the judge's JSON output, the Phase 2 question file) — keep
  that boundary.
- Distinguish failure kinds explicitly: `infra_error` (toolchain missing →
  inconclusive) vs. compile error vs. TLE vs. wrong answer. Never collapse them.

## 5. Secrets & untrusted input
- `ANTHROPIC_API_KEY` comes from the **environment only** — never committed,
  logged, or written to a report/JSON. Absent key → offline heuristic, not an
  error. gitleaks runs in pre-commit as a backstop.
- Candidate code is untrusted; the runner protects only with a timeout. Do not
  weaken that, and note the sandboxing gap in any production-facing work.

## 6. Style & enforcement
- `uv run ruff check .` and `uv run mypy` must be clean before commit (or
  `pre-commit run --all-files`). Naming is `snake_case`, `_private` for helpers.
- Docstrings explain **why**, not what. Match the density and idiom of the file
  you're editing.
- Simplicity first (global `CLAUDE.md` §2): no speculative abstraction, no
  extension point until a second caller exists. "Open for extension" is earned by
  real change pressure, not applied preemptively.
