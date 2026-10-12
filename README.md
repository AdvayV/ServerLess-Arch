# Warm or Cold? — serverless memory demo

This is a small, explainable **college-portal simulation** of serverless cold starts and warm-container retention, based mainly on **FaasCache: Keeping Serverless Computing Alive with Greedy-Dual Caching** (Fuerst and Sharma, ASPLOS 2021). It compares an adapted FaasCache GDSF rule, a fixed timeout + LRU, and an experimental recent-demand variation. LRU and LFU are available as additional baselines. Every policy receives the same student requests and idle-memory budget.

## Run on Windows PowerShell

Python 3.14.8 was used during development. Python 3.11 or newer is expected to work.

Open a terminal in this `ServerLess-Arch` folder. From the original research folder, use:

```powershell
cd "C:\Advay study\VIT\SEM 5 Fall 26-27\Operating System - OS\LAB OS Reviews\ServerLess-Arch"
```

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Open http://127.0.0.1:8000. The page runs one comparison automatically with FaasCache selected. Pick a college situation or memory budget and press **Run comparison**. Select a policy row and use **Next** to follow one student at a time. Each scenario has only 6–8 requests plus one final snapshot. Expand **Memory decisions since the last request** to explain removals. Check **Show LRU and LFU too** for all five policies.

Open http://127.0.0.1:8000/corrections for the **exam answer-key correction** research prototype. It models selective invalidation of question-level grade components, a finite worker pool, FIFO / earliest-deadline / fair-earliest-deadline schedulers, and LRU / cost-aware GDSF cache eviction. Choose one of three synthetic correction scenarios; each comparison replays identical input across six policy combinations. Results are simulation only. The design and scope are in `ARCHITECTURE.md`.

Run the checks with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The Python core can run without FastAPI:

```powershell
python -c "from simulator import compare; import json; print(json.dumps(compare('burst', 512)['runs']['faascache']['summary'], indent=2))"
```

## Folder and Git

This folder is a standalone Git repository. `app.py` serves the API and page, `INDEX.html` contains the interface, `simulator.py` implements the simulation, and `tests/test_simulator.py` checks its behavior. `requirements.txt`, this README, `PROJECT_HANDOFF.md`, and `PROGRESS.md` provide setup and project documentation. The research PDFs remain in the parent folder.

The `origin` remote is `https://github.com/AdvayV/ServerLess-Arch.git`. The implementation has been published to `main`. For subsequent changes, commit them and publish with:

```powershell
git status
git push -u origin main
```

The initial source commit is preserved. Normal pushes preserve existing history; reconcile any conflicting remote changes before pushing. This checkout uses HTTPS and the existing Git Credential Manager authentication.

## What it models

The four services use the illustrative values from the project handoff:

| Student action | Memory MiB | Startup ms | Execution ms |
| --- | ---: | ---: | ---: |
| View timetable | 128 | 100 | 20 |
| Submit assignment | 256 | 1500 | 80 |
| View semester report | 512 | 2500 | 150 |
| Pay fees | 128 | 800 | 60 |

One ready copy of every service requires 1,024 MiB. With a 512 MiB idle budget, the policy must choose what to retain. A cold assignment request takes 1,580 ms versus 80 ms when warm. All activity is simulated; the demo does not execute actual college services.

The `steady` trace has eight actions: each service is called twice. The six-action `burst` trace includes three overlapping assignment submissions to demonstrate that one container cannot serve simultaneous requests. The six-action `shift` trace changes from timetable checks to assignments, fee payment, and semester reports. The compressed timelines illustrate these patterns rather than modelling a real day's duration. The recent-demand policy estimates reuse from observed requests; example probabilities are not supplied as predictions.

Each function has a configured startup delay, execution duration, and memory requirement. An arriving request reuses an idle container of the same function if possible. Otherwise, it creates a new one. A container serves one request at a time. Starting and busy containers cannot be evicted. After a request finishes, the policy decides which idle containers fit within the budget.

The simulator uses a virtual-time event queue; it never sleeps to imitate startup. At equal times, startup and execution completions run first, then idle timeouts, then arrivals in request-ID order. Zero-duration operations complete before the next equal-time arrival. Stale timeout events are ignored. A run ends at its last request completion; idle memory is integrated from time zero to that completion. A zero-request run reports zero metrics.

The adapted **FaasCache GDSF** priority follows section 4.1, equation (1):

```text
priority = container access clock + function frequency × startup cost / memory size
```

Frequency is shared across all containers of a function and resets when its last live container is evicted. Each container records the server's logical clock when used. Evicting a container advances that clock to its priority; this is an eviction clock, not wall-clock time. The lowest-priority idle container is removed first. Shared function frequency is evaluated when selecting victims, with completion recency and container ID breaking ties. Startup cost and size are configured illustrative values; they are not measured from executions.

The **recent-demand variation** (API key `adaptive`) uses estimated reuse probability over 1,800 ms × configured startup delay ÷ memory MiB. A decayed request count with 2,600 ms decay estimates demand using observed arrivals only. It uses a Poisson arrival assumption, which can be inaccurate for periodic or irregular bursts. Every idle copy of a function gets the same function-level score; this can overvalue extra copies. The score is a heuristic, not an optimal memory packing algorithm or a proven improvement over FaasCache.

LRU uses the most recent execution completion time. LFU uses cumulative observed invocation counts per function, then recency and container ID for ties. The fixed timeout is 1,200 ms and also uses LRU when memory fills before expiry. Cumulative LFU can respond slowly to demand shifts. Ties are deterministic.

The idle budget is **not a total host memory limit**. Active capacity is assumed sufficient, and active memory peak is tracked separately. Requests never queue. A container larger than the idle budget can run but cannot remain ready after it finishes. Network latency and Docker are not included. All displayed workload timings are illustrative simulation inputs, not measurements of a real platform.

The existing API is `POST /api/run` with JSON such as `{"scenario":"burst","budget_mib":512}`. It returns all five policies' request outcomes, full event logs, simplified `demo_steps`, memory timelines, and summaries. Scenarios are `burst`, `shift`, and `steady`. The comparison supports budgets of 0, 128, 256, 384, 512, 768, and 1024 MiB; the page offers the most useful four.

The correction API is `POST /api/corrections` with `{"scenario":"single"}`, `{"scenario":"rush"}`, or `{"scenario":"shift"}`. It returns the common workload, six scheduler/cache runs, per-task outcomes, priority decisions, cache metrics, and event logs. Question, submission, rubric, and grader versions are part of component-cache keys. The correction workflow only invalidates old components for the changed question; it checks the current rubric again before publishing a result. Worker capacity and cache capacity are fixed simulation inputs. FIFO and earliest-deadline-first are baselines; fair EDF and GDSF are candidate heuristics, not optimality claims.

Summary metrics include request count, cold and warm starts, cold-start percentage, total startup overhead, average/p95/p99 response latency, idle MiB-ms and average/peak idle memory, peak active memory, evictions, policy decisions, and controller wall-clock milliseconds. Total startup overhead captures the paper's concern that cold starts have different costs; counting misses alone can hide expensive misses. Percentiles use nearest rank. Controller time is measured separately and never added to simulated response latency. The traces are deterministic and identical for all policies.

## Mapping to the paper

| Paper component | Demo implementation | Scope |
| --- | --- | --- |
| Section 4: keep-alive as caching | Reuse an idle container; otherwise create a cold container | Core cache analogy implemented |
| Section 4.1: GDSF | Frequency, initialization cost, size, individual access clocks and eviction clock in `Simulator` | Equation-based adaptation to idle retention |
| Section 4.2: comparison policies | LRU and LFU; fixed timer as the conventional baseline | Simplified baselines; no Landlord policy |
| Section 5.1: static provisioning | User changes idle memory and compares results | Illustrates tradeoffs; no reuse-distance curve or automatic sizing |
| Section 5.2: elastic scaling | No dynamic resource controller | Not implemented; changing demand is a trace, not autoscaling |
| Section 6, figure 4: incoming requests and ContainerPool | Request trace → event queue → container pool → retention policy | Python model; FastAPI presents results |
| Section 6: Provisioning Controller and OpenWhisk invoker | Manual budget input and simulated execution | No OpenWhisk, VM deflation, or Docker |
| Sections 6–7: simulation and evaluation | Same trace replayed under five policies, latency and memory metrics | Small hand-inspectable synthetic workloads, not Azure-scale validation |

The main difference from the paper is resource admission: FaasCache limits **warm plus running** containers by physical server capacity and frees resources when launching a new container. This demo limits **idle** memory after execution finishes and assumes enough active capacity. The paper also learns initialization costs from cold/warm executions; this model uses constants. Accordingly, it is labelled an **adapted policy**, not a reproduction of the published platform or its results.

For a short presentation: use **Timetables to semester reports / 512 MiB**, select FaasCache, and advance through seven steps. Explain reuse, then show the memory-removal explanations. Compare startup overhead and latency with the fixed timer and recent-demand variation. Try **256 MiB** to show that a 512 MiB report container can execute but cannot stay ready. Use **Assignment deadline rush** to show concurrent copies. These are explanations of policy behavior, not research benchmarks.

## Research position

FaasCache already studies value-based warm retention, so a startup-cost/memory score alone is not a novelty claim. The implemented recent-demand variation lets historical popularity fade, making changing demand a testable hypothesis. Six- or eight-request examples cannot establish superiority; larger traces, parameter sweeps, and repeated workload evaluation are needed. A further research extension is to estimate the marginal benefit of each additional idle copy under bursty concurrency. That extension needs its own baseline comparisons and ablation tests. A later Docker backend could measure actual startup and idle-memory consumption.
