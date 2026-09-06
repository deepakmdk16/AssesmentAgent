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

- **Local-LLM provider (landed + baselined; the flake root-caused + fixed
  2026-08-03).** `llm.py` selects `ASSESS_LLM_PROVIDER` (anthropic default /
  ollama local via `ollama_chat`). **Judge, authoring, and adversarial** all
  route through it, verified live on `qwen3-coder:30b`: judge — strong O(n)→4.5,
  buggy O(n²)→1.5 with complexity flagged; authoring — a Kadane brief drafted
  into a valid question whose own executed reference validates (warnings=[]);
  adversarial — probed 8 cases on correct code with 0 findings (no false
  positives). All $0, schema-valid, with offline routing/degradation unit tests
  per surface. **Qwen now has its own eval baseline** (see the reference section
  below): judge 7/7 at $0, drafting 4/4, adversarial 2/2×3 consecutive runs —
  the malformed-JSON flake on the probe and on grid-shaped drafting was
  root-caused (a repetition loop inside a JSON string field, general to both
  generative surfaces) and is now recovered by an escalated in-call retry in
  `ollama_chat` (details in the greedy-decoding section below). Both eval
  harnesses used to gate `SKIP` on `ANTHROPIC_API_KEY` directly, which hid that
  failure as a skip; they now gate on `provider()`, so a configured-but-failing
  local backend reports FAIL.
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
  differentiation now measured (DONE 2026-08-03).** `DIFFICULTY: easy|medium|hard`
  used to be a bare label; `question_draft.md` now has a "Calibrating to the
  requested difficulty" section tying each level to concrete levers (constraint
  size → forced complexity, algorithmic depth, edge-case emphasis).
  **assess-draft-eval re-run 2026-07-23 on `qwen3-coder:30b` with the new prompt
  active: 3/3, every draft's reference still grades PASS 100% — the difficulty
  section did not regress drafting.** The once-owed differentiation eval now
  exists: `DIFFERENTIATION_CASES` (`pair_sum_tiers`) drafts the *same* brief at
  easy/medium/hard — deliberately pinning no `target_complexity`, so difficulty
  alone must move the levers — and `_differentiation_verdict` requires a strict
  easy→hard separation (complexity rank rises, size bound rises ≥10× — the
  parity guard's own drift threshold — or hard states a big bound where easy
  states none parseable). A size bound that *shrinks* as tiers rise fails as
  inverted; a falling complexity rank alone does not (a hard problem's insight
  can BE a low bound, per the calibration guard), it just isn't separation.
  Verdict logic is pure and offline-unit-tested (10 tests, incl. the harness
  half through the real parsers); live it passed 2/2 this session on
  `qwen3-coder:30b` (size bound rose N≈1e3→1e5). Keyed Sonnet baseline still
  owed at the next checkpoint-#4 run.
  **Enforce, don't just instruct — post-draft guard DONE 2026-07-26.** The
  difficulty→levers mapping was a *soft prompt* with nothing checking the model
  obeyed. `authoring._check_difficulty_calibration` now reads the two levers back
  after a draft validates and **warns** (never rejects — difficulty is a soft
  signal, so a mislabel must not throw away a valid question the way a broken
  oracle does) on a clear mismatch with the requested difficulty: `constraints`
  size outside the tier's band (easy too large / medium+hard too small to force
  the naive to TLE), `required_complexity` heavier than an easy tier or trivial
  for medium, and a difficulty-independent **feasibility** cross-check (the
  claimed complexity at the stated N must clear the time limit, else the
  reference a candidate matches would itself TLE). It stays silent when a lever
  can't be parsed and the bands are wide, so false positives are near zero; the
  two parsers (`_parse_size_bound`, `_complexity_rank`) are the intended
  **parity check** for multi-question set generation. No prompt change, so the
  eval baselines are unaffected (a keyed re-run is confirmatory, not required).
  The differentiation eval this paragraph used to owe landed 2026-08-03 — see
  the T3 entry above (`DIFFERENTIATION_CASES` in the draft eval).
  Note the guard is heuristic on free-text
  `constraints`: it skips sizes < 1e3 (can't tell an upper bound from the `1` in
  `1 ≤ n`), so a "medium, N≤100" mislabel slips through — the common large-bound
  miscalibration is what it catches.
- **Multi-question set generation (cross-repo) — agent half DONE 2026-07-26; the
  platform half has since shipped too (VS1 #36 + VS2 #37), so only the
  regeneration follow-up at the end of this entry is still open.** `authoring.draft_question_set`
  drafts **K variants** for one brief by calling `draft_question` K times at the
  **same** pinned `difficulty` + `target_complexity` (K independent, executed-oracle
  drafts — **not** one prompt asking for K questions, which dilutes each and wrecks
  parity). `_check_set_parity` reuses the calibration parsers (`_parse_size_bound`,
  `_complexity_rank`) to warn when siblings drift — differing complexity rank, or
  size bounds ≥10× apart — so no candidate gets an easier variant than another;
  advisory only, silent on unparseable levers, like the calibration guard. Returns
  a `DraftSetResult` (per-variant `DraftResult`s + set-level warnings; a variant
  shortfall is warned, not fatal). **`POST /questions/draft-set` on `assess-api`
  DONE 2026-07-26** (`count` 2..8, same token/signature/rate-limit guards as
  `/questions/draft`, shares the `draft` rate bucket): returns every variant +
  set-level warnings; a partial set (some variants unusable) still 200s so the
  caller judges whether the usable count suffices, only an all-failed set 422s;
  offline `ANTHROPIC_API_KEY`-absent path 503s like the single draft.
  Offline-tested end to end. The platform half (UI to request a K-variant set,
  storage, per-candidate assignment, and variant-set slots inside an assessment)
  **landed 2026-07-26** — see `../assessment-platform/STATUS.md` §A VS1/VS2.
  **Still to do here:** parity is heuristic post-hoc (warn, don't regenerate the
  outlier) — regeneration/backoff of a drifting variant is a later refinement if
  parity warnings prove common.
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

## SaaS-launch audit — 2026-09-06 (open items, ordered P0 → P3)

**How this list was produced (2026-09-06).** Every DONE claim in both STATUS files was traced
to code and tests by five independent read-only audits (agent claims, platform backend,
web frontend, SaaS readiness, code quality), then the high-impact claims were checked
live: both `checkpoints.sh` gates green (agent 265 passed / 4 skipped, platform 266
passed + web 113 vitest + build), Playwright E2E 9/9, the real-wire cross-repo smoke
(`scripts/smoke_e2e.py`) PASS, the full platform API flow (register → question →
assessment → invite → start → draft/events/run → submit → callback → attempts/CSV/
analytics → archive/delete → cross-owner 403) against a real **Postgres 16** with all 19
migrations applied, the agent **Docker image built** and its nsjail sandbox exercised
under `--privileged` over HTTP (egress blocked, 1 GB alloc killed, forks capped at 63,
C compiles, env clean), and every P1 below re-read at the cited lines. **Not validated:**
the three LLM surfaces (judge / drafting / adversarial) — no `ANTHROPIC_API_KEY` on the
machine and the local Ollama install is broken (see A24), and the weekly keyed CI evals
have been red since 2026-07-27 (A23). An adversarial re-verification workflow was
started; 19 independent refuters ran before the account session limit stopped it and
all 19 confirmed their finding (tagged below) — the remaining VERIFY-status items carry
the tag "single-audit claim". Priorities: **P0** blocks taking money or endangers
customers · **P1** first paying customers hit it · **P2** fix before scale · **P3** polish.
Effort: XS minutes · S self-contained · M multi-file · L data + API + UI.

Agent-repo items only (33: P0: 1, P1: 8, P2: 14, P3: 10). Cross-repo and platform items
(the organisation/billing/privacy/deploy epics, and the grading-durability epic that
spans both repos) live in `../assessment-platform/STATUS.md` §E.

**Suggested sequence.** (1) the cheap P0/P1 correctness + deploy blockers (P01, P02, P04,
A01, A03, P09, P19, A32, W01, W02); (2) the grading-durability epic as one change
(A04 + P05 + P10 + P15: platform-owned job table, id minted before trigger, background
reaper with auto-retry); (3) accounts → organisation → billing (P13 → X01 → X02);
(4) privacy (X03, X04) and email/notifications (X06, X07); (5) deploy + ops (X05, X08,
A06, A07, P26, X11); (6) everything else by priority. Close each item by deleting it
here in the same commit (checkpoint #5).

- **A03 · P0 · S — Concurrent grading jobs are not serialised; preexec_fn forked
from a multithreaded parent.**
  Evidence: api.py:516 `background.add_task(_run_job, …)` (sync → anyio threadpool);
  sync /run api.py:402-473 and /run/tests run there too; runner.py:9-15 states
  serial execution is required (TLE timing + preexec_fn deadlock); runner.py:262
  uses preexec_fn on the passthrough path; grep Semaphore|Lock( in api.py/runner.py:
  none. Why: perf-case TLE becomes load-dependent (false FAIL); on non-nsjail
  deploys a grade can hang forever. Fix: process-wide execution lock/semaphore
  around run_submission (or single worker thread + queue); bound concurrent /run;
  document per-instance throughput.
  Verifier note: refuter confirmed: Confirmed. api.py:516 add_task(_run_job) and
  sync def /run (402) and /run/tests (429) all dispatch via Starlette
  run_in_threadpool (background.py:23); no Lock/Semaphore in api.py/runner.py or
  platform.
  _Verified: cited lines read in this audit; 2 independent refuter(s) confirmed;
  source: trace,saas,quality._
- **A01 · P1 · XS — Container as documented is unreachable (uvicorn binds
127.0.0.1).**
  Evidence: assessment_agent/api.py:676 host default "127.0.0.1"; Dockerfile:12
  documents `docker run -p 8000:8000`; live probe: default CMD unreachable after 45
  s (listener 0100007F:1F40), with ASSESS_API_HOST=0.0.0.0 reachable in 9 s. Why:
  first production deploy fails. Fix: `ENV ASSESS_API_HOST=0.0.0.0` in Dockerfile +
  document the variable.
  _Verified: live run in this audit; source: trace,saas,live._
- **A02 · P1 · S — Auto-Ollama provider default breaks a keyless worker and
contradicts the docs.**
  Evidence: assessment_agent/llm.py:78-93 returns "ollama" whenever
  ANTHROPIC_API_KEY is absent; Dockerfile sets no key, no ASSESS_LLM_PROVIDER, no
  OLLAMA_HOST; judge.py:173-180 degrades to FAILED_ENGINE "unavailable";
  /questions/draft returns 422 "Draft generation failed: connection refused" not the
  documented 503 (api.py:313, authoring.py:268-272); live: with no key the eval
  harnesses selected ollama and every call failed. Why: every customer report says
  quality "unavailable"; README/CLAUDE promise the offline heuristic. Fix:
  auto-select ollama only when OLLAMA_HOST is set (else anthropic → heuristic), or
  pin ENV ASSESS_LLM_PROVIDER in Dockerfile; fix the 503 path; update docs.
  Verifier note: refuter confirmed: Confirmed. llm.py:93 returns "ollama" with no
  key/no ASSESS_LLM_PROVIDER; Dockerfile sets only ASSESS_SANDBOX. judge.py:173-180
  → FAILED_ENGINE. api.py:313-321 raises 503 only for OFFLINE_ENGINE, so t.
  _Verified: cited lines read in this audit; 2 independent refuter(s) confirmed;
  source: trace,live._
- **A04 · P1 (was P0) · M — In-flight jobs are lost on restart; replicas cannot see
each other's jobs.**
  Evidence: api.py:170-176 in-memory `_JOBS` OrderedDict; 202 returned before work
  runs (api.py:516); no persistence; crash between 202 and _post_callback
  (api.py:572-573) sends nothing; GET /assessments/{id} 404s on another replica
  (api.py:528-530); ratelimit.py:5-6 per-process. Why: at-most-once grading: a
  candidate's submission silently vanishes on deploy/OOM. Fix: platform owns a
  durable jobs table and re-triggers unacknowledged submissions after a deadline
  (pairs with P05/P10/P15); agent emits an error callback on shutdown.
  Verifier note: one refuter argued P2 because the platform persists the submission
  before the 202 and _reap_stale_running heals it after 15 min; kept P1 because that
  healing needs a manual interviewer retry (see P10).
  _Verified: cited lines read in this audit; 2 independent refuter(s) confirmed;
  source: trace,saas._
- **A05 · P1 · M — POST /questions/draft-set is synchronous and unbounded in time.**
  Evidence: api.py:333-362 runs draft_question_set inline; each variant up to
  _DRAFT_ATTEMPTS=2 calls × (2× ASSESS_LLM_TIMEOUT_S with the in-call retry);
  count=8 can hold a request open for over an hour; platform STATUS.md already
  records an AGENT_DRAFT_TIMEOUT_S mismatch for a single draft. Why: the platform
  502s while the worker keeps burning model calls. Fix: make draft-set an async job
  (202 + callback) like /assessments, or draft variants in parallel under a hard
  budget.
  Verifier note: refuter confirmed: Core claim holds: api.py:333-362 sync route
  calls draft_question_set inline; authoring.py:326-331 drafts sequentially, :247
  attempts=2, no deadline; api.py:193 count<=8; uvicorn.run (api.py:678) has n.
  _Verified: single-audit claim, not independently re-verified; 2 independent
  refuter(s) confirmed; source: trace._
- **A06 · P1 · S — The sandbox (security boundary) has no CI coverage.**
  Evidence: .github/workflows/checkpoints.yml runs on ubuntu-latest without nsjail;
  tests/test_sandbox_nsjail.py:18-21 SKIPs there; no workflow builds the Dockerfile;
  the file has only 2 tests (correct run, egress) while STATUS.md:22-25 claims C
  compile + cgroup OOM are covered; live: image builds and both tests pass inside a
  privileged container, and manual /run probes confirmed egress blocked, 1 GB alloc
  killed, forks capped at 63, C compiles. Why: a regression in _nsjail_wrap flags or
  the image ships green. Fix: CI job that builds the image and runs
  test_sandbox_nsjail.py inside it; add OOM-kill, pids and C-compile cases; fail
  (not skip) when CI=1.
  Verifier note: refuter confirmed: Accurate: checkpoints.yml has no nsjail/docker
  step; test_sandbox_nsjail.py:18-21 skips; neither repo builds the Dockerfile.
  Partly mitigated: tests/test_sandbox.py:69-115 runs in CI and asserts --ifa.
  _Verified: cited lines read in this audit; 2 independent refuter(s) confirmed;
  source: trace,quality._
- **A07 · P1 · M — Worker runs as root with --privileged; uid remap off; no seccomp;
no CPU cgroup.**
  Evidence: Dockerfile has no USER (live: `id -u` = 0 in image); Dockerfile:12-17
  prescribes --privileged --cgroupns=host; sandbox.py:73-77 jail is
  root-in-container (nsjail warns at runtime); "CAP_SYS_ADMIN alone suffices" is
  asserted not validated; STATUS.md:33-37 lists seccomp + CPU cgroup as optional.
  Why: an nsjail escape is host root; rules out Cloud
  Run/Fly/Railway/Render/Fargate, needs VMs or privileged k8s pods. Fix: validate +
  document the minimal cap set; non-root USER with writable temp root; enable uid
  remap (fix 0700 workdir ownership); add --seccomp_policy and
  --cgroup_cpu_ms_per_sec; run on dedicated VMs (or gVisor/Firecracker) isolated
  from the platform DB.
  _Verified: live run in this audit; source: trace,saas._
- **A23 · P1 · XS — The weekly keyed evals workflow has failed every Monday since
2026-07-27 (secret never set).**
  Evidence: gh run list --workflow evals.yml: 6 consecutive failures on schedule;
  failed step "Require ANTHROPIC_API_KEY" — secret unset; so checkpoint #4 has never
  run in CI and the Sonnet baselines STATUS.md still owes (T3 differentiation, F4
  floor) remain unrun. Why: an LLM-surface regression would ship unnoticed; the gate
  exists but is permanently red. Fix: set the repo secret (or make the job
  skip-with-notice); run the owed Sonnet baselines and record them.
  _Verified: live run in this audit; source: live._
- **A32 · P1 · XS — /docs and /openapi.json are public on the code-execution
worker.**
  Evidence: no docs_url=None / openapi_url=None in assessment_agent/api.py (FastAPI
  default on). Why: the full route surface of an internal code-execution service is
  discoverable. Fix: disable docs on the agent (keep the platform's behind auth or
  as a published spec).
  _Verified: cited lines read in this audit; source: saas._
- **A08 · P2 · XS — Dockerfile production hygiene.**
  Evidence: no HEALTHCHECK (/health exists); base debian:bookworm-slim by tag not
  digest; apt packages unpinned (Dockerfile:38,43-53); uv + nsjail are pinned. Why:
  orchestrators can't detect a wedged worker; builds not reproducible. Fix:
  HEALTHCHECK CMD curl -f http://127.0.0.1:8000/health; pin base digest.
  Verifier note: refuter confirmed: Accurate: Dockerfile has no HEALTHCHECK; FROM
  debian:bookworm-slim by tag (lines 26,38); apt unpinned (27-31, 43-53); uv pinned
  (58), nsjail tag 3.4 (33). /health exists (api.py:283). No compose/k8s/C.
  _Verified: cited lines read in this audit; 1 independent refuter(s) confirmed;
  source: trace._
- **A09 · P2 · S — Unbounded request bodies on question/result dicts; /report has no
rate bucket.**
  Evidence: api.py:196-200,234-237,266-274 cap `code` (200 KB) and /run stdin (2 MB)
  but `question: dict` and `result: dict` are unbounded; uvicorn has no default body
  limit; api.py:376 /report has no bucket. Why: memory DoS by any token holder. Fix:
  cap total test-case stdin/expected bytes in _QuestionSpec; give /report a bucket.
  Verifier note: refuter confirmed: Confirmed: api.py:197-200/234/266-274 leave
  question/result dicts unbounded; loader.py:31-37 stdin/expected are bare str; no
  body middleware; uvicorn 0.51 Config has no body-size option; /report (api..
  _Verified: single-audit claim, not independently re-verified; 1 independent
  refuter(s) confirmed; source: trace._
- **A10 · P2 · S — SSRF guard is bypassable with non-canonical IP literals and
blocks legitimate VPC callbacks.**
  Evidence: api.py:82-105: anything ipaddress.ip_address can't parse is treated as a
  hostname and allowed ("127.1", "2130706433" resolve to loopback via getaddrinfo);
  conversely a private PLATFORM_BASE_URL (http://10.x.x.x:9000) is rejected with
  400, so in-VPC deploys fail. Why: a token holder can make the worker POST results
  to internal services; VPC deploy breaks. Fix: resolve the host and check every
  resolved address, plus an explicit ASSESS_CALLBACK_ALLOWLIST for the platform
  host.
  Verifier note: refuter confirmed: Confirmed. api.py:100-101 returns for any
  unparseable host; verified 127.1, 2130706433, 0x7f000001 and "localhost." all pass
  the guard and getaddrinfo/httpx resolve them to 127.0.0.1. Platform's defau.
  _Verified: cited lines read in this audit; 1 independent refuter(s) confirmed;
  source: trace,saas._
- **A11 · P2 · XS — Compile step lacks the process-group kill.**
  Evidence: runner.py:349-363 uses subprocess.run(..., timeout=) without
  start_new_session/_kill_tree, unlike _run_case (runner.py:266-281). Why: a
  gcc/javac timeout kills only the direct child on passthrough → orphaned cc1/JVM
  processes. Fix: route compile through the same Popen + _kill_tree path.
  _Verified: cited lines read in this audit; source: trace._
- **A13 · P2 · S — Rate limiter is per-IP but the only caller is the platform → a
global limit across tenants.**
  Evidence: ratelimit.py:41-57, api.py:153-158: one platform IP →
  ASSESS_ASSESSMENTS_RATE_LIMIT_MAX=30 is 30 grades/min for everyone; behind a proxy
  client_ip needs ASSESS_TRUST_PROXY_HEADERS or every caller shares a bucket
  (ratelimit.py:67-85); README calls it "per-client"; limiter is in-memory so a
  second replica doubles every limit. Why: spurious 429s at modest scale. Fix: key
  on a platform-supplied tenant/candidate header or move limiting to the platform
  (which has a DB backend); document defaults for a single-platform deploy.
  Verifier note: refuter confirmed: Confirmed: ratelimit.py:41-57/67-85 key on
  (bucket, socket peer); api.py:153-158 assessments=30, run=60; single uvicorn
  process (api.py:678); README:224 "per-client". Platform is sole caller; nothing .
  _Verified: single-audit claim, not independently re-verified; 1 independent
  refuter(s) confirmed; source: trace,saas._
- **A14 · P2 · S — /run can exhaust the sync threadpool.**
  Evidence: api.py:229-231 allows time_limit_s ≤ 30, ×3 for Python (languages.py:57)
  → 90 s per synchronous request; anyio's default 40 threads → ~40 in-flight /run
  block every sync route including /assessments. Why: one candidate mashing Run
  stalls grading for everyone. Fix: lower the cap or bound concurrent executions
  with the semaphore from A03.
  _Verified: single-audit claim, not independently re-verified; source: trace._
- **A15 · P2 · S — Other candidates' workdirs are readable inside the jail.**
  Evidence: sandbox.py:100-103 chroots to / read-only with the jail as root
  (sandbox.py:73-77); every /tmp/assess_* workdir (runner.py:340, mode 0700 root) of
  a concurrently running submission is readable. Why: cross-candidate source
  disclosure. Fix: bind a private tmpfs over /tmp exposing only the own workdir, or
  run each jail under a distinct uid.
  _Verified: single-audit claim, not independently re-verified; source: trace._
- **A16 · P2 · XS — assess-eval exits 0 and counts a failed judge as a real model
when the LLM is unavailable.**
  Evidence: live run with Ollama down: "Eval engine: unavailable", complexity 0/7,
  exit code 0; eval.py:51 treats FAILED_ENGINE as real_model; no tests import
  eval.py although CLAUDE.md:100 claims every harness has a unit-tested half. Why:
  checkpoint #4 can be fooled by exit status; misleading baselines. Fix: exit
  non-zero when the engine is FAILED_ENGINE; exclude it from tallies; add
  tests/test_eval.py.
  _Verified: live run in this audit; source: trace,live._
- **A17 · P2 · XS — CONVENTIONS §2 violations — raw verdict/category literals and a
6× duplicated case-status ternary.**
  Evidence: draft_eval.py:60-61 raw "correctness"/"performance", :74 raw "PASS";
  report.py:49-53 colour maps keyed by raw "PASS"/"FAIL"/"ERROR"; `"PASS" if
  o.passed else ("TLE" if o.timed_out else "FAIL")` copy-pasted at
  agent.py:72,180,232, api.py:467, cli.py:38,52, report.py:270. Why: exactly the
  drift the constants exist to catch. Fix: use CORRECTNESS/PERFORMANCE/PASS from
  constants; add CaseStatus Literal + one case_status(o) helper.
  Verifier note: refuter confirmed: Confirmed: 6 identical ternaries
  (agent.py:72,180; api.py:467; cli.py:38,52; report.py:270), raw literals at
  draft_eval.py:60-61,74 (no constants import) and report.py:49-53. agent.py:232 is
  the inver.
  _Verified: cited lines read in this audit; 1 independent refuter(s) confirmed;
  source: trace,quality._
- **A19 · P2 · XS — compare_digest on a non-ASCII header raises TypeError → 500
instead of 401.**
  Evidence: api.py:129 `secrets.compare_digest(x_assess_token or "", expected)` on
  str; CPython raises TypeError for non-ASCII str inputs. Why: attacker-supplied
  header turns auth failure into a 500 (error-rate pollution). Fix: compare
  .encode() bytes.
  Verifier note: refuter confirmed: Confirmed. api.py:129 compares str; reproduced
  with TestClient: header bytes b'\xe9' (Starlette latin-1-decodes to non-ASCII)
  yields 500 vs 401 for ASCII. Both h11 and httptools accept obs-text header.
  _Verified: single-audit claim, not independently re-verified; 1 independent
  refuter(s) confirmed; source: trace._
- **A21 · P2 · XS — nsjail's own warnings leak into candidate-visible stderr.**
  Evidence: live /run in the privileged image: stderr = "[W]…logParams():313 Process
  will be UID/EUID=0 in the global user namespace…" for a memory-killed run; this
  reaches CandidateRunOut and reports. Why: candidates see sandbox internals (and
  that it runs as root); pollutes stderr comparisons. Fix: pass nsjail --quiet / -l
  <logfile> and keep candidate stderr clean.
  _Verified: live run in this audit; source: live._
- **A22 · P2 · S — A cgroup memory kill is indistinguishable from an empty-output
wrong answer.**
  Evidence: live /run with a 1 GB allocation: stdout "", timed_out=false,
  infra_error=null, compile_error=null, duration 0.115 s — no signal that the
  process was killed; CONVENTIONS §4 requires failure kinds be distinguished (infra
  vs compile vs TLE vs wrong answer). Why: interviewer and candidate cannot tell
  "memory limit exceeded" from "printed nothing". Fix: detect SIGKILL/exit 137 or
  read cgroup memory.events → new outcome kind MLE surfaced in report/run output.
  _Verified: live run in this audit; source: live._
- **A24 · P2 · XS — LLM surfaces were NOT validated live in this audit (no key;
local Ollama install is broken).**
  Evidence: the Ollama server on this machine answers /api/tags but every chat 500s:
  "llama-server binary not found" — it points at a deleted scratchpad path from an
  earlier session (…/121d110c…/scratchpad/ollama-app/Ollama.app/…), `ollama` CLI is
  not on PATH; all three harnesses therefore failed (judge: engine unavailable;
  draft 0/4; adversarial 0/2). Why: nothing exercised judge/draft/adversarial since
  the 2026-09-05 flake fix; local checkpoint #4 is blocked. Fix: reinstall Ollama
  properly (brew install ollama, or the official app) and re-run assess-eval /
  assess-draft-eval / assess-adversarial-eval against the STATUS baselines.
  _Verified: live run in this audit; source: live._
- **A31 · P2 · S — Docs drift bundle (agent): CLAUDE.md/README/STATUS contradict the
code.**
  Evidence: CLAUDE.md:79 + STATUS.md:53 "anthropic default" vs llm.py:83-93 auto;
  README.md:48-51,94-97 + CONVENTIONS.md:49-51 "absent key → offline heuristic" (now
  Ollama is tried first); CLAUDE.md:125-126, README.md:134-137, STATUS.md:187-188,
  draft_eval.py:5-6, adversarial_eval.py:5-6 "offline they SKIP" (they FAIL under
  auto; assess-eval has no SKIP path); STATUS.md:154-155 "key-absent path 503s"
  (auto → 422); STATUS.md:22-25 nsjail test coverage overstated; CLAUDE.md:100 "each
  eval has a unit-tested half" (not eval.py); README.md:210-217 endpoint table omits
  POST /report and /questions/draft-set, README.md:226-227 + CLAUDE.md:85-87
  rate-limit lists omit draft-set; Dockerfile:12 run command unreachable; cli.py:167
  "--to defaults to the built-in recipient" vs mailer.py:9-13;
  api.py:216-217,316,356, authoring.py:20-22, adversarial.py:19-21 docstrings
  pre-date Ollama; agent.py:224 "future warnings"; STATUS.md:175-176 misreads the
  7-case eval totals as per-candidate tokens (actual ≈ $0.011 judge cost per
  candidate); README.md rate limiter "per-client". Why: every future reader (human
  or agent) inherits a lie; checkpoints.sh only guards module/script names. Fix: one
  docs pass fixing each line; extend the docs-drift gate to endpoint tables.
  _Verified: single-audit claim, not independently re-verified; source:
  trace,quality._
- **A12 · P3 (was P2) · XS — _email_report can turn a decided grade into an ERROR
callback.**
  Evidence: api.py:648-663 catches only RuntimeError; a build_report_pdf failure
  (reportlab ValueError, disk full) propagates to _run_job's catch-all
  (api.py:567-570) which records status error and posts an error callback. Why:
  platform stores ERROR for a submission that graded fine. Fix: catch Exception in
  _email_report (it already returns a structured failure).
  Verifier note: refuter: unreachable from the platform, which always sends
  email_to=None; still wrong for CLI/direct API callers.
  _Verified: cited lines read in this audit; 1 independent refuter(s) confirmed;
  source: trace._
- **A18 · P3 (was P2) · XS — result_from_dict drops `warnings`, so /report PDFs omit
the F4 advisory.**
  Evidence: agent.py:276-291 never passes warnings although result_to_dict emits it
  (agent.py:208); docstring at agent.py:224 still calls warnings "future". Why:
  interviewer report silently omits the question-shape warning. Fix: pass
  warnings=data.get("warnings", []) and update the docstring.
  Verifier note: refuter: build_report_pdf never reads result.warnings either, so
  the fix is to render warnings in the PDF and pass them through.
  _Verified: cited lines read in this audit; 1 independent refuter(s) confirmed;
  source: trace._
- **A20 · P3 · S — HMAC signature has a 5-minute replay window with no nonce
(documented as accepted).**
  Evidence: signing.py:27,51; a captured /assessments body can be re-posted to spawn
  duplicate jobs/callbacks/emails. Why: duplicate grades/emails. Fix: optional nonce
  cached for the tolerance window.
  _Verified: single-audit claim, not independently re-verified; source: trace._
- **A25 · P3 · XS — _JOBS registry mutated from worker threads without a lock.**
  Evidence: api.py:176 OrderedDict; _record_job (api.py:540-543) does move_to_end +
  eviction from threadpool threads while request threads .get. Why: worst case
  over-eviction; not a crash. Fix: threading.Lock in _record_job/get_assessment.
  _Verified: single-audit claim, not independently re-verified; source: quality._
- **A26 · P3 · XS — Three identical `# type: ignore[call-overload]` on the same
Anthropic call.**
  Evidence: adversarial.py:255, authoring.py:859, judge.py:269 each wrap
  client.messages.create(...) identically. Why: three copies of one workaround. Fix:
  one typed llm.claude_call(...) wrapper.
  Verifier note: refuter confirmed: Accurate: adversarial.py:255, authoring.py:859,
  judge.py:269 each carry the identical ignore, identical 3-line justification
  comment, plus duplicated output_config/thinking kwargs, refusal check, text.
  _Verified: cited lines read in this audit; 1 independent refuter(s) confirmed;
  source: quality._
- **A27 · P3 · XS — LLM text lands in the contract's `reason` field unconstrained.**
  Evidence: agent.py:143 appends quality.time_complexity to reason; judge.py:49
  types it as bare str with no validator. Why: a submission that steers the judge
  can put arbitrary prose in the field the platform reads by name and shows the
  interviewer (verdict unaffected). Fix: regex-validate time_complexity or keep it
  out of reason.
  _Verified: single-audit claim, not independently re-verified; source: quality._
- **A28 · P3 · S — authoring.py (903 lines) has two clean split seams.**
  Evidence: calibration pure functions (authoring.py:94, 576-696) are imported as
  private names by draft_eval.py:31;
  build_from_spec/_cross_check_oracle/_build_performance_case (368-575) is the
  deterministic oracle half; LLM halves at 783-886. Why: eval imports private names;
  the module mixes three concerns. Fix: calibration.py (public names) +
  draft_build.py; leave api.py/runner.py alone.
  _Verified: cited lines read in this audit; source: quality._
- **A29 · P3 · S — Lint/type strictness and tooling hygiene below what the code
already satisfies.**
  Evidence: ruff set E,F,I,N,B,UP only; enabling S,SIM,RUF,PL,PT,ARG,T20,ERA ≈ 240
  real fixes; mypy ignore_missing_imports global (comment says it's for anthropic);
  tests excluded from mypy; mypy --strict = 39 errors (35 bare dict/list);
  .pre-commit-config pins ruff v0.8.4 / mypy v1.13.0 vs lock ruff 0.15.21 / mypy
  2.2.0; .gitignore lacks .env, *.db, .DS_Store, dist/; dev group re-lists httpx.
  Why: hooks and `uv run` can disagree; strictness gap grows with the codebase. Fix:
  pre-commit autoupdate; scope the mypy override; add the ignore lines; adopt the
  stricter ruff set with per-file-ignores for tests.
  _Verified: cited lines read in this audit; source: quality._
- **A33 · P3 · XS — Recipient emails are logged in plain text.**
  Evidence: api.py:657,662 and mailer.py:136 log the recipient address (PII) at
  INFO. Why: PII in logs without a redaction knob (platform has LOG_PII; agent has
  none). Fix: redact or gate behind an ASSESS_LOG_PII flag.
  _Verified: single-audit claim, not independently re-verified; source: trace._
- **A34 · P3 · XS — /health is a static ok; no readiness signal.**
  Evidence: api.py:283-285 returns {"status":"ok"} unconditionally; no check of
  toolchains/nsjail/LLM reachability. Why: a worker missing nsjail (forced sandbox)
  reports healthy and then fails every run. Fix: readiness that verifies sandbox
  availability when ASSESS_SANDBOX=nsjail.
  _Verified: cited lines read in this audit; source: saas._

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
