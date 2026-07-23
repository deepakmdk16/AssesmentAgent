"""Phase 2 loader: interviewer-supplied question files parse, validate, and grade."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from assessment_agent.agent import assess
from assessment_agent.loader import load_question, question_from_dict

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sum_of_n.json"

SUM_SRC = (
    "import sys\n"
    "data = sys.stdin.read().split()\n"
    "n = int(data[0])\n"
    "print(sum(int(x) for x in data[1:1 + n]))\n"
)


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_example_file_loads_with_structured_fields():
    q = load_question(EXAMPLE)
    assert q.id == "sum_of_n"
    assert q.required_complexity == "O(N)"
    assert q.example_input == "2\n3 4\n"
    assert q.example_output == "7"
    assert any(t.category == "performance" for t in q.test_cases)


def test_loaded_question_grades_end_to_end():
    q = load_question(EXAMPLE)
    result = assess(SUM_SRC, "python", q)
    assert result.verdict == "PASS"
    assert result.score_pct == 100.0


def _minimal_spec() -> dict:
    return {
        "id": "q",
        "title": "Q",
        "prompt": "p",
        "constraints": "c",
        "test_cases": [
            {"name": "ok1", "stdin": "1\n", "expected": "1"},
            {"name": "ok2", "stdin": "2\n", "expected": "2"},
            {"name": "ok3", "stdin": "3\n", "expected": "3"},
            {"name": "ok4", "stdin": "4\n", "expected": "4"},
            {
                "name": "perf",
                "stdin": "1\n",
                "expected": "1",
                "category": "performance",
                "weight": 6.0,
            },
        ],
    }


def test_optional_fields_default_to_none():
    q = question_from_dict(_minimal_spec())
    assert q.example_input is None and q.required_complexity is None


def test_missing_performance_case_is_rejected():
    spec = _minimal_spec()
    spec["test_cases"] = [spec["test_cases"][0]]  # drop the performance case
    with pytest.raises(ValueError, match="performance"):
        question_from_dict(spec)


def test_bad_schema_is_rejected():
    spec = _minimal_spec()
    del spec["prompt"]  # required field
    with pytest.raises(ValidationError):
        question_from_dict(spec)


def test_unknown_field_is_rejected():
    # A typo'd key must be a loud error, not a silently-dropped field.
    spec = _minimal_spec()
    spec["threshold"] = 0.5  # typo for pass_threshold
    with pytest.raises(ValidationError):
        question_from_dict(spec)
    spec = _minimal_spec()
    spec["test_cases"][0]["stdout"] = "1"  # typo for expected
    with pytest.raises(ValidationError):
        question_from_dict(spec)
