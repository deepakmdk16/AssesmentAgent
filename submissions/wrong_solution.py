# Bug: prints only the sum, not "<max> <sum>".
import sys

data = sys.stdin.read().split()
n = int(data[0])
nums = [int(x) for x in data[1:1 + n]]
print(sum(nums))
