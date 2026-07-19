# STATUS — Assessment Agent

Pending / next work, plus the small amount of **reference** data needed to tell a
regression from noise (the eval baselines). Feature *history* is `git log`
(commits are per-slice and detailed) — there is deliberately no changelog file.
Update this file in the same commit that opens or closes an item (pre-push
checkpoint #5). Durable architecture / boundary / invariants live in CLAUDE.md +
CONVENTIONS.md.

## Open items

### Runner sandboxing — landed; prod bring-up remains
The OS sandbox that closes the fork-bomb / network-egress / JVM-Go-memory gap now
exists: `sandbox.py` wraps each untrusted child's argv in **nsjail** (fresh network
namespace = no egress, all capabilities dropped, cgroup-v2 **memory + pids**
ceilings), selected by `ASSESS_SANDBOX`. The `Dockerfile` bundles nsjail + every
toolchain and sets `ASSESS_SANDBOX=nsjail`, so production runs sandboxed by
default; macOS/dev/CI fall through to a no-op passthrough (today's rlimits + killpg
only). Forcing `ASSESS_SANDBOX=nsjail` where nsjail is missing fails the run loudly
rather than executing untrusted code open.

**Validated end-to-end on real nsjail** (Docker, `--privileged --cgroupns=host`,
2026-07-19): a correct submission runs, network egress is blocked, C compiles+runs,
and a 1 GB allocation is killed by a 256 MB cgroup — `test_sandbox_nsjail.py` passes
(it SKIPs where nsjail is absent, like the eval harnesses). Bring-up shook out four
real nsjail-flag facts now baked into `sandbox.py`: the rw bind flag is `--bindmount`
(not `--bindmount_rw`); nsjail owns the rlimits so the runner skips its preexec caps
under the sandbox (else RLIMIT_AS raise → EPERM); `--rlimit_as inf` + cgroup is what
bounds JVM/Go memory; and nsjail `execve()`s argv[0] literally so a bare `python3`
must be resolved to an absolute path and a minimal PATH/HOME injected (host env is
cleared, which usefully keeps secrets out of candidate code).

Still to do:
- **Optional hardening not yet added**: a seccomp-bpf syscall filter, and per-run
  cgroup CPU limits (today CPU is bounded only by the wall-clock timeout).
- **The uid remap stays off**: the jail runs as root-in-container (nsjail warns).
  The container is the outer boundary; revisit if the worker ever runs less isolated.

Recorded so a third rlimit attempt doesn't repeat it — **an rlimit expresses a
proxy, not the intent; only a cgroup can say "this submission gets N megabytes / M
processes"** (now delivered by nsjail's cgroup controllers above):
- `RLIMIT_NPROC` — counts per *UID*, not per process tree, so it cannot bound one
  submission. Rejected; orphans are handled by the process-group kill instead.
- `RLIMIT_AS` — caps *address space*, not memory in use. The JVM and Go reserve
  GBs of untouched virtual space at startup, so it stops them booting rather than
  bounding them; skipped for those two via `Language.address_space_capped`. It
  survives as best-effort on the passthrough path (a runaway CPython allocation on
  Linux); the nsjail cgroup is what actually bounds memory for every language now.

## Other pending work

- **Candidate-feedback agent (cross-repo, not yet chosen).** Once the platform can
  surface it — actionable feedback to candidates. Spans both repos.
- **Multiple examples per question (deferred).** `Question`/loader/report hold a
  single example; the authoring vision wants a list. Extend when the authoring UI
  needs it.
- **Parked cost optimizations.** Enum/coded judge output + repo-side prose catalog;
  Batch API on the email path (50% off, fits async delivery); warm-cache cadence /
  1-hour TTL. Revisit together. (See README → Future cost optimizations.) There is
  now real data to aim at: output is 3153 of ~7355 tokens per candidate, so the
  enum/coded-output idea targets the larger, more expensive half.
- **Composite score (optional).** Weighted verdict-score + quality. The
  `required_complexity`-in-report half is already done.

## Reference — eval baselines (not pending work)

Not open items; recorded here because CLAUDE.md checkpoint #4 points at them, and
because a bare "3/3 passed" can't distinguish a regression from normal variance
without them. **All green on claude-sonnet-4-6, 2026-07-17**, re-run after the
`llm.wrap_untrusted` prompt change — the fence degraded nothing.

Re-run all three after any model/prompt change. **Offline they SKIP, so a green
`pytest` is never evidence they passed.**

- **Judge** — `assess-eval` ([eval.py](assessment_agent/eval.py)): **7/7
  verdicts**, plus the reported (never gated) quality labels at 7/7 complexity and
  7/7 meets-constraints. Cost **$0.0109/candidate → ~$10.90 per 1,000** (4202 in /
  3153 out, 17730 cache-read — the rubric prefix caches as designed).
- **Drafting** — `assess-draft-eval` ([draft_eval.py](assessment_agent/draft_eval.py)):
  each brief must draft into a valid question whose own reference grades PASS
  100%. **3/3:** two_sum 7+1, reverse_words 8+1, count_islands 10+1. The case
  *counts* drift run to run (a previous baseline saw 7+1 / 9+1 for the last two)
  — the model proposes inputs and only those surviving the reference run are kept.
  Treat it as ~7-10 correctness + 1 perf, **not** a fixed number; the anchor that
  must not move is "the drafted question's own reference grades PASS 100%".
- **Adversarial gen** — `assess-adversarial-eval` ([adversarial_eval.py](assessment_agent/adversarial_eval.py)):
  the probe runs against known-correct references and must generate cases yet
  report ZERO findings (a finding on correct code = a false positive). **2/2:**
  strong + knapsack_good each probed 8, no crash/timeout.
