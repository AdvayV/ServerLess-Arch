"""Compare replica-value candidates on disjoint seeded synthetic workloads.

The original five baseline policies are unchanged. Memory is integrated to a
common observation end, including an analytically computed post-completion
idle tail. Timing includes observation updates and eviction decisions.
"""

import argparse
import csv
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator import FUNCTIONS, Simulator


METHODS = ("timeout", "faascache", "adaptive", "lru", "lfu", "erlang", "marginal", "marginal_no_returns")
FAMILIES = ("poisson", "periodic", "batches", "startup_overlap", "long_gaps", "shift")


class TrialSimulator(Simulator):
    def __init__(self, trace, method, budget, **options):
        super().__init__(trace, "marginal" if method == "marginal_no_returns" else method, budget, **options)
        self.method = method

    def active_return_times(self, function):
        return [] if self.method == "marginal_no_returns" else super().active_return_times(function)

    def summary_at(self, end):
        run = self.run()
        summary = dict(run["summary"])
        own_end = summary["evaluation_ms"]
        if end < own_end:
            raise ValueError("Common observation end is too early")
        extra_area = 0
        for container in self.containers.values():
            if container["state"] != "IDLE":
                continue
            expiry = container["last_used"] + self.timeout_ms if self.method == "timeout" else end
            extra_area += FUNCTIONS[container["function"]].memory * max(0, min(end, expiry) - own_end)
        summary["common_observation_ms"] = end
        summary["common_idle_mib_ms"] = summary["idle_mib_ms"] + extra_area
        summary["common_avg_idle_mib"] = summary["common_idle_mib_ms"] / end if end else 0
        summary["observation_and_eviction_ms"] = (self.observation_ns + self.decision_ns) / 1_000_000
        return summary


def workload(family, seed, count=120):
    rng = random.Random(seed)
    names = tuple(FUNCTIONS)
    calls = []
    time = 0
    for index in range(count):
        if family == "poisson":
            time += max(1, round(rng.expovariate(1 / 160)))
            function = rng.choices(names, (4, 3, 1, 2))[0]
        elif family == "periodic":
            time += 100 + rng.randrange(-10, 11)
            function = names[(index // 8) % len(names)]
        elif family == "batches":
            if index % 6 == 0:
                time += 1800 + rng.randrange(-150, 151)
            function = "assignment" if index % 6 < 4 else rng.choice(("timetable", "payment"))
        elif family == "startup_overlap":
            time += 100 + rng.randrange(0, 25)
            function = "assignment" if index % 12 < 8 else rng.choice(("timetable", "payment"))
        elif family == "long_gaps":
            time += rng.choice((50, 100, 250, 6000))
            function = rng.choice(names)
        elif family == "shift":
            time += rng.randrange(40, 181)
            phase = min(2, index * 3 // count)
            weights = ((7, 1, 1, 1), (1, 7, 1, 1), (1, 1, 5, 3))[phase]
            function = rng.choices(names, weights)[0]
        else:
            raise ValueError(family)
        calls.append({"id": f"R{index:05d}", "arrival": time, "function": function})
    return calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=4)
    parser.add_argument("--requests", type=int, default=120)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments" / "priority_results.csv")
    args = parser.parse_args()
    rows = []
    for split, first_seed in (("calibration", 100), ("held_out", 1000)):
        for family in FAMILIES:
            for seed in range(first_seed, first_seed + args.seeds):
                trace = workload(family, seed, args.requests)
                end = max(request["arrival"] + FUNCTIONS[request["function"]].startup
                          + FUNCTIONS[request["function"]].execution for request in trace) + 1800
                for budget in (256, 512, 1024):
                    for method in METHODS:
                        summary = TrialSimulator(trace, method, budget).summary_at(end)
                        rows.append({"split": split, "family": family, "seed": seed,
                                     "budget_mib": budget, "method": method,
                                     "tau_ms": 2600, "horizon_ms": 1800, **summary})
            print(f"Finished {split}: {family}", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} runs to {args.output}", flush=True)


if __name__ == "__main__":
    main()
