"""Load an interviewer-supplied question from an external file (Phase 2).

Phase 1 questions live as Python literals in questions.py. Phase 2 lets an
interviewer supply the whole question — prompt, constraints, worked example,
time limit, pass threshold, and the (stdin, expected) test cases including the
large performance case — as a JSON file at runtime, with no code change.

The interviewer is the oracle: they provide the expected output for every case
(that is what makes the inputs/outputs fully args-based). We therefore validate
the file's *shape* and the structural invariants every question must satisfy
(via `validate_question`), but we cannot differentially re-check their answers
the way the built-in questions' oracles are cross-checked in the test suite.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from .questions import Question, TestCase, validate_question


class _TestCaseSpec(BaseModel):
    name: str
    stdin: str
    expected: str
    category: str = "correctness"
    weight: float = 1.0


class _ExampleSpec(BaseModel):
    input: str
    output: str


class _QuestionSpec(BaseModel):
    id: str
    title: str
    prompt: str
    constraints: str
    test_cases: list[_TestCaseSpec] = Field(min_length=1)
    time_limit_s: float = 2.0
    pass_threshold: float = 0.9
    example: _ExampleSpec | None = None
    required_complexity: str | None = None


def question_from_dict(data: dict) -> Question:
    """Build a validated Question from a plain dict (the parsed JSON)."""
    spec = _QuestionSpec.model_validate(data)
    question = Question(
        id=spec.id,
        title=spec.title,
        prompt=spec.prompt,
        constraints=spec.constraints,
        test_cases=tuple(
            TestCase(t.name, t.stdin, t.expected, t.category, t.weight)
            for t in spec.test_cases
        ),
        time_limit_s=spec.time_limit_s,
        pass_threshold=spec.pass_threshold,
        example_input=spec.example.input if spec.example else None,
        example_output=spec.example.output if spec.example else None,
        required_complexity=spec.required_complexity,
    )
    # Shape is guaranteed by pydantic; this adds the semantic invariants (e.g. a
    # question must carry a performance case, weights > 0, threshold in (0, 1]).
    validate_question(question)
    return question


def load_question(path: str | Path) -> Question:
    """Load and validate an interviewer-supplied question JSON file."""
    return question_from_dict(json.loads(Path(path).read_text()))
