import sys

# Correct but exponential O(2^N): explores every subset, so it TLEs the
# performance case (N=200) even though it passes the small correctness cases.


def main():
    data = sys.stdin.read().split()
    n, capacity = int(data[0]), int(data[1])
    nums = data[2:]
    items = [(int(nums[2 * i]), int(nums[2 * i + 1])) for i in range(n)]

    def best(i, remaining):
        if i == n:
            return 0
        skip = best(i + 1, remaining)
        weight, value = items[i]
        if weight <= remaining:
            return max(skip, value + best(i + 1, remaining - weight))
        return skip

    print(best(0, capacity))


if __name__ == "__main__":
    main()
