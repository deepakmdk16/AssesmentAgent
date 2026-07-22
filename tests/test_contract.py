"""Agent side of the agent -> platform callback contract.

Asserts the payload the agent POSTs to the platform's /assessments/callback
conforms to contract/callback_contract.py — the narrow envelope the platform
reads by name. If result_to_dict() ever drops or renames verdict/score_pct/reason,
or emits a verdict outside the enum, this fails here (offline, free) before it can
500 the platform in production. Mirror of the platform repo's tests/test_contract.py.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from assessment_agent.agent import assess, result_to_dict
from assessment_agent.eval_cases import EVAL_CASES

# Load the byte-identical contract module by path (it lives at repo root, outside
# the package, because it is a cross-repo artifact, not agent internals).
_CONTRACT = pathlib.Path(__file__).resolve().parents[1] / "contract" / "callback_contract.py"
_spec = importlib.util.spec_from_file_location("callback_contract", _CONTRACT)
callback_contract = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(callback_contract)

STRONG = next(c for c in EVAL_CASES if c.id == "strong").source


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch):
    # Verdict is execution-based, not LLM-based, so this runs offline and free.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _callback_payload(code: str, language: str = "python") -> dict:
    """Reproduce exactly what api._run_job builds and POSTs to callback_url."""
    result = assess(code, language)
    payload = result_to_dict(result)
    payload["candidate"] = "test-candidate"
    payload["job_id"] = "job-123"
    return payload


def test_passing_assessment_callback_conforms():
    payload = _callback_payload(STRONG)
    assert payload["verdict"] == "PASS"
    assert callback_contract.validate_callback(payload) == []


def test_failing_assessment_callback_conforms():
    payload = _callback_payload("print(0)\n")
    assert payload["verdict"] == "FAIL"
    assert callback_contract.validate_callback(payload) == []


def test_worker_error_payload_conforms():
    # The exception path in api._run_job emits a reduced envelope; job_id is the
    # only hard requirement and the platform tolerates the rest.
    payload = {"job_id": "job-err", "status": "error", "error": "boom"}
    assert callback_contract.validate_callback(payload) == []
