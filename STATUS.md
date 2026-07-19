# STATUS — Assessment Agent

Pending / next work, plus the small amount of **reference** data needed to tell a
regression from noise (the eval baselines). Feature *history* is `git log`
(commits are per-slice and detailed) — there is deliberately no changelog file.
Update this file in the same commit that opens or closes an item (pre-push
checkpoint #5). Durable architecture / boundary / invariants live in CLAUDE.md +
CONVENTIONS.md.

## Open items

### Next up: global (cross-repo) Claude-setup fixes
Found in the 2026-07-17 audit; these live in `~/.claude/` and are **shared with
`assessment-platform`**. The guard-hook/config gaps below are now fixed (guard.py
`rm -fr`/`rm -r -f`/`rm -Rf` all ask and root-target deletes deny — order- and
separation-independent, proven by `~/.claude/hooks/guard_probe.py`; Read of a
secret path now asks via the wired-in Read matcher; the stray
`~/.claude/.claude.json` is deleted). The home-wide Read grant was **checked and
is not present** in `assessment-platform` — it carries only a `~/.claude/**`-scoped
grant, not the machine-wide `Read(//Users/madiredeepakkumar/**)`. Remaining:

- Global `CLAUDE.md` §7 mandates serena-before-Read, but serena needs an explicit
  `activate_project` first, so the mandated call fails once per session and the
  natural recovery is the whole-file Read §7 exists to prevent. Worked around
  repo-side (see this repo's CLAUDE.md); the global rule should say so too.

### Rate limiting on the API (not started — decide the approach first)
`/assessments` and `/run` execute submitted code, and `adversarial: true` spends
API money per call. Fail-closed auth now gates *who* can reach them, but there is
no per-caller quota. Needs a dependency (e.g. slowapi) or a reverse proxy —
that choice is the blocker, not the code.

### Sandboxing the runner (the biggest production gap)
Today: a per-run timeout, an output cap (`RLIMIT_FSIZE`), a process-group kill so
a timeout takes the whole tree, and an address-space cap (`RLIMIT_AS`) that
applies to *some* languages. Nothing bounds fork bombs, network egress, or memory
on the JVM/Go paths. Real isolation is a container with no network, dropped
capabilities, and cgroups for **memory + pids**.

Two rlimits have now been tried and found to be the wrong instrument — worth
recording so a third attempt doesn't repeat it. **The lesson is that an rlimit
expresses a proxy, not the intent**; only a cgroup can say "this submission gets
N megabytes / M processes":
- `RLIMIT_NPROC` — counts per *UID*, not per process tree, so it cannot bound one
  submission. Set low it breaks legitimate code (the login session's own process
  count, ~686 locally, already exceeds any sane cap); set high it does nothing.
  Rejected; orphans are handled by the process-group kill instead.
- `RLIMIT_AS` — caps *address space*, not memory in use. The JVM and Go reserve
  GBs of untouched virtual space at startup, so the cap doesn't bound them, it
  stops them booting. Now skipped for those two via
  `Language.address_space_capped` (found by CI's first run, 2026-07-17). What it
  still buys: a runaway CPython allocation on Linux, and little else — it is
  silently ignored on macOS.

### The eval harnesses only ever run on one machine
The Java bug CI caught existed because a code path had never executed. The three
eval harnesses are one blind spot of the same shape: they SKIP without an API
key, so CI never exercises them and they only run on a dev Mac, by hand. Not
urgent, and running them in CI costs real money per run — but if they break,
nothing will say so. Consider a scheduled/manual-dispatch job with the key in
secrets.

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
