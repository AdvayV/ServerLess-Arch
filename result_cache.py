"""Bounded cache of immutable computed results, independent of publication."""

from dataclasses import dataclass

from aid_domain import CheckResult, ComponentKey
from simulation_utils import require_integer


@dataclass
class _Entry:
    result: CheckResult
    cost: int
    last: int
    accesses: int
    clock: float


class ResultCache:
    def __init__(self, capacity: int, policy: str):
        require_integer(capacity, "Cache capacity")
        if policy not in ("lru", "gdsf"):
            raise ValueError(f"Unknown result-cache policy: {policy}")
        self.capacity = capacity
        self.policy = policy
        self.entries: dict[ComponentKey, _Entry] = {}
        self.clock = 0.0
        self.sequence = 0
        self.hits = self.misses = self.evictions = self.invalidations = 0
        self.peak_entries = 0

    def get(self, key: ComponentKey) -> CheckResult | None:
        entry = self.entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        self.sequence += 1
        entry.last = self.sequence
        entry.accesses += 1
        entry.clock = self.clock
        return entry.result

    def _priority(self, key: ComponentKey) -> float:
        entry = self.entries[key]
        return entry.clock + entry.accesses * entry.cost

    def put(self, key: ComponentKey, result: CheckResult, cost: int) -> None:
        require_integer(cost, "Recomputation cost", 1)
        if not self.capacity:
            return
        self.sequence += 1
        if key in self.entries:
            old = self.entries[key]
            self.entries[key] = _Entry(result, cost, self.sequence, old.accesses + 1, self.clock)
            return
        if len(self.entries) == self.capacity:
            if self.policy == "lru":
                victim = min(self.entries, key=lambda k: (self.entries[k].last, k))
            else:
                victim = min(self.entries, key=lambda k: (self._priority(k), self.entries[k].last, k))
                self.clock = max(self.clock, self._priority(victim))
            del self.entries[victim]
            self.evictions += 1
        self.entries[key] = _Entry(result, cost, self.sequence, 1, self.clock)
        self.peak_entries = max(self.peak_entries, len(self.entries))

    def invalidate(self, current_key) -> None:
        obsolete = [key for key in self.entries if current_key(key.student, key.check) != key]
        for key in obsolete:
            del self.entries[key]
        self.invalidations += len(obsolete)
