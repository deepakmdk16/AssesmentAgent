# STATUS — Assessment Agent

Pending / next work **only**. Feature *history* is `git log` (commits are
per-slice and detailed) — there is deliberately no changelog file. Update this
file in the same commit that opens or closes an item (pre-push checkpoint #5).
Durable architecture / boundary / invariants live in CLAUDE.md + CONVENTIONS.md.

## Open items

- **Deferred: global (cross-repo) Claude-setup fixes.** Found during the
  2026-07-17 audit but out of scope for a repo-level session — they live in
  `~/.claude/` and are shared with `assessment-platform`:
  - `~/.claude/hooks/guard.py` misses `rm -fr` and `rm -r -f` (its risky-tier
    regex requires `r` before `f` in one flag cluster, so both get **no** prompt;
    the catastrophic tier for `/` and `~` does still catch them). Add a probe
    script so the patterns stay proven.
  - `guard.py` guards Bash-reading-`.env` and Write/Edit-of-secrets but **not the
    Read tool** — add a Read matcher reusing its `sensitive` list.
  - Stray `~/.claude/.claude.json` (headroom-only, wrong directory, loaded by
    nothing) — delete; the real config is `~/.claude.json`.
  - Global `CLAUDE.md` §7 mandates serena-before-Read but serena needs an
    explicit `activate_project` first. Worked around repo-side (see CLAUDE.md);
    the global rule should say so too.
- **Rate limiting on the API (not started).** `/assessments` and `/run` execute
  code, and `adversarial: true` spends API money per call. Fail-closed auth now
  gates who can reach them, but there is no per-caller quota. Needs a dep
  (e.g. slowapi) or a reverse proxy — decide which before building.
- **Sandboxing the runner (unchanged, still the biggest production gap).** The
  runner now has a timeout, memory/output rlimits and a process-group kill, but
  nothing bounds fork bombs or network egress. Real isolation is a container with
  no network, dropped capabilities and cgroups (incl. the pids controller).
  `RLIMIT_NPROC` was tried and rejected: it counts per-UID, so it cannot bound one
  submission — set low it breaks legitimate code (the login session's own process
  count already exceeds any sane cap), set high it does nothing.

- **LLM-surface eval baselines (all green on claude-sonnet-4-6, 2026-07-17,
  re-run after the `llm.wrap_untrusted` prompt change).** The fence did not
  degrade any surface — every anchor held at full marks.
  - **Judge** — `assess-eval` ([eval.py]): **7/7 verdicts**, and the reported
    (never gated) quality labels also 7/7 complexity + 7/7 meets-constraints.
    Cost: **$0.0109/candidate → ~$10.90 per 1,000** (4202 in / 3153 out, 17730
    cache-read — the rubric prefix is caching as designed).
  - **Drafting** — `assess-draft-eval` ([draft_eval.py]): each brief must draft
    into a valid question whose own reference grades PASS 100%. **3/3:** two_sum
    7+1, reverse_words 8+1, count_islands 10+1. Note the case *counts* drift run
    to run (the previous baseline saw 7+1 / 9+1 for the last two) — the model
    proposes inputs and only those surviving the reference run are kept, so treat
    the count as ~7-10 correctness + 1 perf, not a fixed number. The anchor is
    "reference grades PASS 100%", which is what must not move.
  - **Adversarial gen** — `assess-adversarial-eval` ([adversarial_eval.py]): the
    probe runs against known-correct references and must generate cases yet report
    ZERO findings (a finding on a correct solution = a false positive). **2/2:**
    strong + knapsack_good each probed 8, no crash/timeout.
  Re-run all three after any model/prompt change — offline they SKIP, so a green
  `pytest` is never evidence they passed.
- **Candidate-feedback agent (cross-repo, not yet chosen).** Once the platform can
  surface it — actionable feedback to candidates. Spans both repos.
- **Multiple examples per question (deferred).** `Question`/loader/report hold a
  single example; the authoring vision wants a list. Extend when the authoring UI
  needs it.
- **Parked cost optimizations.** Enum/coded judge output + repo-side prose catalog;
  Batch API on the email path (50% off, fits async delivery); warm-cache cadence /
  1-hour TTL. Revisit together. (See README → Future cost optimizations.)
- **Composite score (optional).** Weighted verdict-score + quality. The
  `required_complexity`-in-report half is already done.
