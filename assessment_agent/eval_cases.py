"""A small fixed sample for calibrating / A/B-testing the judge.

Only the deterministic anchors assert a verdict (`expected_verdict`); the
borderline cases are report-only (`None`) so you can eyeball how a given model
scores them and tune PASS_QUALITY_THRESHOLD accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalCase:
    id: str
    language: str
    source: str
    expected_verdict: str | None  # "PASS" | "FAIL" | None (report-only)
    note: str


EVAL_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        id="strong",
        language="python",
        expected_verdict="PASS",
        note="validated, decomposed, idiomatic",
        source=(
            "import sys\n\n"
            "def read_numbers(stream):\n"
            "    n = int(stream.readline())\n"
            "    nums = [int(x) for x in stream.readline().split()]\n"
            "    if len(nums) != n:\n"
            "        raise ValueError('count mismatch')\n"
            "    return nums\n\n"
            "def main():\n"
            "    nums = read_numbers(sys.stdin)\n"
            "    print(max(nums), sum(nums))\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
    ),
    EvalCase(
        id="terse_correct",
        language="python",
        expected_verdict=None,
        note="correct + efficient but opaque names, no validation (sits near the bar)",
        source=(
            "import sys\n"
            "d=sys.stdin.read().split()\n"
            "n=int(d[0]); a=[int(x) for x in d[1:1+n]]\n"
            "print(max(a), sum(a))\n"
        ),
    ),
    EvalCase(
        id="inefficient_correct",
        language="python",
        expected_verdict=None,
        note="correct output but a pointless nested loop (efficiency should score low)",
        source=(
            "import sys\n\n"
            "def main():\n"
            "    data = sys.stdin.read().split()\n"
            "    n = int(data[0])\n"
            "    nums = [int(x) for x in data[1:1+n]]\n"
            "    biggest = nums[0]\n"
            "    for x in nums:\n"
            "        for _ in nums:\n"
            "            pass\n"
            "        if x > biggest:\n"
            "            biggest = x\n"
            "    total = 0\n"
            "    for x in nums:\n"
            "        total += x\n"
            "    print(biggest, total)\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
    ),
    EvalCase(
        id="buggy",
        language="python",
        expected_verdict="FAIL",
        note="prints only the sum — must fail the correctness gate",
        source=(
            "import sys\n"
            "d=sys.stdin.read().split()\n"
            "n=int(d[0]); a=[int(x) for x in d[1:1+n]]\n"
            "print(sum(a))\n"
        ),
    ),
    EvalCase(
        id="good_javascript",
        language="javascript",
        expected_verdict="PASS",
        note="cross-language check — correct, clear JS",
        source=(
            "const data = require('fs').readFileSync(0, 'utf8').split(/\\s+/).filter(Boolean);\n"
            "const n = parseInt(data[0], 10);\n"
            "const nums = data.slice(1, 1 + n).map(Number);\n"
            "console.log(`${Math.max(...nums)} ${nums.reduce((a, b) => a + b, 0)}`);\n"
        ),
    ),
)
