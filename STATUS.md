# STATUS — Assessment Agent

Pending / next work, plus the small amount of **reference** data needed to tell a
regression from noise (the eval baselines). Feature *history* is `git log`
(commits are per-slice and detailed) — there is deliberately no changelog file.
Update this file in the same commit that opens or closes an item (pre-push
checkpoint #5). Durable architecture / boundary / invariants live in CLAUDE.md +
CONVENTIONS.md.

## Open items

### The F4 case-floor rejects pre-floor questions at grade time — P0 (cross-repo)
`MIN_CORRECTNESS_CASES = 4` (F4) is enforced by `validate_question`, which runs on
**every inline question at grade-time intake** (`loader.py:78` ← `question_from_dict`
← `api.py:392`, the synchronous 400 guard). So a question authored before the floor
existed — the platform has `grid_path_minimize` with **3** correctness cases — now
hard-fails **every candidate submission** with `400 invalid question: … needs at
least 4 'correctness' cases`. The platform marks the submission `error` and returns
502; the candidate is punished for the interviewer's question shape (found in manual
testing 2026-07-24).

The defect is *where* the invariant is checked, not the floor itself. A case-count
floor is an **authoring** invariant; enforcing it on the **grading** path fails the
one actor who can't fix it. **Fix direction:** on the grade/intake path, downgrade
the case-floor (and similar authoring-only invariants) from a hard 400 to a
**warning** carried in the result — keep grading the code — while keeping the floor
**hard** for authoring/drafting (`draft_eval`, `authoring.py`) where it belongs. The
platform half (validate at question *creation*, and flag existing offenders) is
tracked in `../assessment-platform/STATUS.md` A1/A2.

**Standing lesson (A2 there) — flag early, degrade gracefully when tightening a
shared invariant.** F4 made `validate_question` stricter and silently invalidated
already-stored data. When a shared invariant tightens: (1) **flag** existing rows
that would now fail (a deploy-time check that lists offenders), and (2) **degrade**
rather than hard-fail on paths where the data owner can't act. Worth writing into
CONVENTIONS.md when this is picked up — it will recur.

### Report endpoint for platform PDF download — AR3 (cross-repo)
`report.py` renders a PDF but is reachable only via CLI/email. The platform stores
the *serialized* result dict, while `build_report_pdf` wants the rich
`AssessmentResult`. Agent half: add `POST /report` + a `result_from_dict` inverse of
`result_to_dict` (nested, parity-sensitive), so the platform can proxy + serve it.
Platform half (proxy + download button) is in `../assessment-platform/STATUS.md`.

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

- **Local-LLM provider (landed + baselined; the probe is not yet dependable).**
  `llm.py` selects `ASSESS_LLM_PROVIDER` (anthropic default / ollama local via
  `ollama_chat`). **Judge, authoring, and adversarial** all route through it,
  verified live on `qwen3-coder:30b`: judge — strong O(n)→4.5, buggy O(n²)→1.5
  with complexity flagged; authoring — a Kadane brief drafted into a valid
  question whose own executed reference validates (warnings=[]); adversarial —
  probed 8 cases on correct code with 0 findings (no false positives). All $0,
  schema-valid, with offline routing/degradation unit tests per surface. **Qwen
  now has its own eval baseline** (see the reference section below): judge 7/7 at
  $0 and drafting 3/3 are solid locally; **the adversarial probe is flaky on Qwen
  and must not be relied on** (details below). Both eval harnesses used to gate
  `SKIP` on `ANTHROPIC_API_KEY` directly, which hid that failure as a skip; they
  now gate on `provider()`, so a configured-but-failing local backend reports
  FAIL.
- **Authoring: drafted references are now cross-checked (landed; one gap left).**
  A drafted `reference_solution` is the oracle — every `expected` comes from
  executing it — so a reference that is *wrong but deterministic* used to pass
  every check and then mark correct candidates wrong. `DraftSpec` now carries an
  optional `brute_force_solution` and `_cross_check_oracle` re-derives each small
  correctness case with it, **dropping** any case the two disagree on (the
  performance case is never brute-forced). Missing/broken/timed-out second
  opinions degrade to a warning. Measured on 5 hard briefs × `qwen3-coder:30b`:
  5/5 emitted a brute force, 0 disputes, and all 5 references verified correct
  against independently-written brute forces (1800 random cases, 0 mismatches).
  **The prompt fix matters as much as the check:** the first run produced a false
  positive that destroyed a good question — the "brute force" was a second DP that
  invented its own input format, misparsed, printed `0`, and outvoted a correct
  reference. `question_draft.md` now requires the brute force to parse the
  reference's exact stdin and to contain no DP/heap/memo.
  Still open: **spec precision**, the defect this does *not* catch. Across the
  same 5 briefs, 0/5 drafts mentioned integer overflow and neither ambiguous brief
  pinned its ambiguity (Damerau-Levenshtein OSA-vs-unrestricted; the strict
  `a[i] == 2*a[j]` boundary). A correct candidate still fails on a rule the prompt
  never stated. Also 1/5 still used `queue<>` with only `<stack>` included — it
  builds on libc++ via a transitive include and would fail elsewhere.
- **Test-case floor landed (F4) — draft-eval RE-RUN DONE (local).** `validate_question`
  now requires **≥ 4 correctness cases** (`MIN_CORRECTNESS_CASES`, exempts the perf
  case), matching the draft-eval's `min_correctness_cases`, so both hand-authored
  and AI-drafted questions must clear it. Offline unit tests updated + green.
  **Re-baselined 2026-07-23 on `qwen3-coder:30b`: assess-draft-eval 3/3, drafts at
  7 / 5 / 7 correctness — all above the floor, so it rejects nothing real.** A
  Sonnet re-run (needs a key) would confirm on that model, but the floor is
  structural and the local run is strong evidence.
- **Difficulty now has prompt semantics (T3) — no-regression CONFIRMED (local);
  differentiation still unmeasured.** `DIFFICULTY: easy|medium|hard` used to be a
  bare label; `question_draft.md` now has a "Calibrating to the requested difficulty"
  section tying each level to concrete levers (constraint size → forced complexity,
  algorithmic depth, edge-case emphasis). **assess-draft-eval re-run 2026-07-23 on
  `qwen3-coder:30b` with the new prompt active: 3/3, every draft's reference still
  grades PASS 100% — the difficulty section did not regress drafting.** Still owed
  (not blocking): new eval cases that draft the *same* brief at easy/medium/hard and
  assert the constraint sizes / required complexity actually diverge — the current
  harness confirms drafts stay valid, not that "hard" is harder than "easy".
  **Enforce, don't just instruct (raised 2026-07-24):** the difficulty→levers
  mapping is a *soft prompt* (`question_draft.md` "Calibrating to the requested
  difficulty"), verified only on local qwen. Difficulty IS wired end to end
  (platform UI → `agent_client.py:131` → `DIFFICULTY:` hint → the levers, so
  "medium binary search on the answer, N≤1e5" vs "easy direct binary search" vs
  "hard rotated-array" is what the prompt *asks* for) — but nothing checks the model
  obeyed. Add a **deterministic post-draft guard** that reads back the drafted
  `constraints` / `required_complexity` and warns (or rejects) when they fall outside
  the requested difficulty band, so calibration is checked, not hoped for.
- **Multi-question set generation (cross-repo, enables per-candidate variants).**
  Add an orchestration that drafts **K variants** for one brief + difficulty by
  calling the existing single-question drafter K times (each keeps its executed-oracle
  guarantee) — **not** one prompt asking for K questions, which dilutes each and
  wrecks quality parity. Pin `difficulty` + `target_complexity` across the set and
  reuse the difficulty guard above as a **parity check** so no variant is harder than
  its siblings. Platform half (UI + orchestration trigger) tracked in
  `../assessment-platform/STATUS.md`.
- **Candidate-feedback agent (cross-repo, not yet chosen).** Once the platform can
  surface it — actionable feedback to candidates. Spans both repos.
- **Net-new agent-side ideas (unscheduled).** Per-candidate unique question variants
  (compounds the executed-oracle moat + anti-cheat), reference generated in the
  candidate's own language, and difficulty auto-calibration from real pass-rates.
  Full cross-repo idea list lives in `../assessment-platform/STATUS.md` §D (the old
  PRODUCT_BACKLOG was consolidated there and deleted, 2026-07-24).
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

### Local model — `qwen3-coder:30b`, 2026-07-21

Its own baseline, not a substitute for the one above: with no `ANTHROPIC_API_KEY`
the provider auto-selects Ollama, so these are what `assess-*-eval` report on a
keyless machine. **Judge and drafting pass locally**, at $0 and with candidate
code never leaving the machine; **the adversarial probe is flaky — see below.**

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
- **Adversarial — FLAKY: 2/2 once, then 1/2 twice. Do not treat as green.**
  `strong` passes every time; `knapsack_good` is the unstable one. The decoding
  fix below cured the *hang* (it no longer runs to the token ceiling), but the
  local model still intermittently emits malformed JSON for this anchor — once as
  a timeout at the 120 s default, once as `Unterminated string (char 1427)` with a
  300 s budget, so it is **not** a timeout problem. Temperature 0.3 escapes the
  repetition loop at the cost of genuine run-to-run variance, and this surface
  sits close enough to the edge that the variance shows.
  **Consequence: the probe is not yet dependable on a local model.** It is opt-in
  and advisory (a failure never touches a verdict), so a local deployment should
  leave it off or point it at Claude until this is understood. Next step is to
  find whether the malformed JSON is specific to this question's schema/size or a
  general structured-output weakness at 30B.
  Still worth a fix: the harness's failure line reads "drew a finding (false
  positive)" even when the real cause was a timeout or a parse error with zero
  findings — it sends you after the wrong thing.

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
   drafting service".

**Retries are worthless without sampling variation.** `_DRAFT_ATTEMPTS` exists
because "drafting is stochastic… asking again tends to produce a working draft."
That is true of Claude and **false at temperature 0**: the retry reproduced a
byte-identical failure at the same character offset, so two attempts only doubled
the wait. Any future retry/backoff logic on a local path must change *something*
between attempts.
