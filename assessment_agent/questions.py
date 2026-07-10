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
    # Optional, args-based extras (mainly for Phase 2, interviewer-supplied
    # questions). A structured worked example, and an *advisory* required
    # complexity (e.g. "O(N log N)"). The performance gate stays empirical (the
    # TLE) — required_complexity only labels intent for the judge report and
    # evals; it never affects the score or verdict.
    example_input: str | None = None
    example_output: str | None = None
    required_complexity: str | None = None


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


def _knapsack(capacity: int, items: list[tuple[int, int]]) -> int:
    """Reference (oracle) 0/1 knapsack: max value with total weight <= capacity."""
    dp = [0] * (capacity + 1)
    for w, v in items:
        for c in range(capacity, w - 1, -1):
            if dp[c - w] + v > dp[c]:
                dp[c] = dp[c - w] + v
    return dp[capacity]


def _knapsack_case(name: str, capacity: int, items: list[tuple[int, int]],
                   category: str = "correctness", weight: float = 1.0) -> TestCase:
    """Build a case whose `expected` is labelled by the oracle, so the stdin and
    the expected answer can never drift apart."""
    stdin = f"{len(items)} {capacity}\n" + "".join(f"{w} {v}\n" for w, v in items)
    return TestCase(name, stdin, str(_knapsack(capacity, items)), category, weight)


def _knapsack_perf_case(name: str, n: int, capacity: int,
                        seed: int = 54321, weight: float = 6.0) -> TestCase:
    rng = random.Random(seed)
    items = [(rng.randint(1, 1000), rng.randint(1, 1000)) for _ in range(n)]
    return _knapsack_case(name, capacity, items, category="performance", weight=weight)


KNAPSACK_QUESTION = Question(
    id="knapsack_01",
    title="0/1 Knapsack",
    prompt=(
        "Read two integers N and W from the first line of standard input: the "
        "number of items and the knapsack's weight capacity. Then read N lines, "
        "each containing two integers w_i v_i — the weight and value of item i. "
        "Each item may be taken at most once (0/1). Print a single integer: the "
        "maximum total value of a subset of items whose total weight is at most "
        "W.\n\n"
        "Example:\n"
        "  Input:\n    3 5\n    4 5\n    1 1\n    3 4\n"
        "  Output:\n    5\n"
    ),
    constraints=(
        "1 <= N <= 200, 0 <= W <= 10000, and 1 <= w_i, v_i <= 1000. With N this "
        "large, an exponential O(2^N) brute force over all subsets will exceed "
        "the time limit — an O(N*W) dynamic-programming solution is required."
    ),
    test_cases=(
        _knapsack_case("classic", 5, [(4, 5), (1, 1), (3, 4)]),
        _knapsack_case("zero_capacity", 0, [(1, 5), (2, 8)]),
        _knapsack_case("none_fit", 2, [(5, 10), (6, 12)]),
        _knapsack_case("single_fits", 10, [(4, 7)]),
        _knapsack_case("take_all", 6, [(2, 3), (3, 4), (1, 2)]),
        _knapsack_perf_case("performance_large", 200, 10_000),
    ),
)


# Registry of the available questions, keyed by id. The CLI selects one with
# --question; the default preserves the original single-question behaviour.
QUESTIONS: dict[str, Question] = {
    q.id: q for q in (HARDCODED_QUESTION, KNAPSACK_QUESTION)
}


def validate_question(q: Question) -> None:
    """Structural invariants every Question must satisfy, independent of which
    problem it encodes. Shared by the generic test suite and the Phase 2 loader
    (which validates interviewer-supplied questions before grading). Raises
    ValueError on the first problem found; returns None when the question is
    well-formed.
    """
    if not q.id.strip():
        raise ValueError("question id must be non-empty")
    for field in ("title", "prompt", "constraints"):
        if not getattr(q, field).strip():
            raise ValueError(f"question {q.id!r}: {field} must be non-empty")
    if not q.test_cases:
        raise ValueError(f"question {q.id!r}: needs at least one test case")

    names = [t.name for t in q.test_cases]
    if len(names) != len(set(names)):
        raise ValueError(f"question {q.id!r}: test case names must be unique")

    for t in q.test_cases:
        if not t.name.strip():
            raise ValueError(f"question {q.id!r}: a test case has an empty name")
        if t.weight <= 0:
            raise ValueError(
                f"question {q.id!r}: test case {t.name!r} weight must be > 0 (got {t.weight})"
            )
        if t.category not in ("correctness", "performance"):
            raise ValueError(
                f"question {q.id!r}: test case {t.name!r} has invalid category {t.category!r}"
            )
        if t.expected == "":
            raise ValueError(
                f"question {q.id!r}: test case {t.name!r} has empty expected output"
            )

    if not any(t.category == "performance" for t in q.test_cases):
        raise ValueError(
            f"question {q.id!r}: needs at least one 'performance' test case "
            "(the constraint-sized TLE gate that catches too-slow solutions)"
        )
    if not (0.0 < q.pass_threshold <= 1.0):
        raise ValueError(
            f"question {q.id!r}: pass_threshold must be in (0, 1], got {q.pass_threshold}"
        )
    if q.time_limit_s <= 0:
        raise ValueError(
            f"question {q.id!r}: time_limit_s must be > 0, got {q.time_limit_s}"
        )
