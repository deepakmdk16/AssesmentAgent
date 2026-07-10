# Correct but O(n^2): recomputes every subarray sum. Right answer on small
# inputs, but exceeds the time limit on the large constrained input (TLE).
import sys


def main() -> None:
    data = sys.stdin.read().split()
    n = int(data[0])
    nums = [int(x) for x in data[1 : 1 + n]]
    best = nums[0]
    for i in range(n):
        total = 0
        for j in range(i, n):
            total += nums[j]
            if total > best:
                best = total
    print(best)


if __name__ == "__main__":
    main()
