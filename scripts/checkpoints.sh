#!/usr/bin/env bash
# Deterministic pre-push gate for the Assessment Agent.
# A non-zero exit ABORTS the push (wired as the git pre-push hook).
# Bypass is deliberate and discouraged: `git push --no-verify`.
#
# This is the scriptable half of the old "ship" routine — the objective gate.
# The judgment half (/code-review, live-Claude smoke, ROADMAP update) stays in
# the `ship` skill; it can't be scripted.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "==> pytest";           uv run pytest -q
echo "==> ruff check";       uv run ruff check .
echo "==> mypy";             uv run mypy

echo "==> secret scan"
# 1) sensitive files must never be tracked
if git ls-files | grep -Ei '(^|/)\.env$|\.pem$|(^|/)id_rsa$|\.p12$|\.keystore$|(^|/)\.aws/credentials$'; then
  echo "❌ a sensitive file is tracked (above) — remove it and add to .gitignore"; exit 1
fi
# 2) high-signal hard-coded secrets in tracked text (this script is excluded so its
#    own pattern literals don't self-match)
_pat='sk-'; _pat="${_pat}ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,}"
_hits="$(git ls-files -z -- . ':(exclude)scripts/checkpoints.sh' | xargs -0 grep -InE "$_pat" 2>/dev/null || true)"
if [ -n "$_hits" ]; then
  echo "$_hits"; echo "❌ possible hard-coded secret in tracked files (above)"; exit 1
fi

echo "==> docs drift"
# CLAUDE.md loads every session and README.md is the front door, so a stale one
# misleads every future reader — human or agent. Checkpoint #5 already made
# STATUS.md a gate by naming it; the docs that drifted were the ones no gate
# named. These two checks are the mechanical half of that rule: they can't judge
# whether the prose is *good*, only that it hasn't silently fallen behind the
# code. That's exactly the drift that actually happened.
_drift=0

# 1. Every module must be mentioned in CLAUDE.md's architecture section.
for _f in assessment_agent/*.py; do
  _mod="$(basename "$_f")"
  case "$_mod" in __init__.py) continue ;; esac
  if ! grep -q "$_mod" CLAUDE.md; then
    echo "  ❌ $_mod is not mentioned in CLAUDE.md (architecture section is stale)"
    _drift=1
  fi
done

# 2. Every console script must be documented in README.md.
for _script in $(grep -oE '^[a-z-]+ = "assessment_agent' pyproject.toml | cut -d' ' -f1); do
  if ! grep -q "$_script" README.md; then
    echo "  ❌ '$_script' is a [project.scripts] entry but appears nowhere in README.md"
    _drift=1
  fi
done

if [ "$_drift" -ne 0 ]; then
  echo "❌ docs drift (above) — update the doc, or the next reader inherits a lie"; exit 1
fi

# Advisory only: claims a script can't verify, surfaced for a human re-read.
# These strings went stale before (README described shipped features as "still to
# build"), so print them rather than trusting memory. Never fails the gate.
_claims="$(grep -InE 'Still to build|hard-coded|in progress|not yet' README.md CLAUDE.md || true)"
if [ -n "$_claims" ]; then
  echo "  ℹ️  unverifiable claims — confirm these are still true:"
  echo "$_claims" | sed 's/^/     /'
fi

echo "✅ checkpoints passed"
