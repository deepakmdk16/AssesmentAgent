# Production image for the assessment agent's HTTP intake worker.
#
# Bundles nsjail plus every supported language toolchain and turns the OS sandbox
# ON by default (ASSESS_SANDBOX=nsjail), so untrusted candidate code runs with no
# network, dropped capabilities, and cgroup-v2 memory, pids and CPU ceilings — the
# production gap that the per-child rlimits alone cannot close (see
# assessment_agent/runner.py and assessment_agent/sandbox.py).
#
# RUN, from the repo root. The docker run flags live in deploy/docker-run.flags, one
# per line with its reason. That file is the source of truth, so they aren't
# restated here:
#
#   docker build -t assessment-agent .
#   sudo apparmor_parser -r deploy/apparmor/assess-nsjail   # on the host, once per boot
#   docker run --rm $(grep -Ev '^[[:space:]]*(#|$)' deploy/docker-run.flags) \
#       -p 8000:8000 assessment-agent
#
# Where `sysctl -n kernel.apparmor_restrict_unprivileged_userns` is absent or 0, skip
# apparmor_parser and append --security-opt=apparmor=unconfined after the flags (last wins).
# On Docker Desktop or Colima "the host" is their Linux VM, not macOS: run the sysctl
# and apparmor_parser there (e.g. `colima ssh -- sudo apparmor_parser -r <abs path>`).
#
# Posture: the server, nsjail and the jailed code all run as the unprivileged
# `assess` user (uid 10001) with no capabilities, no_new_privs and a seccomp filter.
# nsjail builds each jail from an unprivileged user namespace. Only the entrypoint
# (deploy/entrypoint.sh) runs as root, and it still needs this from the host:
#   - CAP_SYS_ADMIN (plus CHOWN, SETUID, SETGID, SETPCAP) at container START, to
#     delegate the container's private cgroup-v2 namespace to the worker. All of
#     them are dropped before CMD runs.
#   - on Ubuntu >= 23.10, the host AppArmor profile deploy/apparmor/assess-nsjail,
#     because unprivileged user namespaces are AppArmor-restricted there.
#   - the custom seccomp profile deploy/seccomp.json (Docker's default lacks
#     pivot_root, which nsjail needs).
# Without them the entrypoint refuses to start, or nsjail can't build the jail. With
# ASSESS_SANDBOX=nsjail that fails the run loudly rather than executing unsandboxed,
# which is the intended safety posture.
#
# That still rules out managed container platforms (Cloud Run, Fargate, Fly, Railway,
# Render): none grants CAP_SYS_ADMIN, a custom seccomp profile or a host AppArmor
# profile. Run it on a VM you control.
#
# tests/test_sandbox_nsjail.py is the check: .github/workflows/sandbox.yml builds this
# image and runs that suite inside it with the flags from deploy/docker-run.flags,
# where a missing jail is an error rather than a skip (PRs from this repo and push to
# main; fork PRs are skipped). It still SKIPs on a macOS dev box.

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
# Plus nsjail's shared-library deps (libprotobuf, libnl-route), and tini: PID 1 after
# the entrypoint's drop, reaping the jail processes each TLE orphans.
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
        tini \
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

# Bind on all interfaces. The app's default is 127.0.0.1 — right for a bare
# `uv run assess-api` on a dev box, but inside a container it is unreachable
# through `-p 8000:8000` (the documented run command silently failed without
# this). Override ASSESS_API_HOST/ASSESS_API_PORT at `docker run` if needed.
ENV ASSESS_API_HOST=0.0.0.0

# Unprivileged worker (A07). The server and everything it spawns (nsjail and the
# jailed candidate code) run as this uid. A real home gives uv a writable cache. The
# group is made explicitly because --user-group would pick a system gid (999).
RUN groupadd --system --gid 10001 assess \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/assess \
        --shell /usr/sbin/nologin assess
ENV HOME=/home/assess
# The venv synced above stays root-owned. Without this, `uv run --frozen --no-dev`
# re-installs the editable project into it at every start, which as the worker fails
# with EACCES before serving a single request.
ENV UV_NO_SYNC=1
# Runs as root just long enough to delegate the cgroup namespace, then drops to
# `assess` for good and execs CMD. See the script, and deploy/docker-run.flags for
# the flags it needs.
COPY --chmod=0755 deploy/entrypoint.sh /usr/local/bin/assess-entrypoint
ENTRYPOINT ["/usr/local/bin/assess-entrypoint"]

EXPOSE 8000
# --frozen --no-dev: run from the venv this image already built. Plain `uv run`
# re-resolves the lockfile at every container start, which downloads the *dev*
# group (mypy, ruff, pytest) from PyPI before serving a single request — so the
# image could not boot without network access to PyPI, boot time depended on a
# third party (measured: 44s vs 0.85s here, and >180s under bandwidth
# contention), and what ran was not the artifact that was tested.
CMD ["uv", "run", "--frozen", "--no-dev", "assess-api"]
