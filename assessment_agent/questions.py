"""Coding questions and their test cases.

Phase 1: a single hard-coded question (below).
Phase 2: an interviewer supplies a Question (prompt + test cases) at runtime;
nothing else in the pipeline changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TestCase:
    name: str
    stdin: str
    expected: str


@dataclass(frozen=True)
class Question:
    id: str
    title: str
    prompt: str
    test_cases: tuple[TestCase, ...]


HARDCODED_QUESTION = Question(
    id="max_and_sum",
    title="Maximum and Sum",
    prompt=(
        "Read an integer N from the first line of standard input, then read N "
        "space-separated integers from the second line. Print two integers "
        "separated by a single space: the maximum value, followed by the sum "
        "of all N values.\n\n"
        "Example:\n"
        "  Input:\n    3\n    1 2 3\n"
        "  Output:\n    3 6\n"
    ),
    test_cases=(
        TestCase("basic", "3\n1 2 3\n", "3 6"),
        TestCase("single_element", "1\n5\n", "5 5"),
        TestCase("negatives", "4\n-1 -2 -3 -4\n", "-1 -10"),
        TestCase("larger_values", "5\n10 20 30 40 50\n", "50 150"),
    ),
)
