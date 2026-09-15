"""The agent -> platform callback contract: the ONE cross-repo wire boundary.

The agent POSTs its result to the platform's ``POST /assessments/callback``. The
platform stores the whole body verbatim in ``full_result``, but it *reads* a
narrow envelope out of it to drive its own state — the verdict, the score, the
reason, and the error signals (see the platform's ``assessments_callback``).
Those fields, and only those, are the contract; everything else in the payload is
opaque to the platform and may change freely.

SIZE is the exception to "freely": the platform 413s a body over its own
``MAX_BODY_BYTES`` and the agent does not retry a 4xx, so an oversized callback
does not degrade the grade, it loses it (R2-001 — a 9.6 MB performance input did
exactly that, twice). Two bounds hold it, on opposite sides, and neither is
sufficient alone: the platform bounds the test cases it will STORE
(``schemas.py::MAX_QUESTION_CASES_BYTES``), and the agent bounds every free-text
value it ECHOES back (``agent.py::PAYLOAD_EXCERPT_BYTES``) — the second because
a candidate's stdout, and the compiler's stderr, are bounded only by the agent's
64 MB output cap, which no choice of ``MAX_BODY_BYTES`` could absorb. Note that
the second bound covers ``compile_error`` and each case's ``error`` as well as
its I/O: those come from the toolchain, so no bound on the QUESTION reaches them. The platform's
``tests/test_agent_contract_parity.py`` multiplies these out and fails if raising
one without the other would put the worst body over the cap.

This module is mirrored BYTE-FOR-BYTE in both repos (agent + platform) and kept
identical by ``scripts/checkpoints.sh`` — a push fails if the two copies diverge,
exactly like ``signing.py``. Edit it in one repo and the gate stops you until you
mirror it. Keep it stdlib-only so both repos import it with no new dependency.

Two payload shapes are valid, matching the agent's two delivery paths:
  * a *graded* result — carries verdict / score_pct / reason (the full envelope);
  * an *error* payload from the worker's exception path (or its shutdown flush) —
    carries job_id + error only, which the platform reads as "the job never
    completed": it re-queues the submission for its reaper while trigger attempts
    remain, and stores an ERROR result only once they are exhausted.
``job_id`` is the single field required by both.
"""

from __future__ import annotations

from typing import Any

# The only verdicts the platform knows how to store and display. Must stay in
# lockstep with the agent's ``constants.Verdict`` Literal.
VERDICTS: tuple[str, ...] = ("PASS", "FAIL", "ERROR")

# Graded-result fields the platform reads by name -> accepted python type(s).
GRADED_REQUIRED: dict[str, type | tuple[type, ...]] = {
    "verdict": str,
    "score_pct": (int, float),
    "reason": str,
}


def _is_error_payload(payload: dict[str, Any]) -> bool:
    """The reduced envelope the agent's exception path sends (job_id + error)."""
    return bool(payload.get("error")) or payload.get("status") == "error"


def validate_callback(payload: Any) -> list[str]:
    """Return a list of contract violations; an empty list means it conforms.

    Only the cross-repo envelope is checked, never the opaque ``full_result``
    detail. A graded payload must carry the full envelope with a known verdict; an
    error payload only needs a ``job_id``.
    """
    if not isinstance(payload, dict):
        return ["payload is not a JSON object"]

    errors: list[str] = []
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        errors.append("missing or empty required field: 'job_id'")

    if _is_error_payload(payload):
        return errors  # error path: job_id is the only hard requirement

    for key, types in GRADED_REQUIRED.items():
        if key not in payload:
            errors.append(f"missing required field: '{key}'")
        elif not isinstance(payload[key], types):
            errors.append(
                f"field '{key}' has wrong type: {type(payload[key]).__name__}"
            )
    verdict = payload.get("verdict")
    if verdict is not None and verdict not in VERDICTS:
        errors.append(f"verdict {verdict!r} not in {VERDICTS}")
    return errors
