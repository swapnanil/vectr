"""Conversion helpers."""

DEFAULT_PRECISION = 2


def convert(amount, rate):
    """Convert `amount` at `rate`."""
    return round(float(amount) * float(rate), DEFAULT_PRECISION)


def convert_bulk(pairs):
    """Convert each (amount, rate) pair in `pairs`."""
    return [convert(amount, rate) for amount, rate in pairs]
