# STATUS — Assessment Agent

Pending / next work **only**. Feature *history* is `git log` (commits are
per-slice and detailed) — there is deliberately no changelog file. Update this
file in the same commit that opens or closes an item (pre-push checkpoint #5).
Durable architecture / boundary / invariants live in CLAUDE.md + CONVENTIONS.md.

## Open items

- **Adversarial-gen eval (next).** The drafting eval is live and baselined —
  `assess-draft-eval` ([draft_eval.py]) drafts a fixed set of briefs and asserts each
  is a usable, validated question whose own reference grades PASS 100% (offline it
  SKIPs; logic covered by `tests/test_draft_eval.py`). **Baseline 3/3 on
  claude-sonnet-4-6 (2026-07-17):** two_sum 7 corr+1 perf, reverse_words 7+1,
  count_islands 9+1, each reference PASS 100%. Re-run after a model/prompt change.
  Still TODO: a parallel eval for adversarial test-gen (adversarial.py), which is
  likewise only live-smoke-tested today.
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
