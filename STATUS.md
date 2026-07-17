# STATUS — Assessment Agent

Pending / next work **only**. Feature *history* is `git log` (commits are
per-slice and detailed) — there is deliberately no changelog file. Update this
file in the same commit that opens or closes an item (pre-push checkpoint #5).
Durable architecture / boundary / invariants live in CLAUDE.md + CONVENTIONS.md.

## Open items

- **LLM-surface evals — drafting baselined; adversarial needs one baseline run.**
  Both drafting and adversarial-gen now have anchored eval harnesses (offline they
  SKIP — no heuristic; logic covered by `tests/test_draft_eval.py` +
  `tests/test_adversarial_eval.py`, in the gate).
  - **Drafting** — `assess-draft-eval` ([draft_eval.py]): each brief must draft into a
    valid question whose own reference grades PASS 100%. **Baseline 3/3 on
    claude-sonnet-4-6 (2026-07-17):** two_sum 7+1, reverse_words 7+1, count_islands 9+1.
  - **Adversarial gen** — `assess-adversarial-eval` ([adversarial_eval.py]): the probe
    runs against known-correct references and must generate cases yet report ZERO
    findings (a finding on a correct solution = a false positive). **TODO: one live-key
    baseline run to record the anchor.**
  Re-run both after any model/prompt change.
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
