# Serverless simulations

Two virtual-time simulations for studying caching and scheduling:

- **Warm or cold?** compares seven policies for retaining idle serverless containers under a memory budget, including two experimental methods that value extra warm copies.
- **When the key changes** compares three schedulers and two result-cache policies for selective exam regrading after rubric corrections.

Inputs are small, deterministic synthetic workloads. These simulations do not execute cloud services, grade real submissions, or establish that a policy is superior.

## Run

Use Python 3.11 or newer. From this repository folder in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Open [the warm-container demo](http://127.0.0.1:8000/) or [the correction demo](http://127.0.0.1:8000/corrections).
Choose a scenario, run a comparison, and select a policy row to inspect its decisions. The warm-container page also provides a step-by-step request replay. Rows support Enter and Space as well as clicks.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Checks cover retention budgets, overlapping requests, timeout behavior, version-scoped invalidation, superseded and stale jobs, cache eviction, input validation, API responses, and page assets.
The engines can also run without the web dependencies:

```powershell
python -c "from simulator import compare; print(compare('burst', 512)['runs']['faascache']['summary'])"
python -c "from corrections import compare; print(compare('shift')['runs']['edf_lru']['summary'])"
```

## API

| Endpoint | Example JSON | Choices |
| --- | --- | --- |
| `POST /api/run` | `{"scenario":"shift","budget_mib":512}` | `steady`, `burst`, `shift`; budgets 0, 128, 256, 384, 512, 768, 1024 MiB |
| `POST /api/corrections` | `{"scenario":"single"}` | `single`, `rush`, `shift` |

Each response contains the common workload, policy runs, summaries, and event logs. Resource values must be integers; unknown options are rejected. Invalid scenarios and unsupported budgets return HTTP 400; malformed request bodies return HTTP 422. Interactive API documentation is at `/docs`.

## Repository

| File or folder | Purpose |
| --- | --- |
| `simulator.py` | Container lifecycle, idle retention, request traces and metrics |
| `corrections.py` | Versioned result cache, finite workers, scheduling and correction traces |
| `simulation_utils.py` | Shared integer validation and nearest-rank percentiles |
| `replica_value.py` | Shadow warm-pool history, Erlang increments and marginal copy values |
| `experiments/` | Seeded policy comparisons, results and evaluation protocol |
| `app.py` | FastAPI routes and static assets |
| `INDEX.html`, `CORRECTIONS.html`, `static/` | Interfaces, styles and browser scripts |
| `tests/`, `pytest.ini` | Regression checks and test discovery |
| `requirements.txt`, `requirements-dev.txt` | Runtime and development dependencies |
| `ARCHITECTURE.md` | Algorithms, event ordering and model boundaries |

Research papers and archived handoff/backup files are outside this repository.

## Research basis

The warm-container GDSF policy adapts [FaasCache: Keeping Serverless Computing Alive with Greedy-Dual Caching (ASPLOS 2021)](https://homes.luddy.indiana.edu/prateeks/papers/faascache-asplos21.pdf). Its score uses access clocks, function frequency, configured startup cost and memory size. This model constrains **idle** memory and assumes sufficient active capacity; the paper's platform constrains warm and running containers together. Startup costs here are illustrative constants.

The recent-demand retention rule and fair EDF scheduler are experimental heuristics. The small traces help explain their behavior; novelty, fairness guarantees and performance improvements require separate research evidence.

## Experimental value of extra copies

The original `adaptive` policy gives a function's idle copies the same reuse-probability score regardless of how many copies already exist. The new `marginal` policy replays observed arrivals in a virtual pool with no startup delay, occupying each slot for the configured execution duration. It estimates the additional warm hits supplied by another slot, weights startup delay avoided per MiB, and credits active copies only after their known completion. `erlang` estimates the same fixed-pool quantity under a stationary Poisson model.

Both remain experimental. In the included held-out synthetic study, `marginal` reduces total startup delay by 15.5% against `adaptive`, but loses 17 of 54 individual comparisons and performs worse on repeated simultaneous batches. The original methods remain available. See [the evaluation protocol](experiments/README.md) for assumptions, formulas and reproduction commands.
