# Report guidance (what to put in each field)

Return a structured assessment with exactly these fields:

- **criteria** — one entry per criterion (`robustness`, `readability`,
  `efficiency`, `design`), each with its `score` (1–5 integer) and a one-sentence
  `comment` naming the specific evidence for that score.
- **overall_score** — a single 1–5 number (may be fractional, e.g. 3.5),
  your holistic judgement of the code's quality.
- **time_complexity** — the solution's Big-O time complexity as a short string
  (e.g. `O(n)`, `O(n log n)`, `O(n^2)`). Base it on the code, consistent with
  the timing results.
- **meets_time_constraints** — `true` if that complexity is fast enough for the
  stated constraints (and the performance case did not TLE), else `false`.
- **strengths** — 1–3 short bullet points, each a concrete thing done well.
- **weaknesses** — 1–3 short bullet points, each a concrete, actionable issue.
  If there are none worth noting, return a single item saying so.
- **summary** — 2–3 sentences a hiring manager can read on its own: what the
  candidate did, how well, and the one thing that most affected the score.

## Style

- Be concrete and cite specifics ("uses `max()`/`sum()` in one pass",
  "no guard for empty input") rather than generic praise or criticism.
- Do not restate the test pass/fail count as if it were your finding — it is
  given to you. Focus on code quality.
- Keep every field terse. No preamble, no markdown headings inside field values.
