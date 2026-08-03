"""Radix sort for non-negative integers."""


def radix_sort(items):
    if not items:
        return items
    out = list(items)
    exp = 1
    limit = max(out)
    while limit // exp > 0:
        buckets = [[] for _ in range(10)]
        for value in out:
            buckets[(value // exp) % 10].append(value)
        out = [v for bucket in buckets for v in bucket]
        exp *= 10
    return out
