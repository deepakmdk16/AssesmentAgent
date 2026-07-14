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
not this map. Auth (a shared secret on the inbound call and the callback) is not
yet implemented and is required before exposing this publicly.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent import assess, result_to_dict
from .languages import LANGUAGES
from .loader import question_from_dict

app = FastAPI(
    title="Assessment Agent",
    description="Stateless intake worker: grade a candidate submission against a supplied question.",
    version="0.2.0",
)

# job_id -> {"status": "accepted"|"done"|"error", "result": dict|None, "error": str|None}
_JOBS: dict[str, dict[str, Any]] = {}


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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/assessments", status_code=202)
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

    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {"status": "accepted", "result": None, "error": None}
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
        result = assess(req.code, req.language, question)
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
    try:
        httpx.post(url, json=payload, timeout=10.0)
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
