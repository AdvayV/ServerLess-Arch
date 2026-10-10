# Progress

## Demo build

- Added a deterministic Python event simulator and three fixed workload patterns.
- Added fixed timeout + LRU, LRU, LFU, and adaptive retention policies.
- Added a FastAPI page with plain-language explanations, comparison metrics, and a step-by-step request and eviction walkthrough.
- Added tests for reuse, overlapping requests, memory enforcement, timeout, memory integration, determinism, causal behavior, validation, and score arithmetic.
- Verified on Python 3.14.8: 8 tests passed; FastAPI home and `/api/run` both returned HTTP 200; inline JavaScript passed `node --check`.
- Simplified the page to three steps: choose a situation, compare rules, and replay decisions. Detailed metrics and assumptions are expandable.

## College portal update - 10 October 2026

- Replaced the edge-computer services with timetable, assignment submission, semester report, and fee payment using the handoff's illustrative resource values.
- Replaced all three workloads with a normal college day, an assignment-deadline rush, and demand shifting from timetables/assignments to semester reports.
- Updated the page, API scenario descriptions, README, and regression-test timings to match the college example. No example reuse probabilities are hard-coded into the policy.
- Verification: 8 tests passed in 0.06 s; inline JavaScript passed `node --check`; FastAPI home and comparison endpoint returned HTTP 200. All three scenarios were exercised at 0, 256, 512, and 1024 MiB.
- Historical results from the longer college traces were recorded during the first update; they are superseded by the shorter FaasCache demo below.
- File access was restored after the incomplete Git directory was renamed to `.git-codex-backup`. Git integration remains deferred.

## Short FaasCache demo - 10 October 2026

- Read the local FaasCache paper's caching policies, provisioning design, and implementation components (sections 4–6 and figure 4).
- Added an adapted GDSF policy with startup cost, memory size, shared invocation frequency, per-container access clocks, and an eviction logical clock. Frequency resets only after the last live copy of a function is removed.
- Reduced normal-day traffic to 8 requests and both deadline-rush and demand-shift traffic to 6 requests. Replay now shows one step per arrival plus a final snapshot, while preserving all removal explanations and the full event log in the API.
- Defaulted the page to FaasCache, with fixed timeout and recent-demand variation visible. LRU/LFU are optional. Added paper-component mapping and explicit resource-model differences to the page and README.
- Added total startup overhead and warm-start count. Recent-demand decay is labelled experimental; it is not a proven improvement over the published algorithm.
- Verification: 15 tests passed, inline JavaScript passed `node --check`, and FastAPI checks covered all 21 combinations of three scenarios and seven supported budgets. Invalid scenario and budget inputs were rejected.
- New default shift trace at 512 MiB: FaasCache and fixed timeout each have 5/6 cold starts, 7,400 ms total startup overhead, and 1,313.3 ms average response. Recent-demand and LRU each have 4/6 cold starts, 4,900 ms startup overhead, and 896.7 ms average response. This tiny trace illustrates a tradeoff, not general superiority.
- Scope remains a Python simulator with a FastAPI interface: no physical-host admission limit, real Docker/OpenWhisk execution, reuse-distance provisioning, or elastic VM controller.

## Next steps

- Visually inspect the page in a browser during rehearsal. This environment exposed no browser surface, so automated visual inspection was unavailable.
- Push the local Git repository to GitHub when SSH authentication is available. Remote access previously failed with `Permission denied (publickey)`.
- Explore replica-aware marginal retention as a research extension after the lab demo.
- Add real Docker measurements only when the simulator comparison has been demonstrated and verified.

## GitHub upload preparation - 10 October 2026

- Prepared source, tests, handoff, and current documentation in a separate local Git directory, `.publish-git`, without modifying the earlier Git backup. PDFs, ZIPs, caches, and publication metadata are ignored.
- Configured origin as `git@github.com:AdvayV/ServerLess-Arch.git`. GitHub's SSH host key was pinned from its official documentation in a local ignored known-hosts file.
- Fixed the HTML filename reference to match `INDEX.html` on case-sensitive Linux filesystems. Rechecked: 15 tests passed.
- Remote inspection was blocked by SSH `Permission denied (publickey)`; HTTPS alternatives also failed certificate validation. Repository contents could not be fetched, so publication must use a normal non-forced push once authentication is available.

## Standalone demo folder - 10 October 2026

- Consolidated demo source, tests, requirements, handoff, and documentation into `ServerLess-Arch`, separate from the research PDFs and manifests.
- Created a normal Git checkout with its own `.git` directory and preserved the initial source commit. Origin points to `git@github.com:AdvayV/ServerLess-Arch.git`.
- Updated run and publication instructions for the new folder. Verification from this folder: 15 simulator tests passed.
