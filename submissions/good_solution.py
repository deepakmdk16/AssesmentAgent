import sys


def main() -> None:
    data = sys.stdin.read().split()
    n = int(data[0])
    nums = [int(x) for x in data[1 : 1 + n]]
    print(max(nums), sum(nums))


if __name__ == "__main__":
    main()
