# Bug: resets the running sum to 0, so an all-negative array wrongly yields 0
# instead of the largest (least-negative) element. Passes the mixed/positive
# cases but fails the negative edge cases.
import sys

d = sys.stdin.read().split()
n = int(d[0])
a = [int(x) for x in d[1:1 + n]]

best = 0
cur = 0
for x in a:
    cur = max(0, cur + x)
    best = max(best, cur)
print(best)
