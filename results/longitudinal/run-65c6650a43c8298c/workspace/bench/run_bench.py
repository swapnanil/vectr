"""Local microbenchmark harness for swiftsort algorithms."""
import sys
import time

from swiftsort.merge import merge_sort
from swiftsort.radix import radix_sort
from swiftsort.tim import tim_sort

ALGOS = {"merge_sort": merge_sort, "radix_sort": radix_sort, "tim_sort": tim_sort}


def main(argv):
    name = argv[0] if argv else "radix_sort"
    fn = ALGOS[name]
    data = list(range(2000, 0, -1))
    start = time.perf_counter()
    fn(list(data))
    elapsed_ns = (time.perf_counter() - start) * 1e9
    print(f"{name}: {elapsed_ns:.0f} ns/op (n=2000, local)")


if __name__ == "__main__":
    main(sys.argv[1:])
