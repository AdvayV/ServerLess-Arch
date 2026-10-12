"""Validation and metrics shared by both virtual-time simulations."""

from collections.abc import Sequence
from math import ceil


def require_integer(value: object, name: str, minimum: int = 0) -> None:
    """Reject booleans, fractional values, and invalid resource bounds."""
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def percentile(values: Sequence[int], fraction: float) -> float:
    """Nearest-rank percentile; an empty sample returns zero."""
    if not 0 < fraction <= 1:
        raise ValueError("Percentile fraction must be in (0, 1]")
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[ceil(fraction * len(ordered)) - 1])
