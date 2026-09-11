#!/bin/sh
# Container entrypoint (A07), and the only code in the image that runs as root. It
# turns the container's private cgroup-v2 namespace into a subtree the unprivileged
# worker can hand to nsjail, then drops to that worker for good (no capabilities in
# any set, no_new_privs) and execs CMD. Nothing untrusted runs before the drop.
#
# It must exec, never fork: tini inherits PID 1 through the exec chain, after the drop.
# As PID 1 it reaps the orphaned jail processes every TLE leaves behind (nothing else
# would, and each zombie holds a slot in the container's pids limit), and it forwards
# `docker stop`'s SIGTERM to CMD. Today that is `uv run`, which forwards it to the
# server (api.py's graceful shutdown error-callbacks in-flight jobs).
#
# The docker run flags it needs are in deploy/docker-run.flags. Every refusal below
# but cgroup v1's names the flag that is probably at fault.
set -eu

WORKER=assess
CG=/sys/fs/cgroup

die() {
    echo "assess-entrypoint: $*" >&2
    echo "assess-entrypoint: start the container with the flags in deploy/docker-run.flags" >&2
    exit 1
}

[ "$(id -u)" = 0 ] || die "must start as root (it drops to '$WORKER' itself); don't pass --user"

# Keyed on docker-init, not on PID 1 alone, so a pod sharing its process namespace
# still starts.
[ "$$" = 1 ] || [ "$(cat /proc/1/comm 2>/dev/null)" != docker-init ] ||
    die "don't pass --init (compose: init: true): docker-init lacks CAP_KILL and can't forward docker stop's SIGTERM to the dropped worker; the image runs its own tini"

# Checked first: on cgroup v1 the next check would fail with a misleading hint. No
# docker run flag fixes v1, so this one skips die's flags hint.
[ -f "$CG/cgroup.controllers" ] || {
    echo "assess-entrypoint: the host must run cgroup v2 (unified hierarchy); cgroup v1/hybrid is unsupported" >&2
    exit 1
}

# A host cgroup namespace would mean remounting the HOST's hierarchy read-write.
[ "$(cat /proc/self/cgroup)" = "0::/" ] ||
    die "not in a private cgroup-v2 namespace ($(tr '\n' ' ' </proc/self/cgroup)); pass --cgroupns=private"

# Check every cap up front, and SETPCAP above all: without it setpriv skips the
# bounding-set drop silently instead of failing.
capeff=$(sed -n 's/^CapEff:[[:space:]]*//p' "/proc/$$/status")
for cap in CHOWN:0 SETGID:6 SETUID:7 SETPCAP:8 SYS_ADMIN:21; do
    [ $(((0x$capeff >> ${cap#*:}) & 1)) = 1 ] || die "missing CAP_${cap%:*}; pass --cap-add=${cap%:*}"
done

# Docker mounts a private cgroupns read-only. Remounting it takes SYS_ADMIN (checked
# above) plus an AppArmor profile that allows mount (docker-default doesn't).
mount -o remount,rw "$CG" ||
    die "cannot remount $CG read-write; pass --security-opt=apparmor=assess-nsjail (load deploy/apparmor/assess-nsjail on the host first)"

# cgroup v2's "no internal processes" rule: the ns root can enable controllers for
# its children only once it holds no processes. So move everything (just this shell)
# into a leaf. A pid that exited meanwhile is fine.
mkdir -p "$CG/worker" || die "cannot create $CG/worker"
while read -r pid; do
    echo "$pid" >"$CG/worker/cgroup.procs" 2>/dev/null || [ ! -d "/proc/$pid" ] ||
        die "cannot move pid $pid into $CG/worker"
done <"$CG/cgroup.procs"
echo "+cpu +memory +pids" >"$CG/cgroup.subtree_control" ||
    die "cannot enable the cpu/memory/pids controllers; the host must run cgroup v2 and delegate them to containers"

# Delegate, per the kernel's cgroup-v2.rst "Delegation Containment": the worker gets
# the ns-root directory plus its cgroup.procs, cgroup.threads and
# cgroup.subtree_control. That lets nsjail create NSJAIL.<pid> children there and
# move each jail into one. The ns root's own memory.max/pids.max/cpu.max stay
# root-owned, so the worker can't lift the ceiling Docker put on the container.
chown "$WORKER:$WORKER" "$CG" "$CG/cgroup.procs" "$CG/cgroup.threads" "$CG/cgroup.subtree_control" ||
    die "cannot hand $CG to $WORKER"

exec setpriv --reuid="$WORKER" --regid="$WORKER" --clear-groups \
    --inh-caps=-all --ambient-caps=-all --bounding-set=-all --no-new-privs -- /usr/bin/tini -- "$@"
