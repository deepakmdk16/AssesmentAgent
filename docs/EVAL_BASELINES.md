# Eval baselines

Measured baselines for the three LLM surfaces (judge, drafting, adversarial), plus
the decoding lesson behind the current sampling settings. This is **reference**, not
pending work — it is what checkpoint #4 compares a run against. Open work lives in
`../STATUS.md`.

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

### Local model — `qwen3-coder:30b`, 2026-07-21 (flake fixed 2026-08-03)

Its own baseline, not a substitute for the one above: with no `ANTHROPIC_API_KEY`
the provider auto-selects Ollama, so these are what `assess-*-eval` report on a
keyless machine. **All three surfaces now pass locally**, at $0 and with
candidate code never leaving the machine; the structured-output flake that made
the probe (and one drafting anchor) unreliable is root-caused and fixed — see
the greedy-decoding section below.

- **Judge — 7/7 verdicts, 7/7 complexity, 7/7 meets-constraints, $0.** Matches
  Sonnet on every anchor including both deterministic ones (strong→PASS,
  buggy→FAIL) and both TLE cases. The judge is the surface where a local model
  costs nothing and gives up nothing measurable.
- **Drafting — 3/3 anchors: two_sum 7+1, reverse_words 5+1, count_islands 8+1.**
  Close to Sonnet's 7+1 / 8+1 / 10+1. Before the prompt fix this was 3+1 / 3+1 /
  5+1: `correctness_inputs` asked for "several" small inputs, and a vague
  quantifier gets satisfied *minimally* by a weaker model — Sonnet reads "several"
  as 7-10, Qwen read it as 3. It now states a floor (>= 6, aim 8-10) plus a
  category checklist, and `min_correctness_cases` moved 3 -> 4 so the harness
  actually holds the line. Drafting also needed the decoding fix below before it
  was reliable on non-trivial briefs.
  **2026-08-03: the count_islands flake is root-caused and fixed.** It had
  flaked in both runs of a re-baseline session (a 2-attempt timeout at the
  120 s default, then `Unterminated string (char 2113)` with a 300 s budget) —
  confirmed by direct repro to be the same repetition loop as the probe's
  `knapsack_good` flake (surface-general, not adversarial-specific; the model
  loops emitting a grid literal `0101…` inside the `stdin` string field, or
  deliberately starts a forbidden 1000×1000 "max_size" grid, until cut off).
  Fixed by the escalated in-call retry in `ollama_chat` (see below). Post-fix:
  `assess-draft-eval` **4/4** (count_islands 8 corr + 1 perf, reference PASS
  100%; differentiation size bound rose N≈1e3→1e5) at the default timeout.
- **Adversarial — FIXED 2026-08-03: 2/2 in three consecutive runs at the
  default 120 s timeout.** Was flaky (2/2 once, then 1/2 twice): `strong`
  passed every time, `knapsack_good` intermittently emitted malformed JSON —
  once as a timeout at the 120 s default, once as `Unterminated string` with a
  300 s budget. Direct repro (16 baseline calls) put the per-call flake at
  ~40-50% for this anchor and pinned the cause: at temperature 0.3 the model
  still falls into a repetition loop (`1 1000\n` / `1 1` repeated ×50-80)
  inside the `stdin` string field — where Ollama's grammar-constrained
  decoding can't reach — runs to the 8192-token ceiling (~140 s, so it's the
  *same* event behind both the timeout face and the unterminated-JSON face),
  and gets cut off mid-string. The answer to "schema-specific or general?" is
  **general**: drafting's count_islands anchor failed with the identical
  signature. Fixed in `ollama_chat` (see the greedy-decoding section below).
  The probe remains opt-in and advisory, but no longer needs to be left off on
  a local deployment.
  **Fixed (2026-07-24):** the harness used to print "drew a finding (false
  positive)" for *every* failure, even a 0-case generation (a timeout or
  unparseable output). `_check` now returns distinct `EMPTY` vs `FINDING`
  statuses and the summary prints the guidance that matches the actual cause.

#### Greedy decoding traps a local model — both generative surfaces

The single most expensive lesson of the local-provider work, recorded because it
will recur with **any** local model and it presents as three unrelated bugs.

At `temperature: 0` a local model that starts emitting repetitive structure
cannot leave it. It hit both generative surfaces, in the same way, for the same
reason — a long run of similar-looking tokens:

- **Adversarial** — `knapsack_01`, the one question whose input format is *N
  repeated lines*, emitted `"1 1000\n"` forever: 1069 s, 0 cases probed, and it
  *still* timed out at `ASSESS_LLM_TIMEOUT_S=600`. Kadane's single-line array gave
  it nothing to loop on, which is why `strong` always passed.
- **Authoring** — a draft is two whole programs plus eight similar test inputs.
  On a shortest-path brief the JSON broke mid-string at char 2424 and repeated to
  the token ceiling (147 s, unparseable). Today's own prompt work made this
  *worse*: adding `brute_force_solution` and raising the case floor roughly
  doubled the output and pushed authoring over the same cliff.

It is not a comprehension failure — the adversarial prompt already forbade large
literal inputs. The model could not escape the loop to obey it.

Three changes, all verified end to end:

1. **Both generative Ollama paths run at `temperature 0.3`.** Adversarial: 138 s
   unparseable -> **7 s, 8 valid cases**. Authoring: 147 s unparseable -> **~25 s,
   8 correctness inputs**, and a live portal draft went from failing twice in
   298 s to validating in **24 s with no warnings**. The **judge stays at 0** — its
   output is short, non-repetitive, and score stability is worth keeping.
2. **Every local call carries a `num_predict` ceiling** (`ASSESS_OLLAMA_MAX_TOKENS`,
   default 8192). This is what turned an unbounded hang into a bounded failure
   *before* the temperature fix, and it still backstops any future runaway.
3. **`ASSESS_LLM_TIMEOUT_S` needs raising for local models** (120 s is Claude-tuned).
   Note the platform's `AGENT_DRAFT_TIMEOUT_S` must exceed
   `ASSESS_LLM_TIMEOUT_S * ASSESS_DRAFT_ATTEMPTS`, or it aborts a draft that is
   still working — that mismatch surfaced as a bogus 502 "couldn't reach the
   drafting service". **2026-08-03: the multiplier doubled** — with the in-call
   retry below, one `ollama_chat` call is worst-case ~2× `ASSESS_LLM_TIMEOUT_S`.

**Retries are worthless without sampling variation.** `_DRAFT_ATTEMPTS` exists
because "drafting is stochastic… asking again tends to produce a working draft."
That is true of Claude and **false at temperature 0**: the retry reproduced a
byte-identical failure at the same character offset, so two attempts only doubled
the wait. Any future retry/backoff logic on a local path must change *something*
between attempts.

**2026-08-03 — temperature 0.3 was necessary but not sufficient; the fix is an
escalated in-call retry.** Even at 0.3 the loop recurred on ~40-50% of
`knapsack_good` probe calls and ~15-25% of count_islands drafts (repro over
16 baseline calls per surface); the grammar-constrained `format` can't help
because the loop lives *inside* a string field, and the truncated reply
surfaced as either a client timeout (120 s default) or a baffling
`Unterminated string` JSON error (larger budget) — the same event wearing two
faces, plus a rarer third (a ~2 k-char reply with no `done_reason` at all).
`ollama_chat` now treats any incomplete reply (`done_reason` != "stop", or a
client timeout) as retryable ONCE, at temperature +0.3 **with
`repeat_penalty` 1.15 over a `repeat_last_n` 256 window** — the penalty taxes
exactly the just-repeated tokens a loop is made of, and it is what actually
breaks the attractor: measured recovery 7/7 failures vs 2/4 for a hotter
retry alone. The happy path is byte-identical to the baselined config (no
penalty on the first call), a doubly-incomplete call raises a diagnosis
naming the loop and both knobs instead of a JSON parse error, and both
attempts' tokens count toward usage. Verified live at the default timeout:
`assess-adversarial-eval` 2/2 × 3 consecutive runs, `assess-draft-eval` 4/4.
