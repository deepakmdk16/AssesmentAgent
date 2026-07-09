## Example — strong submission

PROBLEM: read N then N integers; print the max and the sum.

TEST RESULTS: 4/4 passed.

SUBMISSION (python):
```python
import sys

def parse(stream):
    n = int(stream.readline())
    nums = [int(x) for x in stream.readline().split()]
    if len(nums) != n:
        raise ValueError(f"expected {n} numbers, got {len(nums)}")
    return nums

def main():
    nums = parse(sys.stdin)
    print(max(nums), sum(nums))

if __name__ == "__main__":
    main()
```

IDEAL ASSESSMENT:
- robustness 4 — validates the count against N and raises a clear error; only
  gap is an empty list would still throw on `max`.
- readability 5 — small named functions, clear intent, idiomatic.
- efficiency 5 — single pass via built-ins, no wasted work.
- design 4 — parsing separated from output; clean structure.
- overall 4.5
- strengths: input validated against N; parsing cleanly separated from logic.
- weaknesses: no explicit guard for an empty list.
- summary: A clean, idiomatic solution that separates parsing from output and
  validates its input against N. Solid across the board; the only nit is it
  would throw on an empty list rather than handling it gracefully.
