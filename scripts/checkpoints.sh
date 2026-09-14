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

# Where the companion repo is: beside this one (local dev) or inside the
# workspace (CI checks it out there — Actions cannot check out above it). Empty
# when it isn't present at all, and every cross-repo check below then skips with
# a notice rather than failing.
_companion=""
for _c in ../assessment-platform ./assessment-platform; do
  if [ -d "$_c/assessment_platform" ]; then _companion="$_c"; break; fi
done

echo "==> signing.py parity (cross-repo)"
# signing.py is mirrored byte-for-byte in the companion repo; if the two diverge,
# every signed request 401s. This is the "keep them identical" comment turned into
# a gate. Only runs when the companion repo is checked out beside this one — the
# local pre-push case, where the edit is actually made — and skips with a notice
# otherwise (e.g. CI checks out a single repo). See CLAUDE.md → signing.py.
_own_signing="assessment_agent/signing.py"
_companion_signing="${_companion:-/nonexistent}/assessment_platform/signing.py"
if [ -f "$_companion_signing" ]; then
  if cmp -s "$_own_signing" "$_companion_signing"; then
    echo "  ✓ identical to companion repo"
  else
    echo "  ❌ $_own_signing differs from the companion repo's signing.py:"
    diff "$_own_signing" "$_companion_signing" | sed 's/^/     /' || true
    echo "  keep them byte-identical or signed requests 401 — update BOTH."; exit 1
  fi
else
  echo "  ℹ️  companion repo not checked out beside this one — parity check skipped"
fi

echo "==> callback contract parity (cross-repo)"
# contract/callback_contract.py is the agent->platform callback envelope, mirrored
# byte-for-byte in the companion repo. If the two diverge, one side can bless a
# payload the other rejects. Same gate as signing.py; skips when the companion
# repo isn't checked out beside this one. See contract/callback_contract.py.
_own_contract="contract/callback_contract.py"
_companion_contract="${_companion:-/nonexistent}/contract/callback_contract.py"
if [ -f "$_companion_contract" ]; then
  if cmp -s "$_own_contract" "$_companion_contract"; then
    echo "  ✓ identical to companion repo"
  else
    echo "  ❌ $_own_contract differs from the companion repo's copy:"
    diff "$_own_contract" "$_companion_contract" | sed 's/^/     /' || true
    echo "  the callback contract must stay byte-identical — update BOTH."; exit 1
  fi
else
  echo "  ℹ️  companion repo not checked out beside this one — contract parity skipped"
fi

echo "==> question validator parity (cross-repo)"
# `validate_question` is this repo's half of the intake contract: the platform
# must never STORE a question this refuses to grade, or the candidate — who
# cannot edit it — eats an "error" with no reason (audit R2-002). The gate is a
# test in the platform's suite (tests/test_agent_contract_parity.py), but the
# edit that breaks it is usually made here, so run it from this side too.
# Needs the companion's own environment; skips with a notice when either the
# repo or its venv is absent.
if [ -n "$_companion" ] && [ -d "$_companion/.venv" ]; then
  if ( cd "$_companion" && uv run --no-sync pytest -q tests/test_agent_contract_parity.py ); then
    echo "  ✓ the platform still refuses everything this validator refuses"
  else
    echo "  ❌ question validator parity failed — a question the platform stores would not grade here."
    echo "     Run it in the platform repo for the detail: uv run pytest tests/test_agent_contract_parity.py"
    exit 1
  fi
else
  echo "  ℹ️  companion repo (or its .venv) not available — validator parity skipped"
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
