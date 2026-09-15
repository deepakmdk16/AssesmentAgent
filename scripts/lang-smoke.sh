#!/usr/bin/env bash
# Gate G3 (audit R2-007, R2-008, R2-092, R2-093, R2-094, R2-105): run the
# per-language smoke suite (tests/test_lang_smoke.py) and the toolchain pin diff
# INSIDE a built production image, through the real jail — the programs run in
# nsjail exactly as a candidate's would.
#
#   bash scripts/lang-smoke.sh [image]        # default: assessment-agent:ci
#
# Needs the same host preparation as tests/test_sandbox_nsjail.py in
# .github/workflows/sandbox.yml: cgroup v2, the AppArmor profile loaded, and the
# run flags in deploy/docker-run.flags. On a dev box: `docker build -t
# assessment-agent .`, then `bash scripts/lang-smoke.sh assessment-agent`. Where the
# host cannot load the profile (Colima without it), LANG_SMOKE_EXTRA_FLAGS lets you
# append e.g. --security-opt=apparmor=unconfined (last wins — see the Dockerfile).
#
# ASSESS_REQUIRE_TOOLCHAINS=1 turns every per-language SKIP into a failure and
# enables the pin diff, so a toolchain missing from the image, older than an idiom
# in use, or different from assessment_agent/toolchains.txt is red, never green.
set -euo pipefail
cd "$(dirname "$0")/.."

image="${1:-assessment-agent:ci}"
# No mapfile: macOS still ships bash 3.2, and this script is meant to be runnable
# on a dev box against a locally built image, not only on the CI runner.
flags=()
while IFS= read -r flag; do
    flags+=("$flag")
done < <(grep -Ev '^[[:space:]]*(#|$)' deploy/docker-run.flags)
extra=()
if [[ -n "${LANG_SMOKE_EXTRA_FLAGS:-}" ]]; then
    read -r -a extra <<<"$LANG_SMOKE_EXTRA_FLAGS"
fi

echo "==> toolchains in $image"
# --entrypoint bypasses deploy/entrypoint.sh: a version probe needs no jail.
docker run --rm --entrypoint sh "$image" -c \
    'uv run --frozen --no-dev python -m assessment_agent.toolchains'

echo "==> language smoke + pin diff inside $image"
# tests/ is excluded by .dockerignore and the image installs --no-dev, so mount the
# suite read-only and layer pytest into an ephemeral uv overlay (sandbox.yml's
# recipe): /app/.venv stays the shipped one, assessment_agent resolves to the
# image's copy, and only the harness comes from the checkout.
docker run --rm "${flags[@]}" ${extra[@]+"${extra[@]}"} \
    -e ASSESS_REQUIRE_TOOLCHAINS=1 \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$PWD/tests:/app/tests:ro" \
    "$image" \
    uv run --frozen --no-dev --with pytest \
    pytest -q -rs -p no:cacheprovider tests/test_lang_smoke.py
