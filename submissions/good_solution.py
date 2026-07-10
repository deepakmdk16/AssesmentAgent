import sys


def max_subarray_sum(nums: list[int]) -> int:
    best = current = nums[0]
    for x in nums[1:]:
        current = max(x, current + x)
        best = max(best, current)
    return best


def main() -> None:
    data = sys.stdin.read().split()
    n = int(data[0])
    nums = [int(x) for x in data[1 : 1 + n]]
    print(max_subarray_sum(nums))


if __name__ == "__main__":
    main()
