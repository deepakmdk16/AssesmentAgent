# Scoring scale (applies to every criterion)

Use the whole range. Most real submissions land at 2–4; reserve 1 and 5 for
clear cases.

- **5 — Excellent.** Professional quality. A senior engineer would ship this
  with no changes on this dimension.
- **4 — Good.** Solid and idiomatic with only minor nits.
- **3 — Adequate.** Works and is acceptable, but has real, nameable shortcomings.
- **2 — Weak.** Noticeable problems that a reviewer would ask to be fixed.
- **1 — Poor.** Fails this dimension badly (e.g. unreadable, brittle, or
  wasteful enough to matter).

## Per-criterion anchors

- **robustness 5**: validates input and handles every realistic edge case.
  **robustness 3**: handles the main cases but silently assumes well-formed
  input. **robustness 1**: crashes or corrupts on trivially adversarial input.
- **readability 5**: clear names, obvious structure, idiomatic. **readability
  3**: understandable with a little effort. **readability 1**: cryptic names,
  no structure, hard to follow.
- **efficiency 5**: optimal for the problem with no wasted work. **efficiency
  3**: acceptable, with a redundant pass or allocation. **efficiency 1**:
  needlessly slow (e.g. quadratic where linear is trivial).
- **design 5**: clean decomposition, single clear responsibility per unit.
  **design 3**: works but flat or slightly tangled. **design 1**: spaghetti,
  dead code, or copy-paste.

For a simple problem that genuinely needs no error handling or optimization,
do not force robustness/efficiency to 1 — score what a reasonable engineer
would expect for a problem of that size.
