"""Coding questions and their test cases.

Phase 1: a single hard-coded question (below).
Phase 2: an interviewer supplies a Question (prompt + test cases) at runtime;
nothing else in the pipeline changes.

Test cases have a `category`:
- "correctness" — small, hand-written cases that check the answer.
- "performance" — a large, generated case sized to the problem's constraints so
  that a sub-optimal solution exceeds the time limit (a TLE, like CodeChef /
  Codeforces). This is what catches "correct but too slow" submissions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class TestCase:
    name: str
    stdin: str
    expected: str
    category: str = "correctness"  # "correctness" | "performance"
    # Points this case is worth. Larger inputs carry more weight, so passing the
    # large (performance) case matters more than the small correctness cases.
    weight: float = 1.0


@dataclass(frozen=True)
class Question:
    id: str
    title: str
    prompt: str
    constraints: str
    test_cases: tuple[TestCase, ...]
    # Base per-test time limit in seconds; scaled per language (see languages.py).
    time_limit_s: float = 2.0
    # Fraction of total weight the candidate must earn to PASS (0.9 = 90%).
    pass_threshold: float = 0.9


def _max_subarray(nums: list[int]) -> int:
    """Reference (oracle) solution used to label generated performance cases."""
    best = current = nums[0]
    for x in nums[1:]:
        current = max(x, current + x)
        best = max(best, current)
    return best


def _perf_case(name: str, n: int, seed: int = 12345, weight: float = 6.0) -> TestCase:
    rng = random.Random(seed)
    nums = [rng.randint(-10_000, 10_000) for _ in range(n)]
    stdin = f"{n}\n" + " ".join(map(str, nums)) + "\n"
    return TestCase(name, stdin, str(_max_subarray(nums)),
                    category="performance", weight=weight)


HARDCODED_QUESTION = Question(
    id="max_subarray_sum",
    title="Maximum Subarray Sum",
    prompt=(
        "Read an integer N from the first line of standard input, then read N "
        "space-separated integers from the second line. Print a single integer: "
        "the largest sum obtainable from any non-empty contiguous subarray.\n\n"
        "The array may contain negative numbers, and the subarray must be "
        "non-empty — so for an all-negative array the answer is the largest "
        "(least negative) element, not 0.\n\n"
        "Example:\n"
        "  Input:\n    9\n    -2 1 -3 4 -1 2 1 -5 4\n"
        "  Output:\n    6\n"
    ),
    constraints=(
        "1 <= N <= 100000, and each value fits in [-10^4, 10^4]. With N this "
        "large, an O(N^2) solution will exceed the time limit — an O(N) "
        "(Kadane's) or O(N log N) solution is required."
    ),
    test_cases=(
        TestCase("classic", "9\n-2 1 -3 4 -1 2 1 -5 4\n", "6"),
        TestCase("all_negative", "3\n-5 -2 -8\n", "-2"),
        TestCase("single_element", "1\n-7\n", "-7"),
        TestCase("all_positive", "4\n1 2 3 4\n", "10"),
        _perf_case("performance_large", 100_000),
    ),
)
