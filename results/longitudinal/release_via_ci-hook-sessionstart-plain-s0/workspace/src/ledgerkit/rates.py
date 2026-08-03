"""Conversion helpers."""

DEFAULT_PRECISION = 2


def convert(amount, rate):
    """Convert `amount` at `rate`, rounded to `DEFAULT_PRECISION` decimal places."""
    return round(float(amount) * float(rate), DEFAULT_PRECISION)


def convert_bulk(amounts, rate):
    """Convert each amount in `amounts` at `rate`."""
    return [convert(amount, rate) for amount in amounts]
