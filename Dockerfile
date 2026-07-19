# Production image for the assessment agent's HTTP intake worker.
#
# Bundles nsjail plus every supported language toolchain and turns the OS sandbox
# ON by default (ASSESS_SANDBOX=nsjail), so untrusted candidate code runs with no
# network, dropped capabilities, and cgroup-v2 memory + pids ceilings — the
# production gap that the per-child rlimits alone cannot close (see
# assessment_agent/runner.py and assessment_agent/sandbox.py).
#
# RUN (nsjail needs namespace + cgroup privileges — validated with these flags):
#
#   docker build -t assessment-agent .
#   docker run --rm --privileged --cgroupns=host -p 8000:8000 assessment-agent
#
#   - --cgroupns=host lets nsjail create its cgroup-v2 subtree under the host's
#     unified hierarchy at /sys/fs/cgroup (nsjail runs with --use_cgroupv2).
#   - --privileged grants the namespace + cgroup capabilities; CAP_SYS_ADMIN alone
#     also suffices for a more locked-down deploy. Without them nsjail cannot build
#     the jail and — with ASSESS_SANDBOX=nsjail — the run fails loudly rather than
#     executing unsandboxed, which is the intended safety posture.
#
# Verified end-to-end on 2026-07-19 (correct run, egress blocked, C compile+run,
# cgroup OOM-kill); test_sandbox_nsjail.py is the check and SKIPs where nsjail is
# absent (macOS dev, CI without nsjail) — same pattern as the eval harnesses.

# ---- Stage 1: build nsjail from source (not in Debian stable apt) ----
FROM debian:bookworm-slim AS nsjail-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git build-essential pkg-config \
        libprotobuf-dev protobuf-compiler \
        libnl-route-3-dev libtool bison flex \
    && rm -rf /var/lib/apt/lists/*
# Pin a released tag rather than tracking master.
RUN git clone --depth 1 --branch 3.4 https://github.com/google/nsjail.git /nsjail \
    && make -C /nsjail \
    && strip /nsjail/nsjail

# ---- Stage 2: runtime ----
FROM debian:bookworm-slim

# Language toolchains for every entry in assessment_agent/languages.py:
# python, javascript(node), ruby, go, java, c(gcc), cpp(g++), rust(rustc).
# Plus nsjail's shared-library deps (libprotobuf, libnl-route).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        nodejs \
        ruby \
        golang-go \
        default-jdk-headless \
        gcc g++ \
        rustc \
        libprotobuf32 libnl-route-3-200 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=nsjail-build /nsjail/nsjail /usr/local/bin/nsjail

# uv for reproducible, frozen installs.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app
# Resolve deps first (cache-friendly), then copy the source.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY assessment_agent ./assessment_agent
COPY README.md ./
RUN uv sync --frozen --no-dev

# Turn the sandbox on. With this set, a missing/broken nsjail fails the run rather
# than silently executing untrusted code unsandboxed (see sandbox.py).
ENV ASSESS_SANDBOX=nsjail

EXPOSE 8000
CMD ["uv", "run", "assess-api"]
