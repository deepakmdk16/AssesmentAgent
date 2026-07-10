import sys


def max_value(capacity, items):
    dp = [0] * (capacity + 1)
    for weight, value in items:
        for c in range(capacity, weight - 1, -1):
            if dp[c - weight] + value > dp[c]:
                dp[c] = dp[c - weight] + value
    return dp[capacity]


def read_input(stream):
    data = stream.read().split()
    n, capacity = int(data[0]), int(data[1])
    nums = data[2:]
    items = [(int(nums[2 * i]), int(nums[2 * i + 1])) for i in range(n)]
    return capacity, items


def main():
    capacity, items = read_input(sys.stdin)
    print(max_value(capacity, items))


if __name__ == "__main__":
    main()
