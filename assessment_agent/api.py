"""HTTP intake for the Assessment Agent (Phase 2).

This service is a **stateless assessment worker**. An external platform triggers
a job by POSTing the full question (prompt, constraints, I/O cases, example) plus
the candidate's submitted code — the platform owns question storage; the agent
keeps none. The agent runs the same pipeline the CLI uses (`agent.assess`:
deterministic execution + scoring, with a non-gating Sonnet quality summary) and
delivers the result asynchronously:

    POST /assessments            -> 202 {job_id, status: "accepted"}
      (the work runs in the background; when done the agent POSTs the full result
       to `callback_url` and/or emails the PDF to `email_to`. The platform mints
       `job_id` and sends it in the body — a repeat of a job still in flight is
       acknowledged again, not graded twice, so a trigger whose 202 was lost can
       be retried safely. Absent, the agent mints one.)
    GET  /assessments/{job_id}   -> {status, result?}   polling fallback
    GET  /health

Alongside grading there are two synchronous, non-grading execution endpoints that
back a candidate's editor. Neither calls an LLM, produces a verdict, or records a
job — they exist so a candidate can try their code before committing to a submit:

    POST /run        -> run once against caller-supplied stdin; return its output
    POST /run/tests  -> run the question's tests; return pass/fail per case ONLY
                        (no input/expected/actual — that's the answer key)

Run it with:  `uv run assess-api`  (or `uvicorn assessment_agent.api:app`).

The in-memory `_JOBS` registry is transient run-state for the polling fallback,
**not** a datastore — a multi-instance deployment should rely on `callback_url`,
not this map. Durability is the platform's: it owns the job record and
re-triggers what it never hears back about. The agent's part is to be honest on
the way down — on graceful shutdown (SIGTERM) it waits `ASSESS_SHUTDOWN_GRACE_S`
for in-flight grades, then POSTs an error callback for each one still running so
the platform re-queues it now rather than after its stale-job timeout (see
`_flush_pending_callbacks`). A hard kill (SIGKILL/OOM) sends nothing; the
platform's reaper covers that.

Auth is a shared-secret bearer token in the `X-Assess-Token` header
(env `ASSESS_API_TOKEN` for inbound, `CALLBACK_TOKEN` for the outbound callback)
and is **fail-closed**: with `ASSESS_API_TOKEN` unset every authenticated route
returns 503 unless `ASSESS_AUTH_DISABLED=1` is set to opt out explicitly (dev and
tests). Forgetting to configure a token must not silently publish an endpoint
that executes arbitrary code.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import tempfile
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .agent import assess, result_from_dict, result_to_dict
from .authoring import draft_question, draft_question_set, draft_set_to_dict, draft_to_dict
from .constants import OFFLINE_ENGINE
from .languages import LANGUAGES
from .loader import question_from_dict
from .ratelimit import client_ip, limiter
from .report import build_report_pdf
from .runner import run_once, run_submission
from .signing import SIGNATURE_HEADER, sign, verify

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    # Shutdown: uvicorn has already waited its graceful timeout for in-flight
    # background jobs; whatever is still running will never deliver. Tell the
    # platform now so it re-queues them (see `_flush_pending_callbacks`).
    _flush_pending_callbacks()


app = FastAPI(
    title="Assessment Agent",
    description="Stateless intake worker: grade a candidate submission against a supplied question.",
    version="0.2.0",
    # This is an internal code-execution worker, not a public API: don't
    # advertise its route surface. The platform is the only intended caller and
    # already knows the contract (README documents it for humans).
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=_lifespan,
)

# Shared-secret auth (see also the platform's callback auth). Bearer tokens in the
# `X-Assess-Token` header, enforced only when the corresponding env var is set —
# unset means auth is disabled (dev/tests); production must set both.
#   ASSESS_API_TOKEN — required on inbound POST /assessments (this worker).
#   CALLBACK_TOKEN   — sent on the outbound callback so the platform can verify us.
_AUTH_HEADER = "X-Assess-Token"


def _validate_callback_url(url: str) -> None:
    """Reject a callback that would make the worker hit itself or the internal
    network (SSRF). Guards the scheme and literal internal IPs (loopback, private,
    link-local — including the cloud metadata address — and reserved). No DNS is
    done, so a hostname that *resolves* to an internal IP isn't caught here; that
    residual (rebinding) needs network egress controls, noted as future work."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400, detail=f"callback_url must be http(s), got {parsed.scheme!r}."
        )
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="callback_url has no host.")
    if host.lower() == "localhost":
        raise HTTPException(status_code=400, detail="callback_url must not target localhost.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # a hostname (not a literal IP) — allowed
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
        raise HTTPException(
            status_code=400, detail=f"callback_url must not target an internal address ({host})."
        )


def _require_token(x_assess_token: str | None = Header(default=None)) -> None:
    """Fail-closed shared-secret auth.

    An unset `ASSESS_API_TOKEN` used to mean "auth disabled", so simply forgetting
    to configure it left an endpoint that runs arbitrary submitted code open to
    anyone. Now the omission is a 503 and disabling auth has to be said out loud
    with `ASSESS_AUTH_DISABLED=1`.
    """
    expected = os.environ.get("ASSESS_API_TOKEN")
    if not expected:
        if os.environ.get("ASSESS_AUTH_DISABLED") == "1":
            return
        raise HTTPException(
            status_code=503,
            detail=(
                "server auth is not configured: set ASSESS_API_TOKEN, or set "
                "ASSESS_AUTH_DISABLED=1 to run without auth (dev only)."
            ),
        )
    # Constant-time compare so the shared secret can't be recovered by timing the
    # response. `compare_digest` needs two strings, so coalesce a missing header.
    if not secrets.compare_digest(x_assess_token or "", expected):
        raise HTTPException(status_code=401, detail=f"invalid or missing {_AUTH_HEADER}.")


async def _require_signature(request: Request) -> None:
    """Verify the platform's HMAC body signature when signing is configured.

    Enforced only when `ASSESS_SIGNING_SECRET` is set — like the bearer token,
    unset means "not configured" so dev/tests run without it. The bearer token
    proves the caller knows a secret that travels in the clear; this proves they
    know one that never does, and that the body wasn't altered in flight. Async so
    it can read the raw body — Starlette caches it, so the route still parses it.
    """
    secret = os.environ.get("ASSESS_SIGNING_SECRET")
    if not secret:
        return
    if not verify(secret, await request.body(), request.headers.get(SIGNATURE_HEADER)):
        raise HTTPException(status_code=401, detail="invalid or missing request signature.")


# Per-client-IP rate limits for the endpoints that execute untrusted code or spend
# API money — auth gates *who* can reach them, this caps how hard one caller can
# hammer them. 0 disables a bucket. Kept in a dict (read at request time) so a test
# can lower a limit without re-importing the module.
_RATE_LIMIT_WINDOW_S = int(os.environ.get("ASSESS_RATE_LIMIT_WINDOW_S", "60"))
_RATE_LIMITS: dict[str, int] = {
    "assessments": int(os.environ.get("ASSESS_ASSESSMENTS_RATE_LIMIT_MAX", "30")),
    "run": int(os.environ.get("ASSESS_RUN_RATE_LIMIT_MAX", "60")),
    "draft": int(os.environ.get("ASSESS_DRAFT_RATE_LIMIT_MAX", "10")),
}


def _rate_limit(bucket: str) -> Callable[[Request], None]:
    """A route dependency that 429s once this client exceeds `bucket`'s window."""

    def dep(request: Request) -> None:
        limiter.check(bucket, client_ip(request), _RATE_LIMITS[bucket], _RATE_LIMIT_WINDOW_S)

    return dep


# job_id -> {"status": "accepted"|"done"|"error", "result": dict|None, "error": str|None}
# This is the polling fallback's transient run-state, not a datastore, so it is
# bounded: once it holds _MAX_JOBS entries, inserting a new one evicts the oldest
# (FIFO). Otherwise a long-lived worker would accumulate every job forever.
# Callers that need durable results should use `callback_url`, not this map.
_MAX_JOBS = int(os.environ.get("ASSESS_MAX_JOBS", "1000"))
_JOBS: OrderedDict[str, dict[str, Any]] = OrderedDict()

# job_id -> callback_url for every accepted job that has not delivered yet. The
# shutdown flush error-callbacks whatever is still here; `_run_job` removes its
# entry before posting the real result, so a job finishing during the flush is
# reported once, by whichever side got there first.
_PENDING_CALLBACKS: dict[str, str] = {}

# Ceiling on submitted source. Every endpoint here accepts attacker-supplied text
# and buffers it in memory, so an unbounded body is a free denial of service. Far
# above any real interview answer (~200 KB is thousands of lines) and well below
# anything that threatens the worker.
_MAX_CODE_CHARS = 200_000
# Ceiling on caller-supplied stdin for the ad-hoc "Run" path. Matches the cap the
# adversarial probe puts on model-generated inputs, so both paths agree on how
# large an input the runner will ever be handed.
_MAX_STDIN_CHARS = 2_000_000
# Ceiling on an authoring brief. This one becomes an LLM prompt, so the cap is
# about cost as much as memory.
_MAX_BRIEF_CHARS = 20_000
# A variant set costs one full draft per variant, so bound how many a single call
# can request. Small sets (2-5) are the intended use — enough to hand different
# candidates different siblings without a runaway bill.
_MAX_SET_COUNT = 8


class AssessmentRequest(BaseModel):
    question: dict = Field(
        description="The full question the candidate answered (same shape as a "
        "question JSON: title, prompt, constraints, test_cases, example, ...)."
    )
    code: str = Field(
        min_length=1,
        max_length=_MAX_CODE_CHARS,
        description="The candidate's submitted source code.",
    )
    language: str = Field(description=f"Submission language; one of {sorted(LANGUAGES)}.")
    candidate: str = Field(default="Candidate", description="Candidate name for the report.")
    callback_url: str | None = Field(
        default=None, description="If set, the full result JSON is POSTed here when done."
    )
    email_to: str | None = Field(
        default=None, description="If set, the PDF report is emailed to this address."
    )
    adversarial: bool = Field(
        default=False,
        description="Run advisory adversarial edge-case probes (needs ANTHROPIC_API_KEY "
        "on the worker). Reported separately; never affects the score or verdict.",
    )
    job_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="Caller-minted correlation id, echoed in the 202 and the callback. A "
        "repeat for a job still in flight is acknowledged, not re-run. Minted here if absent.",
    )


class RunRequest(BaseModel):
    code: str = Field(min_length=1, max_length=_MAX_CODE_CHARS, description="Source to execute.")
    language: str = Field(description=f"One of {sorted(LANGUAGES)}.")
    stdin: str = Field(
        default="",
        max_length=_MAX_STDIN_CHARS,
        description="Input fed to the program on stdin.",
    )
    time_limit_s: float = Field(
        default=2.0, gt=0, le=30, description="Base per-run time limit (scaled per language)."
    )


class RunTestsRequest(BaseModel):
    question: dict = Field(description="The full question (same shape as POST /assessments).")
    code: str = Field(min_length=1, max_length=_MAX_CODE_CHARS, description="Source to execute.")
    language: str = Field(description=f"One of {sorted(LANGUAGES)}.")


class DraftRequest(BaseModel):
    brief: str = Field(
        min_length=1,
        max_length=_MAX_BRIEF_CHARS,
        description="Natural-language description of the problem.",
    )
    language: str = Field(
        description=f"Language for the reference solution; one of {sorted(LANGUAGES)}."
    )
    difficulty: str | None = Field(
        default=None, description="Optional difficulty hint (e.g. 'medium')."
    )
    target_complexity: str | None = Field(
        default=None,
        description="Optional target Big-O for the intended solution (e.g. 'O(N log N)').",
    )


class DraftSetRequest(DraftRequest):
    count: int = Field(
        ge=2,
        le=_MAX_SET_COUNT,
        description=f"How many sibling variants to draft (2..{_MAX_SET_COUNT}).",
    )


class ReportRequest(BaseModel):
    result: dict = Field(
        description="The serialized assessment result (a `result_to_dict` payload, "
        "as delivered on the grade callback and stored by the platform)."
    )
    question: dict = Field(
        description="The full question the candidate answered (same shape as POST "
        "/assessments) — the serialized result omits everything but the id/title."
    )
    code: str = Field(
        min_length=1,
        max_length=_MAX_CODE_CHARS,
        description="The candidate's submitted source, rendered verbatim in the report.",
    )
    candidate: str = Field(default="Candidate", description="Candidate name for the report title.")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/questions/draft",
    dependencies=[
        Depends(_require_token),
        Depends(_require_signature),
        Depends(_rate_limit("draft")),
    ],
)
def draft(req: DraftRequest) -> dict:
    """Draft a validated Question from a brief. Stateless: stores nothing — the
    platform persists the result on human approval. Claude drafts the prose,
    constraints, a reference (oracle) solution, and the test inputs; the agent
    executes the reference through the deterministic runner to fill each case's
    expected output, then validates the assembled question."""
    if req.language not in LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported language {req.language!r}; expected one of {sorted(LANGUAGES)}.",
        )
    result = draft_question(
        req.brief,
        language=req.language,
        difficulty=req.difficulty,
        target_complexity=req.target_complexity,
    )
    if result.engine == OFFLINE_ENGINE:
        raise HTTPException(
            status_code=503,
            detail="drafting requires a live model (set ANTHROPIC_API_KEY on the worker).",
        )
    payload = draft_to_dict(result)
    if result.question is None:
        # Draft ran but produced nothing usable — surface the warnings, don't 200.
        raise HTTPException(status_code=422, detail=payload)
    return payload


@app.post(
    "/questions/draft-set",
    dependencies=[
        Depends(_require_token),
        Depends(_require_signature),
        Depends(_rate_limit("draft")),
    ],
)
def draft_set(req: DraftSetRequest) -> dict:
    """Draft a **set** of `count` sibling variants of one brief at a pinned
    difficulty, for per-candidate unique variants. Each variant is an independent,
    executed-oracle draft (see `draft_question_set`); the response carries every
    variant plus set-level warnings (a variant shortfall and any parity drift
    across siblings). A partial set (some variants unusable) still returns 200 —
    the caller decides whether the usable count is enough; only a set with *no*
    usable variant is a 422."""
    if req.language not in LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported language {req.language!r}; expected one of {sorted(LANGUAGES)}.",
        )
    result = draft_question_set(
        req.brief,
        language=req.language,
        count=req.count,
        difficulty=req.difficulty,
        target_complexity=req.target_complexity,
    )
    if result.engine == OFFLINE_ENGINE:
        raise HTTPException(
            status_code=503,
            detail="drafting requires a live model (set ANTHROPIC_API_KEY on the worker).",
        )
    payload = draft_set_to_dict(result)
    if not result.questions:
        # Every variant failed — surface the per-variant warnings, don't 200.
        raise HTTPException(status_code=422, detail=payload)
    return payload


@app.post(
    "/report",
    dependencies=[Depends(_require_token), Depends(_require_signature)],
)
def report(req: ReportRequest) -> Response:
    """Render a stored assessment result to a PDF and return the bytes.

    Stateless, like everything else here: the platform owns storage and supplies
    the serialized `result` it kept plus the two things that payload omits — the
    full `question` and the candidate `code`. We reconstruct the rich result and
    hand it to the same `build_report_pdf` the CLI/email path uses. No LLM, no code
    execution, so no rate-limit bucket — just the shared-secret + signature gates.
    """
    try:
        question, _warnings = question_from_dict(req.question, degrade_authoring=True)
        result = result_from_dict(req.result, question=question, source=req.code)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"malformed report payload: {exc}") from exc
    with tempfile.TemporaryDirectory() as tmp:
        out = build_report_pdf(result, Path(tmp) / "report.pdf", candidate=req.candidate)
        pdf_bytes = out.read_bytes()
    filename = f"{req.candidate}-assessment.pdf".replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _require_language(language: str) -> None:
    if language not in LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported language {language!r}; expected one of {sorted(LANGUAGES)}.",
        )


@app.post(
    "/run",
    dependencies=[
        Depends(_require_token),
        Depends(_require_signature),
        Depends(_rate_limit("run")),
    ],
)
def run_code(req: RunRequest) -> dict:
    """Execute code once against caller-supplied stdin and return what it printed.

    Synchronous and stateless: this is the candidate's "Run" button, not grading.
    Nothing is compared, no verdict is produced, no LLM is called and no job is
    recorded. Runs through the same sandboxing/limits as a graded execution.
    """
    _require_language(req.language)
    result = run_once(req.code, req.language, req.stdin, time_limit_s=req.time_limit_s)
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_s": round(result.duration_s, 3),
        "timed_out": result.timed_out,
        "compile_error": result.compile_error,
        "infra_error": result.infra_error,
    }


@app.post(
    "/run/tests",
    dependencies=[
        Depends(_require_token),
        Depends(_require_signature),
        Depends(_rate_limit("run")),
    ],
)
def run_tests(req: RunTestsRequest) -> dict:
    """Run a submission against the question's tests and report pass/fail only.

    The candidate-facing rehearsal of `POST /assessments`: synchronous, no LLM
    judge, no verdict, nothing stored.

    Deliberately returns **no** input/expected/actual — only each case's status.
    The platform redacts too, but the answer key never crossing the wire on this
    path means a bug there can't leak it. The graded path (`/assessments`) still
    returns full detail, which is what the interviewer's report card reads.
    """
    _require_language(req.language)
    # Candidate-facing rehearsal of the grade path — degrade authoring-shape
    # invariants to warnings (same as /assessments) so a pre-floor question runs
    # instead of 400ing the candidate.
    try:
        question, warnings = question_from_dict(req.question, degrade_authoring=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid question: {exc}") from None

    report = run_submission(
        req.code, req.language, question.test_cases, time_limit_s=question.time_limit_s
    )
    return {
        "compile_error": report.compile_error,
        "infra_error": report.infra_error,
        "test_cases": [
            {
                "name": o.name,
                "category": o.category,
                "status": "PASS" if o.passed else ("TLE" if o.timed_out else "FAIL"),
                "duration_s": round(o.duration_s, 3),
            }
            for o in report.outcomes
        ],
        "warnings": warnings,
    }


@app.post(
    "/assessments",
    status_code=202,
    dependencies=[
        Depends(_require_token),
        Depends(_require_signature),
        Depends(_rate_limit("assessments")),
    ],
)
def create_assessment(req: AssessmentRequest, background: BackgroundTasks) -> dict:
    """Accept an assessment job; run it in the background, deliver via callback/email."""
    if req.language not in LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported language {req.language!r}; expected one of {sorted(LANGUAGES)}.",
        )
    # Validate the inline question up front so a malformed one is a synchronous 400,
    # not a background failure the caller never sees. This is the grade/intake path:
    # authoring-shape invariants (case-count floor, required performance case) degrade
    # to warnings carried in the result rather than 400 the candidate for a question
    # shape only the interviewer can fix. Structural problems still 400.
    try:
        question, warnings = question_from_dict(req.question, degrade_authoring=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid question: {exc}") from None

    if req.callback_url:
        _validate_callback_url(req.callback_url)

    job_id = req.job_id or uuid.uuid4().hex
    # A caller-minted id lets the platform retry a trigger whose 202 it lost
    # without grading twice: a job still in flight is just acknowledged again. A
    # finished one IS re-run — the platform only re-sends a job it never heard
    # back about, or an interviewer asked for a fresh grade. Checked right before
    # the record write so the check-then-insert window is a few bytecodes.
    if req.job_id and _JOBS.get(job_id, {}).get("status") == "accepted":
        logger.info("job %s re-sent while in flight; acknowledged again, not re-run", job_id)
        return {"job_id": job_id, "status": "accepted"}
    _record_job(job_id, {"status": "accepted", "result": None, "error": None})
    if req.callback_url:
        _PENDING_CALLBACKS[job_id] = req.callback_url
    logger.info(
        "job %s accepted: language=%s question=%s adversarial=%s callback=%s email=%s",
        job_id,
        req.language,
        question.id,
        req.adversarial,
        bool(req.callback_url),
        bool(req.email_to),
    )
    background.add_task(_run_job, job_id, req, question, warnings)
    return {"job_id": job_id, "status": "accepted"}


@app.get("/assessments/{job_id}", dependencies=[Depends(_require_token)])
def get_assessment(job_id: str) -> dict:
    """Poll a job's status/result (fallback for callers not using a callback).

    Authenticated like every other route: the result carries each test case's
    input/expected/actual — the answer key that `/run/tests` deliberately never
    returns. An unguessable job_id is not an access control.
    """
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job with id {job_id!r}.")
    return {"job_id": job_id, **job}


def _record_job(job_id: str, state: dict[str, Any]) -> None:
    """Write job state, keeping the registry bounded.

    Eviction happens on every write, not just on create: a job that finishes after
    being evicted would otherwise re-insert itself and push the map past its cap.
    """
    _JOBS[job_id] = state
    _JOBS.move_to_end(job_id)
    while len(_JOBS) > _MAX_JOBS:
        _JOBS.popitem(last=False)  # evict oldest


def _run_job(job_id: str, req: AssessmentRequest, question, warnings: list[str]) -> None:
    """Background worker: assess, then deliver the result via callback and/or email."""
    started = time.perf_counter()
    try:
        result = assess(
            req.code, req.language, question, adversarial=req.adversarial, warnings=warnings
        )
        payload = result_to_dict(result)
        payload["candidate"] = req.candidate
        payload["job_id"] = job_id
        if req.email_to:
            payload["email"] = _email_report(result, req.candidate, req.email_to)
        _record_job(job_id, {"status": "done", "result": payload, "error": None})
        logger.info(
            "job %s done in %.1fs: verdict=%s score=%.0f%% quality_engine=%s",
            job_id,
            time.perf_counter() - started,
            result.verdict,
            result.score_pct,
            result.quality_engine,
        )
    except Exception as exc:  # keep the worker alive; record the failure for polling
        _record_job(job_id, {"status": "error", "result": None, "error": str(exc)})
        payload = {"job_id": job_id, "status": "error", "error": str(exc)}
        logger.exception("job %s failed after %.1fs", job_id, time.perf_counter() - started)

    if req.callback_url:
        # Claim delivery before posting so the shutdown flush can't also report
        # this job (it only flushes what is still registered).
        _PENDING_CALLBACKS.pop(job_id, None)
        _post_callback(job_id, req.callback_url, payload)


# The callback is the only *durable* delivery path (`_JOBS` is in-memory, evicts,
# and dies with the process), so a transient blip losing it loses the assessment
# outright. Retry with a short backoff, and log loudly when we finally give up —
# silence was the real defect here, not the dropped request.
_CALLBACK_ATTEMPTS = int(os.environ.get("ASSESS_CALLBACK_ATTEMPTS", "4"))
_CALLBACK_BACKOFF_S = float(os.environ.get("ASSESS_CALLBACK_BACKOFF_S", "1.0"))


def _post_callback(
    job_id: str, url: str, payload: dict, *, attempts: int | None = None, timeout: float = 10.0
) -> None:
    """POST the result to the platform's callback URL, retrying transient failures.

    `attempts` and `timeout` default to the normal delivery budget; the shutdown
    flush passes a one-shot, short-timeout budget because it has seconds, not
    minutes, before the process is killed.
    """
    tries = max(1, _CALLBACK_ATTEMPTS if attempts is None else attempts)
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("CALLBACK_TOKEN")
    if token:
        headers[_AUTH_HEADER] = token
    # Serialize once so we sign the exact bytes we send. Signs the callback body
    # with CALLBACK_SIGNING_SECRET when set, so the platform can verify it's us and
    # the result wasn't altered (mirrors the inbound _require_signature check).
    body = json.dumps(payload).encode()
    signing_secret = os.environ.get("CALLBACK_SIGNING_SECRET")
    if signing_secret:
        headers[SIGNATURE_HEADER] = sign(signing_secret, body)

    for attempt in range(1, tries + 1):
        try:
            response = httpx.post(url, content=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            reason: str = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code < 400:
                logger.info("job %s callback delivered (attempt %d)", job_id, attempt)
                return
            # 4xx is the platform rejecting the payload — retrying sends the same
            # bytes to the same endpoint, so don't. 5xx may be transient.
            if response.status_code < 500:
                logger.error(
                    "job %s callback rejected with %d — not retrying: %s",
                    job_id,
                    response.status_code,
                    response.text[:200],
                )
                return
            reason = f"HTTP {response.status_code}"

        if attempt < tries:
            delay = _CALLBACK_BACKOFF_S * (2 ** (attempt - 1))
            logger.warning(
                "job %s callback attempt %d/%d failed (%s); retrying in %.1fs",
                job_id,
                attempt,
                tries,
                reason,
                delay,
            )
            time.sleep(delay)
        else:
            # Exhausting the normal budget loses a result (ERROR). The one-shot
            # shutdown budget has the platform's reaper behind it (WARNING).
            logger.log(
                logging.ERROR if attempts is None else logging.WARNING,
                "job %s callback FAILED after %d attempt(s) (%s)%s",
                job_id,
                tries,
                reason,
                " — result is lost unless polled before eviction"
                if attempts is None
                else "; the platform's reaper will re-trigger the job",
            )


# Graceful stop. uvicorn waits ASSESS_SHUTDOWN_GRACE_S for in-flight jobs, then
# the lifespan hook flushes error callbacks for the rest. The whole sequence must
# fit inside the orchestrator's SIGTERM->SIGKILL grace (Docker's default is 10s),
# so the flush gives each callback ONE attempt with a short timeout, in parallel.
_SHUTDOWN_GRACE_S = int(os.environ.get("ASSESS_SHUTDOWN_GRACE_S", "5"))
_SHUTDOWN_CALLBACK_TIMEOUT_S = float(os.environ.get("ASSESS_SHUTDOWN_CALLBACK_TIMEOUT_S", "2.0"))


def _flush_pending_callbacks() -> int:
    """Error-callback every accepted job that hasn't delivered; return how many.

    Runs from the lifespan shutdown hook, after uvicorn's graceful wait. The
    payload is the worker's ordinary exception-path envelope — the platform
    already reads "no verdict + error" as infrastructure rather than the
    candidate, and re-queues the submission. Entries are popped before posting
    (atomic under the GIL), so a job finishing concurrently in its worker thread
    is reported once; one that finishes after the flush still posts its real
    result, which the platform accepts (a grade beats a re-queue).
    """
    pending: list[tuple[str, str]] = []
    while _PENDING_CALLBACKS:
        pending.append(_PENDING_CALLBACKS.popitem())
    if not pending:
        return 0
    logger.warning(
        "shutting down with %d job(s) still running; sending error callbacks so the "
        "platform re-queues them",
        len(pending),
    )

    def _notify(item: tuple[str, str]) -> None:
        job_id, url = item
        payload = {
            "job_id": job_id,
            "status": "error",
            "error": "agent worker shut down before the job completed",
        }
        _post_callback(job_id, url, payload, attempts=1, timeout=_SHUTDOWN_CALLBACK_TIMEOUT_S)

    with ThreadPoolExecutor(max_workers=min(8, len(pending))) as pool:
        list(pool.map(_notify, pending))
    return len(pending)


def _email_report(result, candidate: str, recipient: str) -> dict:
    """Render the PDF and email it; report the outcome without failing the job."""
    from .mailer import send_report
    from .report import build_report_pdf

    fd, tmp = tempfile.mkstemp(prefix="assess_", suffix=".pdf")
    os.close(fd)
    pdf_path = Path(tmp)
    try:
        build_report_pdf(result, pdf_path, candidate=candidate)
        send_report(
            pdf_path,
            candidate=candidate,
            verdict=result.verdict,
            score_pct=result.score_pct,
            recipient=recipient,
        )
        logger.info("emailed report for %s to %s", candidate, recipient)
        return {"emailed": True, "recipient": recipient, "error": None}
    except RuntimeError as exc:
        # `mailer.send_report` normalises every send failure to RuntimeError, so a
        # bad SMTP day is reported here and the assessment is still delivered.
        logger.warning("emailing report for %s to %s failed: %s", candidate, recipient, exc)
        return {"emailed": False, "recipient": recipient, "error": str(exc)}
    finally:
        pdf_path.unlink(missing_ok=True)


def main() -> None:
    """Entry point for `assess-api` — runs uvicorn."""
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("ASSESS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    host = os.environ.get("ASSESS_API_HOST", "127.0.0.1")
    port = int(os.environ.get("ASSESS_API_PORT", "8000"))
    # Bounded graceful stop: wait this long for in-flight grades, then the
    # lifespan hook error-callbacks the rest (see `_flush_pending_callbacks`).
    uvicorn.run(app, host=host, port=port, timeout_graceful_shutdown=_SHUTDOWN_GRACE_S)
