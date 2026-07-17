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

echo "✅ checkpoints passed"
