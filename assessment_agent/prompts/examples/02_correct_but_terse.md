## Example — correct but terse

PROBLEM: read N then N integers; print the max and the sum.

TEST RESULTS: 4/4 passed.

SUBMISSION (python):
```python
import sys
d = sys.stdin.read().split()
n = int(d[0])
a = [int(x) for x in d[1:1+n]]
print(max(a), sum(a))
```

IDEAL ASSESSMENT:
- robustness 2 — no validation; single-letter names hide intent; assumes
  well-formed input entirely.
- readability 2 — `d`, `n`, `a` are opaque; correct but hard to scan.
- efficiency 5 — one pass with built-ins, nothing wasted.
- design 3 — flat script with no decomposition, acceptable at this size.
- overall 3.0
- strengths: correct and efficient; uses built-ins well.
- weaknesses: single-letter names hurt readability; no input validation.
- summary: Functionally correct and efficient, but written as a terse flat
  script with opaque names and no input validation. Passes everything; would
  need naming and a validation pass before it read as production code.
```
Note: this sits right at the pass bar — correct and efficient, but weak on the
"how it's written" dimensions.
```
