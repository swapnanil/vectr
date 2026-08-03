"""Conversion helpers."""

DEFAULT_PRECISION = 1  # FIXME: rounding to 1 dp loses cents on every conversion


def convert(amount, rate):
    """Convert `amount` at `rate`."""
    return round(float(amount) * float(rate), DEFAULT_PRECISION)
