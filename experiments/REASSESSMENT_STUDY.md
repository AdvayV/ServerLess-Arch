# Reassessment study

This study evaluates the implemented dependency-driven aid simulation, separately from the earlier warm-container priority study. All records, rules and durations are synthetic. It evaluates neither institutional scholarship policy nor real-world award decisions.

## Reproduce

From the repository root:

```powershell
python -B experiments/reassessment_study.py
```

The exporter writes `reassessment_results.csv` and its `.provenance.json` companion. The provenance records source-file and CSV SHA-256 hashes, avoiding an ambiguous claim that an uncommitted implementation corresponds to an earlier Git revision. The sweep is deterministic and uses no host-clock timing fields.

The CSV uses LF line endings. Source fingerprints use UTF-8 with CRLF normalized to LF, so Git's Windows/Linux line-ending conversion does not invalidate the provenance. A regression check validates the saved CSV and every recorded source fingerprint.

There are **540 runs**: three workloads, three worker counts (1, 2, 4), five configured result-cache capacities (0, 1, 4, 12, 24), three schedulers and four reuse strategies. These are repeated parameter comparisons on fixed traces, not independent statistical samples. Do not infer a confidence interval or general performance ranking from the row count.

## Strategies

| Strategy | Results retained between requests | Shared exact-key jobs | Carry valid delivered checks across an amendment |
| --- | --- | --- | --- |
| LRU | Within the configured slot budget | Yes | Yes |
| Cost-aware GDSF | Within the configured slot budget | Yes | Yes |
| No result cache | No | Yes | Yes |
| Full recomputation | No | No | No |

Every strategy uses the same finite worker count, inputs, configured check durations, cancellation rules and version-safe publication gate. Full recomputation creates all required components for each new case generation; obsolete queued work is still cancelled and running work still has to finish. It can therefore use more computation without necessarily being slower on every workload. No-cache and full-recompute rows intentionally ignore the configured result-cache capacity.

## Correctness and timing

A request depends on academic, income and evidence checks. Check keys include the application, student, check, record version, rule version and evaluator version. Updates invalidate only keys whose dependency versions changed. Results already delivered into a pending case are separate from the bounded cache. A complete generation must match all current versions before publication, and its answers are compared to an independent uncached evaluator.

At one timestamp, record/rule updates precede worker completions; new requests and cancellations are then applied before replanning, publication and dispatch. An amendment preserves the request's original absolute deadline. A cancelled request is neither a completed request nor a deadline miss. A previously published result remains historical; a later update requires a new request, and `current_at_end` marks whether its target is still current. Different policies can finish on opposite sides of an update; compare the actual target vectors as well as their latencies.

## Metrics

- `compute_ms`: sum of all executed job durations, including stale or abandoned work; not wall-clock duration or monetary cost.
- `avg_latency_ms` and `p95_latency_ms`: original request release to publication; cancelled requests are excluded. `amendment_latency_ms` is available per case through the API.
- `cache_saved_compute_ms`: configured component costs for actual result-cache hits.
- `shared_subscriptions`: additional live subscribers that joined an existing exact-key job. `shared_compute_ms` sums those joined component costs; it is a demand-level counter and must not be added to cache savings to claim a baseline reduction. Use paired `compute_ms` values for measured savings.
- `reattached_components`: existing work reattached to a replacement generation without an additional concurrent subscriber; not counted as a shared-job saving.
- `carried_components`: already delivered results retained across target changes; not new cache hits.
- `stale_compute_ms`: execution that finished after its component key became obsolete.
- `unused_current_compute_ms`: valid completed work with no delivered subscribers at completion. This does not retrospectively classify every result later superseded or cancelled.
- Cache occupancy counts result slots. `peak_live_accumulator_components` counts pending-case delivered values separately. Neither includes history/log storage or bounds physical process memory.

## Inspectable example

With two workers and 12 result slots on `reuse`, EDF with LRU executes 12 jobs (800 ms summed computation) and reuses 12 computed results. The no-cache baseline executes 24 jobs (1,600 ms). Both produce the same eight case outcomes. These are observations of that configured trace, not production savings.

The `amendments` trace exercises active and queued supersession, shared jobs, scoped record updates and a rule change. The `pressure` trace exercises recomputation after eviction. The tests additionally reconstruct records at publication independently, exercise zero/one-slot caches, cancellation of one subscriber, and exact-time update/completion boundaries.

Container startup/retention coupling, calibrated cloud timings, public traces and institutional integration remain future work.
