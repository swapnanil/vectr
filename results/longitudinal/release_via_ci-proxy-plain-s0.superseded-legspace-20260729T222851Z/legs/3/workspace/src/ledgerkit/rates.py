"""Conversion helpers."""

DEFAULT_PRECISION = 2


def convert(amount, rate):
    """Convert `amount` at `rate`."""
    return round(float(amount) * float(rate), DEFAULT_PRECISION)
