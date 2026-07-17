# STATUS — Assessment Agent

Pending / next work **only**. Feature *history* is `git log` (commits are
per-slice and detailed) — there is deliberately no changelog file. Update this
file in the same commit that opens or closes an item (pre-push checkpoint #5).
Durable architecture / boundary / invariants live in CLAUDE.md + CONVENTIONS.md.

## Open items

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
