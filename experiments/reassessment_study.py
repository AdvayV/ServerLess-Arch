"""Export a deterministic resource sweep for the dependency-driven aid model."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reassessment import SCENARIOS, SCHEDULERS, STRATEGIES, ReassessmentSimulator, workload


WORKERS = (1, 2, 4)
CAPACITIES = (0, 1, 4, 12, 24)
METRICS = ("requests", "published", "cancelled_requests", "unfinished", "deadline_misses",
           "avg_latency_ms", "p95_latency_ms", "jobs_created", "jobs_completed", "jobs_cancelled",
           "jobs_discarded_stale", "compute_ms", "stale_compute_ms", "unused_current_compute_ms",
           "cache_hits", "cache_misses", "cache_evictions", "cache_invalidations", "cache_saved_compute_ms",
           "shared_subscriptions", "shared_compute_ms", "carried_components", "reattached_components",
           "peak_cache_entries", "peak_live_accumulator_components", "reference_verified_cases", "reference_verified")


def generate():
    for scenario in SCENARIOS:
        spec = workload(scenario)
        for workers in WORKERS:
            for capacity in CAPACITIES:
                for scheduler in SCHEDULERS:
                    for strategy in STRATEGIES:
                        run = ReassessmentSimulator(spec, scheduler, strategy, workers, capacity).run()
                        summary = run["summary"]
                        if not summary["reference_verified"] or summary["unfinished"]:
                            raise RuntimeError("Reassessment sweep found an invalid or unfinished case")
                        yield {"scenario": scenario, "workers": workers, "configured_cache_slots": capacity,
                               "scheduler": scheduler, "strategy": strategy,
                               **{key: summary[key] for key in METRICS}}


def export(output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = list(generate())
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    sources = ["aid_domain.py", "reassessment.py", "result_cache.py", "simulation_utils.py",
               "experiments/reassessment_study.py"]
    provenance = {
        "description": "Deterministic synthetic resource sweep; rows are not independent statistical samples.",
        "rows": len(rows), "csv_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_hash_format": "UTF-8 with CRLF normalized to LF",
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                          for name in sources},
        "scenarios": list(SCENARIOS), "workers": WORKERS, "cache_capacities": CAPACITIES,
        "schedulers": SCHEDULERS, "strategies": STRATEGIES,
    }
    output.with_suffix(".provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/reassessment_results.csv")
    args = parser.parse_args()
    print(f"Exported {export(args.output)} verified runs to {args.output}")
