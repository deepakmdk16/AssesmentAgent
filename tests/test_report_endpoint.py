"""POST /report: reconstruct a stored result and render it to a PDF.

The platform keeps only the serialized result (`result_to_dict`), so the report
endpoint takes that back plus the two things the serialized form omits — the full
question and the candidate source — reconstructs the rich `AssessmentResult` via
`result_from_dict`, and renders it. All offline: `assess` computes a real verdict
from execution alone, no LLM or key needed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from assessment_agent.agent import assess, result_from_dict, result_to_dict
from assessment_agent.api import app
from assessment_agent.eval_cases import EVAL_CASES
from assessment_agent.questions import Question

STRONG = next(c for c in EVAL_CASES if c.id == "strong").source


def _question_dict(q: Question) -> dict:
    """Serialize a Question to the JSON shape `question_from_dict` accepts."""
    example = None
    if q.example_input is not None or q.example_output is not None:
        example = {"input": q.example_input or "", "output": q.example_output or ""}
    return {
        "id": q.id,
        "title": q.title,
        "prompt": q.prompt,
        "constraints": q.constraints,
        "test_cases": [
            {
                "name": t.name,
                "stdin": t.stdin,
                "expected": t.expected,
                "category": t.category,
                "weight": t.weight,
            }
            for t in q.test_cases
        ],
        "time_limit_s": q.time_limit_s,
        "pass_threshold": q.pass_threshold,
        "example": example,
        "required_complexity": q.required_complexity,
    }


def _graded() -> tuple:
    result = assess(STRONG, "python")
    return result, result_to_dict(result)


def test_result_from_dict_preserves_report_fields():
    original, data = _graded()
    rebuilt = result_from_dict(data, question=original.question, source=original.source)
    redict = result_to_dict(rebuilt)
    # Everything the report renders must round-trip. Cost/usage is intentionally
    # dropped (not stored, not rendered), so we don't assert on judge_cost_usd.
    for key in (
        "verdict",
        "reason",
        "score_pct",
        "points_earned",
        "points_total",
        "pass_threshold_pct",
        "compile_error",
        "infra_error",
        "test_cases",
        "quality",
    ):
        assert redict[key] == data[key], key
    assert rebuilt.source == original.source
    assert rebuilt.question.id == original.question.id


def test_report_endpoint_returns_pdf():
    original, data = _graded()
    client = TestClient(app)
    resp = client.post(
        "/report",
        json={
            "result": data,
            "question": _question_dict(original.question),
            "code": original.source,
            "candidate": "Ada Lovelace",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"
    assert "Ada_Lovelace" in resp.headers["content-disposition"]


def test_report_endpoint_rejects_malformed_result():
    original, _ = _graded()
    client = TestClient(app)
    resp = client.post(
        "/report",
        json={
            "result": {"not": "a result"},
            "question": _question_dict(original.question),
            "code": "print(1)\n",
        },
    )
    assert resp.status_code == 400
    assert "malformed report payload" in resp.text
