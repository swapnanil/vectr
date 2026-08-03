"""Conversion helpers."""

DEFAULT_PRECISION = 2


def convert(amount, rate):
    """Convert `amount` at `rate`."""
    return round(float(amount) * float(rate), DEFAULT_PRECISION)


def convert_bulk(amounts, rate):
    """Convert each amount in `amounts` at the same `rate`."""
    return [convert(amount, rate) for amount in amounts]
