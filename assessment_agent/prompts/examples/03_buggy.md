## Example — buggy submission

PROBLEM: read N then N integers; print the max and the sum.

TEST RESULTS: 0/4 passed (printed only the sum, not "<max> <sum>").

SUBMISSION (python):
```python
import sys
d = sys.stdin.read().split()
n = int(d[0])
a = [int(x) for x in d[1:1+n]]
print(sum(a))
```

IDEAL ASSESSMENT:
- robustness 2 — no validation, and misreads the required output.
- readability 3 — short and scannable despite terse names.
- efficiency 4 — single pass, though it omits the max entirely.
- design 2 — misses half the required output; the approach is incomplete.
- overall 2.0
- strengths: correctly parses N and the list.
- weaknesses: prints only the sum, omitting the maximum the problem requires;
  no input validation.
- summary: The parsing is fine but the solution ignores half the specification
  — it prints the sum only, so every test fails. This is a correctness defect,
  not just a style issue.

Note: the tests are the ground truth here. Even though the code is short and
readable, it does not solve the stated problem, so the quality scores reflect
an incomplete solution — and the verdict will FAIL on the correctness gate
regardless of these scores.
