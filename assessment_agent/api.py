"""HTTP intake for the Assessment Agent (Phase 2).

This service is a **stateless assessment worker**. An external platform triggers
a job by POSTing the full question (prompt, constraints, I/O cases, example) plus
the candidate's submitted code — the platform owns question storage; the agent
keeps none. The agent runs the same pipeline the CLI uses (`agent.assess`:
deterministic execution + scoring, with a non-gating Sonnet quality summary) and
delivers the result asynchronously:

    POST /assessments            -> 202 {job_id, status: "accepted"}
      (the work runs in the background; when done the agent POSTs the full result
       to `callback_url` and/or emails the PDF to `email_to`)
    GET  /assessments/{job_id}   -> {status, result?}   polling fallback
    GET  /health

Run it with:  `uv run assess-api`  (or `uvicorn assessment_agent.api:app`).

The in-memory `_JOBS` registry is transient run-state for the polling fallback,
**not** a datastore — a multi-instance deployment should rely on `callback_url`,
not this map. Auth is a shared-secret bearer token in the `X-Assess-Token` header
(env `ASSESS_API_TOKEN` for inbound, `CALLBACK_TOKEN` for the outbound callback),
enforced only when the env var is set — set both in production.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import tempfile
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .agent import assess, result_to_dict
from .authoring import draft_question, draft_to_dict
from .constants import OFFLINE_ENGINE
from .languages import LANGUAGES
from .loader import question_from_dict

app = FastAPI(
    title="Assessment Agent",
    description="Stateless intake worker: grade a candidate submission against a supplied question.",
    version="0.2.0",
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
    expected = os.environ.get("ASSESS_API_TOKEN")
    # Constant-time compare so the shared secret can't be recovered by timing the
    # response. `compare_digest` needs two strings, so coalesce a missing header.
    if expected and not secrets.compare_digest(x_assess_token or "", expected):
        raise HTTPException(status_code=401, detail=f"invalid or missing {_AUTH_HEADER}.")


# job_id -> {"status": "accepted"|"done"|"error", "result": dict|None, "error": str|None}
# This is the polling fallback's transient run-state, not a datastore, so it is
# bounded: once it holds _MAX_JOBS entries, inserting a new one evicts the oldest
# (FIFO). Otherwise a long-lived worker would accumulate every job forever.
# Callers that need durable results should use `callback_url`, not this map.
_MAX_JOBS = int(os.environ.get("ASSESS_MAX_JOBS", "1000"))
_JOBS: OrderedDict[str, dict[str, Any]] = OrderedDict()


class AssessmentRequest(BaseModel):
    question: dict = Field(
        description="The full question the candidate answered (same shape as a "
        "question JSON: title, prompt, constraints, test_cases, example, ...)."
    )
    code: str = Field(min_length=1, description="The candidate's submitted source code.")
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


class DraftRequest(BaseModel):
    brief: str = Field(min_length=1, description="Natural-language description of the problem.")
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/questions/draft", dependencies=[Depends(_require_token)])
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


@app.post("/assessments", status_code=202, dependencies=[Depends(_require_token)])
def create_assessment(req: AssessmentRequest, background: BackgroundTasks) -> dict:
    """Accept an assessment job; run it in the background, deliver via callback/email."""
    if req.language not in LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported language {req.language!r}; expected one of {sorted(LANGUAGES)}.",
        )
    # Validate the inline question up front so a malformed one is a synchronous 400,
    # not a background failure the caller never sees.
    try:
        question = question_from_dict(req.question)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid question: {exc}") from None

    if req.callback_url:
        _validate_callback_url(req.callback_url)

    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {"status": "accepted", "result": None, "error": None}
    while len(_JOBS) > _MAX_JOBS:
        _JOBS.popitem(last=False)  # evict oldest
    background.add_task(_run_job, job_id, req, question)
    return {"job_id": job_id, "status": "accepted"}


@app.get("/assessments/{job_id}")
def get_assessment(job_id: str) -> dict:
    """Poll a job's status/result (fallback for callers not using a callback)."""
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job with id {job_id!r}.")
    return {"job_id": job_id, **job}


def _run_job(job_id: str, req: AssessmentRequest, question) -> None:
    """Background worker: assess, then deliver the result via callback and/or email."""
    try:
        result = assess(req.code, req.language, question, adversarial=req.adversarial)
        payload = result_to_dict(result)
        payload["candidate"] = req.candidate
        payload["job_id"] = job_id
        if req.email_to:
            payload["email"] = _email_report(result, req.candidate, req.email_to)
        _JOBS[job_id] = {"status": "done", "result": payload, "error": None}
    except Exception as exc:  # keep the worker alive; record the failure for polling
        _JOBS[job_id] = {"status": "error", "result": None, "error": str(exc)}
        payload = {"job_id": job_id, "status": "error", "error": str(exc)}

    if req.callback_url:
        _post_callback(req.callback_url, payload)


def _post_callback(url: str, payload: dict) -> None:
    """POST the result to the platform's callback URL (best-effort)."""
    headers = {}
    token = os.environ.get("CALLBACK_TOKEN")
    if token:
        headers[_AUTH_HEADER] = token
    try:
        httpx.post(url, json=payload, headers=headers, timeout=10.0)
    except httpx.HTTPError:
        # A failed callback must not crash the worker; the result is still pollable.
        pass


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
        return {"emailed": True, "recipient": recipient, "error": None}
    except RuntimeError as exc:
        return {"emailed": False, "recipient": recipient, "error": str(exc)}
    finally:
        pdf_path.unlink(missing_ok=True)


def main() -> None:
    """Entry point for `assess-api` — runs uvicorn."""
    import uvicorn

    host = os.environ.get("ASSESS_API_HOST", "127.0.0.1")
    port = int(os.environ.get("ASSESS_API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
