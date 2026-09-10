# STATUS — Assessment Agent

**Open items only.** Anything already done is history and belongs in `git log`
(commits are per-slice and detailed; there is deliberately no changelog file).
Close an item by **deleting its lines** in the commit that closes the work
(pre-push checkpoint #5) — never by annotating it as DONE.

Eval baselines → `docs/EVAL_BASELINES.md` (checkpoint #4 reads them).
Durable architecture, boundary and invariants → CLAUDE.md + CONVENTIONS.md.
Organisation, billing, privacy and deploy epics live in
`../assessment-platform/STATUS.md`.

Priority: **P1** first paying customers hit it · **P2** fix before scale · **P3** polish.
Effort: **XS** minutes · **S** self-contained · **M** multi-file · **L** data + API + UI.

**Sequence:** (1) organisation → billing (platform X01 → X02) · (2) privacy and email
· (3) deploy + ops (A07) · (4) the rest by priority.

---

## Launch audit — 2026-09-06

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
  from the platform DB. Update .github/workflows/sandbox.yml's run flags in the
  same commit (see A35).
  _Verified: live run in this audit; source: trace,saas._
- **A35 · P2 · S — The live jail suite still has four uncovered properties, and its
run flags will drift when A07 lands.**
  Evidence: .github/workflows/sandbox.yml proves the memory and pids cgroups,
  --chroot / read-only, the net namespace, env clearing and the C compile path, but
  nothing proves (a) that the runner's killpg reaches through nsjail's PID namespace
  on a TLE — nsjail is our direct child, the payload is pid 1 of a child pidns, so a
  leaked spinner would still report timed_out=True; (b) --rlimit_fsize live (only the
  bytes→MB conversion is unit-pinned); (c) that the jailed process cannot READ
  /app/assessment_agent/questions.py — --chroot / makes the whole container readable,
  which is a grading-integrity hole, not only a security one; (d) go/rust/cpp/node/
  ruby in the jail (HOME=<workdir> exists for go's build cache and is untested).
  Also sandbox.yml hard-codes --privileged --cgroupns=host, duplicating Dockerfile:12
  with nothing keeping them in sync, and it cannot run on a fork PR (--privileged is
  root on the runner) — a skipped job satisfies a required status check, so a fork PR
  weakening the jail flags shows green; only the push-to-main run catches it. Why: A07 changes those flags (non-root USER, uid
  remap, --cap-add SYS_ADMIN, --seccomp_policy, --cgroup_cpu_ms_per_sec) and the job
  would keep proving a posture nobody deploys — A06's failure mode one level up.
  Fix: add the four cases; update sandbox.yml in the same commit as any A07 flag
  change, or source the flags from one place.
- **A23 · P1 · XS — The weekly keyed evals workflow has failed every Monday since
2026-07-27 (secret never set).**
  Evidence: gh run list --workflow evals.yml: 6 consecutive failures on schedule;
  failed step "Require ANTHROPIC_API_KEY" — secret unset; so checkpoint #4 has never
  run in CI and the Sonnet baselines STATUS.md still owes (T3 differentiation, F4
  floor) remain unrun. Why: an LLM-surface regression would ship unnoticed; the gate
  exists but is permanently red. Fix: set the repo secret (or make the job
  skip-with-notice); run the owed Sonnet baselines and record them.
  _Verified: live run in this audit; source: live._
- **A22 · P1 (was P2) · S — A cgroup memory kill is indistinguishable from an
empty-output wrong answer.**
  Evidence: live /run with a 1 GB allocation: stdout "", timed_out=false,
  infra_error=null, compile_error=null, duration 0.115 s — no signal that the
  process was killed; CONVENTIONS §4 requires failure kinds be distinguished (infra
  vs compile vs TLE vs wrong answer). A06 (546be6d) raised the reachability:
  sandbox.py now passes --cgroup_mem_swap_max 0, so on any host with swap a
  submission over the ceiling is killed outright where it used to spill to swap and
  survive — slowly, and often surfacing as a legible TLE. The silent kill is now the
  normal path rather than the exceptional one, which is what moves this to P1.
  Why: interviewer and candidate cannot tell "memory limit exceeded" from "printed
  nothing", so a candidate is marked wrong for what is really a resource verdict.
  Fix: detect SIGKILL/exit 137 or read cgroup memory.events → new outcome kind MLE
  surfaced in report/run output.
  _Verified: live run in this audit; reachability re-assessed when A06 landed;
  source: live._
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
  (auto → 422); CLAUDE.md:100 "each
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
  Raised by X08's integration check: X08 built the two exits this takes — an
  aggregator (`ASSESS_LOG_FORMAT=json`) and Sentry breadcrumbs, which capture
  INFO records by default — so the same line now leaves the box on two new
  channels. The platform did the symmetric work (`LOG_PII` gating plus the
  access-line redaction); this repo has no `LOG_PII` equivalent, which is what
  makes this the agent's P08. Fix: gate the recipient/candidate log lines the
  way the platform gates `email_client`.

- **A34 · P3 · XS — /health is a static ok; no readiness signal.**
  Evidence: api.py:283-285 returns {"status":"ok"} unconditionally; no check of
  toolchains/nsjail/LLM reachability. Why: a worker missing nsjail (forced sandbox)
  reports healthy and then fails every run. Fix: readiness that verifies sandbox
  availability when ASSESS_SANDBOX=nsjail.
  _Verified: cited lines read in this audit; source: saas._

---

## Backlog — unscheduled

- **Sandbox hardening.** A seccomp-bpf syscall filter and per-run cgroup CPU limits
  (CPU is bounded only by the wall-clock timeout today). The uid remap stays off —
  the jail runs as root-in-container and the container is the outer boundary.
  Tracked as A07; don't duplicate it here.
- **Spec precision in drafted questions.** The oracle cross-check catches a *wrong*
  reference, not an *underspecified* one. Measured across 5 hard briefs: 0/5 drafts
  mentioned integer overflow, and neither ambiguous brief pinned its ambiguity. A
  correct candidate still fails on a rule the prompt never stated.
- **Variant-set parity: regenerate, don't just warn.** `_check_set_parity` is
  advisory and post-hoc. Add regeneration or backoff for a drifting variant if
  parity warnings prove common in practice.
- **Candidate-feedback agent** (cross-repo, not yet chosen) — actionable feedback to
  candidates, once the platform can surface it.
- **Net-new agent-side ideas.** Reference generated in the candidate's own language;
  difficulty auto-calibration from real pass-rates. Full cross-repo list:
  `../assessment-platform/STATUS.md`.
- **Multiple examples per question (deferred).** `Question`/loader/report hold a
  single example; the authoring vision wants a list. Extend when the UI needs it.
- **Parked cost optimizations.** Enum/coded judge output plus a repo-side prose
  catalog; Batch API on the email path (50% off, fits async delivery); warm-cache
  cadence / 1-hour TTL. Revisit together, and aim at output tokens: 3153 of ~7355
  per **eval run of 7 cases** — not per candidate. See README → Future cost
  optimizations.
- **Composite score (optional).** Weighted verdict-score + quality; the
  `required_complexity`-in-report half is already done.
