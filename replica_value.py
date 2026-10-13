"""Causal estimates of the marginal service supplied by another warm copy.

These are fixed-warm-pool surrogates, not exact predictions of cold starts in
the simulator, where cold requests create additional copies after startup.
"""

from dataclasses import dataclass
from math import exp

from simulation_utils import require_integer


def nonincreasing_rates(values: list[float]) -> list[float]:
    """Equal-weight isotonic projection using pooled adjacent violations."""
    blocks: list[tuple[float, int]] = []
    for value in values:
        blocks.append((value, 1))
        while len(blocks) >= 2:
            left_sum, left_count = blocks[-2]
            right_sum, right_count = blocks[-1]
            if left_sum / left_count >= right_sum / right_count:
                break
            blocks[-2:] = [(left_sum + right_sum, left_count + right_count)]
    return [total / count for total, count in blocks for _ in range(count)]


@dataclass
class _ShadowSlot:
    available_at: int
    count: float


class ShadowWarmPool:
    """Replay arrivals as if startup were free, using the lowest free slot."""

    def __init__(self, execution_ms: int, tau_ms: int = 2600):
        require_integer(execution_ms, "Execution duration")
        require_integer(tau_ms, "Demand decay", 1)
        self.execution_ms = execution_ms
        self.tau_ms = tau_ms
        self.updated = 0
        self.slots: list[_ShadowSlot] = []

    def observe(self, now: int) -> int:
        require_integer(now, "Observation time")
        if now < self.updated:
            raise ValueError("Observations must be chronological")
        decay = exp(-(now - self.updated) / self.tau_ms)
        for slot in self.slots:
            slot.count *= decay
        rank = next((index for index, slot in enumerate(self.slots)
                     if slot.available_at <= now), len(self.slots))
        if rank == len(self.slots):
            self.slots.append(_ShadowSlot(now, 0.0))
        self.slots[rank].available_at = now + self.execution_ms
        self.slots[rank].count += 1.0
        self.updated = now
        return rank + 1

    def rates(self, now: int) -> list[float]:
        if now < self.updated:
            raise ValueError("Cannot score before the last observation")
        decay = exp(-(now - self.updated) / self.tau_ms) / self.tau_ms
        return nonincreasing_rates([slot.count * decay for slot in self.slots])


def erlang_marginal_rates(arrival_rate: float, execution_ms: int, copies: int) -> list[float]:
    """Accepted-arrival increments for stationary Poisson fixed warm pools."""
    require_integer(execution_ms, "Execution duration")
    require_integer(copies, "Copy count")
    if arrival_rate < 0:
        raise ValueError("Arrival rate must be nonnegative")
    offered_load = arrival_rate * execution_ms
    blocking = 1.0
    rates = []
    for copies_in_pool in range(1, copies + 1):
        next_blocking = offered_load * blocking / (copies_in_pool + offered_load * blocking)
        rates.append(arrival_rate * (blocking - next_blocking))
        blocking = next_blocking
    return rates


def expected_extra_hits(
    rates: list[float], idle_copies: int, active_return_times: list[int],
    now: int, horizon_ms: int,
) -> float:
    """Integrate a frozen marginal-rate estimate over known active returns.

    Idle copies supply capacity now. Active copies receive credit only after
    their scheduled completion; this assumes they remain in the future pool.
    Rates beyond observed/estimated capacity have value zero.
    """
    require_integer(idle_copies, "Idle copy count", 1)
    require_integer(horizon_ms, "Prediction horizon", 1)
    returning = sorted(time - now for time in active_return_times if time <= now + horizon_ms)
    available = idle_copies + sum(offset <= 0 for offset in returning)
    previous = 0
    saved_requests = 0.0
    for offset in [offset for offset in returning if offset > 0] + [horizon_ms]:
        rate = rates[available - 1] if available <= len(rates) else 0.0
        saved_requests += (offset - previous) * rate
        available += 1
        previous = offset
    return saved_requests
