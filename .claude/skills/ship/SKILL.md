---
name: ship
description: Pre-push check for the Assessment Agent — runs tests, reviews the diff, checks for secrets, and reports status against the repo checkpoints before offering to commit/push. Use when getting ready to commit or push.
---

# Ship check (pre-commit / pre-push)

Runs the verify-before-commit routine and reports status against the global +
repo `CLAUDE.md` checkpoints, then offers to commit/push. **Does not commit or
push on its own.**

## When to use
- "ship", "ready to push", "pre-push check", or before any commit/push.

## Steps
1. **Tests** — `uv run pytest`; report the ACTUAL result (pass/skip/fail counts).
   Never claim done on unverified code.
2. **Review the diff** — run `/code-review` if there's a meaningful diff,
   otherwise self-review `git diff` for correctness bugs and
   reuse/simplification. Fix clear issues before proceeding.
3. **Secrets** — confirm nothing secret is staged (no `ANTHROPIC_API_KEY`, no
   keys/tokens); `.venv` and caches must be gitignored. Inspect with
   `git diff --cached --name-only` and `git status`.
4. **Live-Claude status** — if the change touches the judge / LLM path, note
   whether it was smoke-tested with a real `ANTHROPIC_API_KEY` (the offline
   heuristic does not exercise the real call). Flag it if still unverified.
5. **Summarize** against the checkpoints — a short table of
   tests / review / secrets / live-smoke.
6. **Offer** to commit and push — do NOT do it unprompted. When the user says yes:
   - if on the default branch, ask whether to branch first;
   - end the commit message with
     `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`;
   - push over SSH (`git@github.com:...`); create a remote only with explicit OK.

## Notes
- Commit/push happens only on an explicit "yes" — this skill prepares and
  reports; it does not auto-ship.
