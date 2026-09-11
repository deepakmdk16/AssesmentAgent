"""Generate deploy/seccomp.json, the container-level seccomp profile (A07).

It is Docker's default profile with a small delta that lets nsjail build a jail from
an unprivileged user namespace. The input is moby's default profile pinned at a
commit, so regenerating it is reproducible:

    moby/profiles @ 61eaf32614c7c71b60bd8927d3e6a4ffc8ff1f31
    https://raw.githubusercontent.com/moby/profiles/61eaf32614c7c71b60bd8927d3e6a4ffc8ff1f31/seccomp/default.json

    python3 scripts/gen-seccomp.py             # downloads the pinned URL
    python3 scripts/gen-seccomp.py default.json  # or reads a local copy of it

The delta, and nothing else:

1. Drop the CAP_SYS_ADMIN-gated catch-all rule. The container holds SYS_ADMIN only
   so its root entrypoint can remount /sys/fs/cgroup. Docker builds the filter once,
   from the caps granted at start, and seccomp filters can't be loosened later. Left
   in, the rule would allow bpf, fsopen, perf_event_open, setns, syslog, unshare,
   and more to the worker and every jail long after the caps are gone.
2. Drop the flag-masked "clone" rule that applies on x86_64/aarch64 (it refuses
   namespace flags without SYS_ADMIN). Add ONE unconditional allow for clone, mount,
   umount2, pivot_root and sethostname. That is what nsjail needs from its
   unprivileged user namespace; --chroot needs pivot_root, which has no upstream entry
   at all. nsjail's own seccomp policy (sandbox.py) re-restricts the jailed child.
3. clone3 -> ENOSYS (errnoRet 38) unconditionally, not just without SYS_ADMIN.
   Its flags live in a struct seccomp can't inspect, and ENOSYS makes glibc fall
   back to clone, which rule 2 covers.
4. archMap: native ABI only (every subArchitectures emptied). Upstream lets an
   x86_64 container make i386 and x32 syscalls, and an aarch64 one make arm32
   syscalls. nsjail's kafel policy only knows native syscall numbers (it has no x32
   handling), so an x32 unshare (0x40000000 | 272) would slip past it. With no
   sub-architectures, libseccomp applies its bad-arch action to those calls.
"""

import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

PINNED_URL = (
    "https://raw.githubusercontent.com/moby/profiles/"
    "61eaf32614c7c71b60bd8927d3e6a4ffc8ff1f31/seccomp/default.json"
)
OUT = Path(__file__).resolve().parent.parent / "deploy" / "seccomp.json"


def _on_native_arch(rule: dict[str, Any]) -> bool:
    # A rule narrowed by includes.arches is for other arches (s390 etc.). One with
    # only excludes.arches, or no arch condition at all, applies on x86_64/aarch64.
    return not rule.get("includes", {}).get("arches")


def transform(profile: dict[str, Any]) -> dict[str, Any]:
    matched = {"sys_admin catch-all": 0, "clone": 0, "clone3": 0}
    rules = []
    for rule in profile["syscalls"]:
        if rule.get("includes", {}).get("caps") == ["CAP_SYS_ADMIN"] and _on_native_arch(rule):
            matched["sys_admin catch-all"] += 1  # (1)
            continue
        if rule["names"] == ["clone"] and _on_native_arch(rule):
            matched["clone"] += 1  # (2)
            continue
        if rule["names"] == ["clone3"]:
            matched["clone3"] += 1  # (3)
            rule = {
                "names": ["clone3"],
                "action": "SCMP_ACT_ERRNO",
                "errnoRet": 38,
                "comment": "A07: ENOSYS even with SYS_ADMIN, so glibc falls back to clone",
            }
        rules.append(rule)
    # The pin makes this deterministic; the check makes a future bump of the pin fail
    # loudly instead of silently producing a different profile.
    if list(matched.values()) != [1, 1, 1]:
        sys.exit(f"upstream profile changed shape, rules matched: {matched}")
    rules.append(
        {
            "names": ["clone", "mount", "umount2", "pivot_root", "sethostname"],
            "action": "SCMP_ACT_ALLOW",
            "comment": "A07: nsjail builds the jail from an unprivileged user namespace",
        }
    )
    profile["syscalls"] = rules
    for arch in profile["archMap"]:
        arch["subArchitectures"] = []  # (4)
    return profile


def main() -> None:
    if len(sys.argv) > 1:
        raw = Path(sys.argv[1]).read_text()
    else:
        with urllib.request.urlopen(PINNED_URL, timeout=30) as resp:
            raw = resp.read().decode()
    OUT.write_text(json.dumps(transform(json.loads(raw)), indent="\t") + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
