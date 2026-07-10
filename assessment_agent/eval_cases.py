"""A small fixed sample for calibrating / A/B-testing the judge.

Verdicts are now score-based (weighted tests vs. the pass threshold), so every
case has a deterministic expected verdict independent of the quality model.
`expected_verdict` may still be `None` for a case you only want to eyeball.
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
        note="validated, decomposed, idiomatic Kadane",
        source=(
            "import sys\n\n"
            "def read_numbers(stream):\n"
            "    n = int(stream.readline())\n"
            "    nums = [int(x) for x in stream.readline().split()]\n"
            "    if len(nums) != n:\n"
            "        raise ValueError('count mismatch')\n"
            "    return nums\n\n"
            "def max_subarray_sum(nums):\n"
            "    best = current = nums[0]\n"
            "    for x in nums[1:]:\n"
            "        current = max(x, current + x)\n"
            "        best = max(best, current)\n"
            "    return best\n\n"
            "def main():\n"
            "    print(max_subarray_sum(read_numbers(sys.stdin)))\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
    ),
    EvalCase(
        id="terse_correct",
        language="python",
        expected_verdict="PASS",
        note="correct + fast Kadane; scores 100% (quality is reported, not gated)",
        source=(
            "import sys\n"
            "d=sys.stdin.read().split()\n"
            "n=int(d[0]); a=[int(x) for x in d[1:1+n]]\n"
            "b=c=a[0]\n"
            "for x in a[1:]:\n"
            "    c=max(x,c+x); b=max(b,c)\n"
            "print(b)\n"
        ),
    ),
    EvalCase(
        id="inefficient_correct",
        language="python",
        expected_verdict="FAIL",
        note="correct O(n^2) brute force — must TLE the performance case (too slow)",
        source=(
            "import sys\n\n"
            "def main():\n"
            "    data = sys.stdin.read().split()\n"
            "    n = int(data[0])\n"
            "    nums = [int(x) for x in data[1:1+n]]\n"
            "    best = nums[0]\n"
            "    for i in range(n):\n"
            "        total = 0\n"
            "        for j in range(i, n):\n"
            "            total += nums[j]\n"
            "            if total > best:\n"
            "                best = total\n"
            "    print(best)\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
    ),
    EvalCase(
        id="buggy",
        language="python",
        expected_verdict="FAIL",
        note="resets running sum to 0 — fails all-negative input (correctness gate)",
        source=(
            "import sys\n"
            "d=sys.stdin.read().split()\n"
            "n=int(d[0]); a=[int(x) for x in d[1:1+n]]\n"
            "best=cur=0\n"
            "for x in a:\n"
            "    cur=max(0, cur+x); best=max(best, cur)\n"
            "print(best)\n"
        ),
    ),
    EvalCase(
        id="good_javascript",
        language="javascript",
        expected_verdict="PASS",
        note="cross-language check — correct, clear JS Kadane",
        source=(
            "const data = require('fs').readFileSync(0, 'utf8').split(/\\s+/).filter(Boolean);\n"
            "const n = parseInt(data[0], 10);\n"
            "const nums = data.slice(1, 1 + n).map(Number);\n"
            "let best = nums[0], cur = nums[0];\n"
            "for (let i = 1; i < n; i++) {\n"
            "  cur = Math.max(nums[i], cur + nums[i]);\n"
            "  best = Math.max(best, cur);\n"
            "}\n"
            "console.log(best);\n"
        ),
    ),
)
