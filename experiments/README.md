# Priority formula study

## Objective and formulas

The original recent-demand policy scores estimated reuse probability times startup delay divided by memory. It can retain several copies created by a slow cold start even when one ready copy could handle the observed arrivals.

The experimental objective is **additional startup delay avoided per MiB by keeping one more warm copy**.

Let `s` be startup delay in ms, `m` memory in MiB, `H=1800 ms`, `tau=2600 ms`, and `k` the number of current idle copies. Let `r_j(t)` estimate the additional accepted requests/ms supplied by the j-th server of a fixed warm pool. `b(u)` counts current active copies whose already-known completion is no later than `t+u`.

```text
Priority(f, k, t) = s_f / m_f * integral from 0 to H of r_(k+b(u))(t) du
```

When there are no active returns, this becomes `H * s_f * r_k(t) / m_f`. Higher value favors retention; the lowest idle removal value is evicted. Same-function idle copies are interchangeable for this calculation. Their removal value is recomputed after each eviction, with completion recency and container ID breaking ties.

### Empirical estimator (`marginal`)

At each observed arrival, assign the request to the lowest numbered free virtual slot and occupy that slot for **execution** duration. This shadow pool ignores actual startup delay and retention decisions. Decayed counts are:

```text
q_j(t) = sum over past arrivals assigned to slot j of exp(-(t-arrival)/tau)
raw_r_j(t) = q_j(t) / tau
```

Equal-weight nonincreasing isotonic regression pools adjacent rate estimates that violate the diminishing-copy assumption. An unobserved extra slot receives zero value. History persists after real eviction. The first observed invocation immediately supplies evidence for slot one.

Example: assignment execution is 80 ms and startup is 1500 ms. Arrivals 0/100/200 ms need one shadow slot; arrivals 0/50/100 need two; simultaneous arrivals need three. This prevents startup-induced overlap from being mistaken for warm execution concurrency.

### Analytical estimator (`erlang`)

Use the same decayed function arrival-rate estimate as the original policy. With offered load `rho = arrival_rate * execution_ms`:

```text
B_0 = 1
B_j = rho * B_(j-1) / (j + rho * B_(j-1))
r_j = arrival_rate * (B_(j-1) - B_j)
```

This is a stationary Poisson fixed-loss-pool model; deterministic execution times are supported in that stationary setting. [Primary source](https://www.columbia.edu/~ww2040/sensitive.pdf). Changing traffic and real cold requests replenishing the pool violate that stationary surrogate. [FaasCache](https://homes.luddy.indiana.edu/prateeks/papers/faascache-asplos21.pdf) supplies the cost/memory framing, not a novelty guarantee for these adaptations.

## Protocol

```powershell
python -B experiments/priority_study.py --seeds 3 --requests 120
```

The included CSV contains **864 runs**:

- Six workload families: Poisson, periodic, simultaneous batches, startup-only overlap, long gaps and demand shifts.
- Three calibration seeds (100-102) and three disjoint held-out seeds (1000-1002) per family.
- Three budgets: 256, 512 and 1024 MiB.
- Five original policies, two marginal policies and a no-active-return ablation.
- Every trace contains 120 requests. All methods receive identical input.

The empirical method with return credits was selected using calibration results before inspecting held-out scores. Tau and H stayed at the original fixed values; no parameter tuning was performed. Production outputs were checked against every prototype run: startup, latency, memory and observation-end metrics matched all 864 runs.

Memory is integrated over one common observation window per trace. It ends 1800 ms after the largest configured cold completion bound. After all requests finish, only idle timeout events can change memory, so the final idle area is computed analytically. `common_idle_mib_ms` and `common_avg_idle_mib` use this common window. Demo metrics retain their original last-completion window.

`observation_and_eviction_ms` includes history updates and eviction/scoring work using raw host-clock counters. Timing varies between runs and is not added to simulated request latency.

## Held-out observations

There are 54 held-out trace/budget cases. Values below sum startup delay over those cases.

| Method | Total startup ms | Mean common idle MiB |
| --- | ---: | ---: |
| Original adaptive | 4,384,300 | 498.7 |
| Poisson marginal | 3,842,900 | 464.9 |
| Empirical marginal | 3,703,400 | 463.8 |

Empirical marginal reduces pooled startup delay by **15.5%** and wins 36, loses 17 and ties one case against original adaptive. On simultaneous batches it increases startup delay from **630,900 to 680,700 ms**, a 7.9% loss. The no-return ablation performs slightly better on held-out aggregate startup delay (3,690,800 ms), despite performing worse during calibration; this experiment does not establish that return credits improve general performance.

## Limits

These are small synthetic experiments using four configured functions and only three seeds per family. No confidence interval or general superiority claim is made. Parameters are not calibrated to production data. The shadow pool is a fixed warm-capacity surrogate: actual misses create future reusable copies, which it does not fully model. Active returns are assumed available later despite possible future memory pressure. Shadow history storage and update cost scale with the largest observed execution concurrency. The budget still limits idle memory rather than total host memory. The common H multiplier does not change rankings when there are no returning copies.
