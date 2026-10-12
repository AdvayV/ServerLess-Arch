"""Deterministic, virtual-time serverless warm-container simulator."""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import exp
from time import perf_counter_ns

from simulation_utils import percentile, require_integer


@dataclass(frozen=True)
class Function:
    name: str
    memory: int
    startup: int
    execution: int


FUNCTIONS = {
    "timetable": Function("View timetable", 128, 100, 20),
    "assignment": Function("Submit assignment", 256, 1500, 80),
    "report": Function("View semester report", 512, 2500, 150),
    "payment": Function("Pay fees", 128, 800, 60),
}

SCENARIOS = {
    "steady": {
        "title": "A normal college day",
        "description": "Eight student actions: each portal service is requested twice so you can see creation and reuse.",
    },
    "burst": {
        "title": "Assignment deadline rush",
        "description": "Six student actions: two timetable checks, three overlapping submissions, then one later submission.",
    },
    "shift": {
        "title": "Timetables to semester reports",
        "description": "Six student actions: timetable checks, an assignment, fee payment, then two semester-report requests.",
    },
}

POLICIES = ("timeout", "faascache", "adaptive", "lru", "lfu")
POLICY_NAMES = {
    "timeout": "Fixed timeout + LRU",
    "lru": "Least recently used",
    "lfu": "Least frequently used",
    "faascache": "FaasCache GDSF (adapted)",
    "adaptive": "Recent-demand variation",
}


def workload(name: str) -> list[dict]:
    """Fixed, hand-inspectable traces; shared unchanged by every policy."""
    if name == "steady":
        calls = [(0, "timetable"), (200, "timetable"), (600, "assignment"),
                 (2400, "assignment"), (2800, "payment"), (3800, "payment"),
                 (4300, "report"), (7300, "report")]
    elif name == "burst":
        calls = [(0, "timetable"), (200, "timetable"), (800, "assignment"),
                 (850, "assignment"), (900, "assignment"), (2700, "assignment")]
    elif name == "shift":
        calls = [(0, "timetable"), (200, "timetable"), (600, "assignment"),
                 (2400, "payment"), (4000, "report"), (8400, "report")]
    else:
        raise ValueError(f"Unknown traffic pattern: {name}")
    return [
        {"id": f"R{i + 1:02d}", "arrival": arrival, "function": function}
        for i, (arrival, function) in enumerate(calls)
    ]


class Simulator:
    def __init__(self, trace: list[dict], policy: str, budget: int, *, timeout_ms: int = 1200,
                 horizon_ms: int = 1800, tau_ms: int = 2600):
        if policy not in POLICIES:
            raise ValueError(f"Unknown policy: {policy}")
        require_integer(budget, "Idle memory budget")
        require_integer(timeout_ms, "Idle timeout")
        require_integer(horizon_ms, "Prediction horizon", 1)
        require_integer(tau_ms, "Demand decay", 1)
        ids = set()
        for request in trace:
            if not isinstance(request, dict) or not {"id", "function", "arrival"} <= request.keys():
                raise ValueError("Each request needs id, function, and arrival fields")
            if not isinstance(request["id"], str) or not request["id"]:
                raise ValueError("Request IDs must be nonempty strings")
            if request["id"] in ids:
                raise ValueError(f"Duplicate request ID: {request['id']}")
            ids.add(request["id"])
            if not isinstance(request["function"], str) or request["function"] not in FUNCTIONS:
                raise ValueError(f"Unknown function: {request['function']}")
            require_integer(request["arrival"], f"Arrival for {request['id']}")
        self.trace = sorted((dict(request) for request in trace), key=lambda r: (r["arrival"], r["id"]))
        self.policy = policy
        self.budget = budget
        self.timeout_ms = timeout_ms
        self.horizon_ms = horizon_ms
        self.tau_ms = tau_ms
        self.now = 0
        self.queue: list[tuple] = []
        self.sequence = 0
        self.containers: dict[int, dict] = {}
        self.next_container_id = 0
        self.requests: dict[str, dict] = {}
        self.history: dict[str, dict] = {key: {"q": 0.0, "updated": 0, "count": 0} for key in FUNCTIONS}
        # Paper section 4.1: shared frequency, individual access clock, and
        # a server logical clock advanced by evictions (not wall-clock time).
        self.gdsf_frequency = {key: 0 for key in FUNCTIONS}
        self.gdsf_clock = 0.0
        self.events: list[dict] = []
        self.timeline = [{"time": 0, "idle_mib": 0}]
        self.idle_area = 0
        self.peak_idle = 0
        self.peak_active = 0
        self.evictions = 0
        self.decisions = 0
        self.decision_ns = 0
        self.completed_count = 0
        self._has_run = False

    def schedule(self, time: int, priority: int, kind: str, payload: object) -> None:
        self.sequence += 1
        heappush(self.queue, (time, priority, self.sequence, kind, payload))

    def idle_memory(self) -> int:
        return sum(FUNCTIONS[c["function"]].memory for c in self.containers.values() if c["state"] == "IDLE")

    def active_memory(self) -> int:
        return sum(FUNCTIONS[c["function"]].memory for c in self.containers.values() if c["state"] in ("STARTING", "BUSY"))

    def snapshot(self) -> list[dict]:
        return [
            {"id": c["id"], "function": c["function"], "name": FUNCTIONS[c["function"]].name,
             "state": c["state"], "memory": FUNCTIONS[c["function"]].memory}
            for c in sorted(self.containers.values(), key=lambda x: x["id"])
            if c["state"] != "EVICTED"
        ]

    def log(self, kind: str, message: str, **details: object) -> None:
        self.events.append({"time": self.now, "kind": kind, "message": message,
                            "idle_mib": self.idle_memory(), "budget_mib": self.budget,
                            "containers": self.snapshot(), **details})

    def advance(self, time: int) -> None:
        self.idle_area += self.idle_memory() * (time - self.now)
        self.now = time

    def observe(self, function: str) -> None:
        h = self.history[function]
        h["q"] = h["q"] * exp(-(self.now - h["updated"]) / self.tau_ms) + 1
        h["updated"] = self.now
        h["count"] += 1

    def score(self, c: dict) -> tuple[float, float, float]:
        f = FUNCTIONS[c["function"]]
        h = self.history[c["function"]]
        intensity = h["q"] * exp(-(self.now - h["updated"]) / self.tau_ms) / self.tau_ms
        probability = 1 - exp(-intensity * self.horizon_ms)
        return (probability * f.startup / f.memory, probability, intensity)

    def eviction_key(self, c: dict) -> tuple:
        if self.policy in ("timeout", "lru"):
            return (c["last_used"], c["id"])
        if self.policy == "lfu":
            return (self.history[c["function"]]["count"], c["last_used"], c["id"])
        if self.policy == "faascache":
            return (self.gdsf_priority(c), c["last_used"], c["id"])
        return (self.score(c)[0], c["last_used"], c["id"])

    def gdsf_priority(self, c: dict) -> float:
        f = FUNCTIONS[c["function"]]
        return c["access_clock"] + self.gdsf_frequency[c["function"]] * f.startup / f.memory

    def enforce_budget(self) -> list[dict]:
        start = perf_counter_ns()
        self.decisions += 1
        removed = []
        while self.idle_memory() > self.budget:
            candidates = [c for c in self.containers.values() if c["state"] == "IDLE"]
            victim = min(candidates, key=self.eviction_key)
            score, probability, intensity = self.score(victim)
            priority = self.gdsf_priority(victim)
            victim["state"] = "EVICTED"
            victim["timer"] += 1
            self.evictions += 1
            if self.policy == "faascache":
                self.gdsf_clock = max(self.gdsf_clock, priority)
                reason = (f"Lowest paper-rule priority ({priority:.3f}): access clock plus "
                          "invocation frequency × startup cost ÷ memory. The logical clock advances after removal.")
                if not any(c["function"] == victim["function"] and c["state"] != "EVICTED"
                           for c in self.containers.values()):
                    self.gdsf_frequency[victim["function"]] = 0
            elif self.policy == "adaptive":
                reason = (f"Estimated {probability * 100:.1f}% chance of reuse soon; "
                          f"value score {score:.3f}. Lowest score was removed first.")
            elif self.policy == "lfu":
                reason = f"Its function had {self.history[victim['function']]['count']} observed requests, the lowest frequency."
            else:
                reason = "It was the least recently completed idle container."
            removed.append({"container_id": victim["id"], "function": victim["function"],
                            "score": round(score, 4), "reuse_probability": round(probability, 4),
                            "estimated_rate_per_ms": round(intensity, 6),
                            "gdsf_priority": round(priority, 4), "logical_clock": round(self.gdsf_clock, 4),
                            "reason": reason})
        self.peak_idle = max(self.peak_idle, self.idle_memory())
        self.decision_ns += perf_counter_ns() - start
        return removed

    def on_arrival(self, request: dict) -> None:
        key = request["function"]
        f = FUNCTIONS[key]
        self.observe(key)
        self.gdsf_frequency[key] += 1
        idle = [c for c in self.containers.values() if c["function"] == key and c["state"] == "IDLE"]
        if idle:
            c = min(idle, key=lambda item: item["id"])
            c["state"] = "BUSY"
            c["timer"] += 1
            cold = False
            self.schedule(self.now + f.execution, 0, "complete", (c["id"], request["id"]))
            message = f"{f.name} request {request['id']} reused ready container #{c['id']}. No startup wait."
        else:
            self.next_container_id += 1
            c = {"id": self.next_container_id, "function": key, "state": "STARTING",
                 "last_used": self.now, "timer": 0, "access_clock": self.gdsf_clock}
            self.containers[c["id"]] = c
            cold = True
            self.schedule(self.now + f.startup, 0, "ready", c["id"])
            self.schedule(self.now + f.startup + f.execution, 0, "complete", (c["id"], request["id"]))
            message = f"{f.name} request {request['id']} needed new container #{c['id']}: {f.startup} ms startup wait."
        c["access_clock"] = self.gdsf_clock
        self.requests[request["id"]] = {"id": request["id"], "function": key,
                                         "arrival": self.now, "cold": cold,
                                         "startup_ms": f.startup if cold else 0,
                                         "execution_ms": f.execution, "container_id": c["id"]}
        self.peak_active = max(self.peak_active, self.active_memory())
        self.log("cold" if cold else "warm", message, request_id=request["id"], function=key,
                 container_id=c["id"])

    def on_complete(self, container_id: int, request_id: str) -> None:
        c = self.containers[container_id]
        c["state"] = "IDLE"
        c["last_used"] = self.now
        c["timer"] += 1
        request = self.requests[request_id]
        request["completion"] = self.now
        request["latency_ms"] = self.now - request["arrival"]
        self.completed_count += 1
        if self.policy == "timeout":
            self.schedule(self.now + self.timeout_ms, 1, "timeout", (container_id, c["timer"]))
        removed = self.enforce_budget()
        retained = c["state"] == "IDLE"
        self.log("complete", f"{FUNCTIONS[c['function']].name} request {request_id} finished in "
                 f"{request['latency_ms']} ms. Container #{container_id} "
                 f"{'stays ready for another request' if retained else 'could not stay within the memory budget'}.",
                 request_id=request_id, function=c["function"], container_id=container_id,
                 retained=retained)
        for victim in removed:
            self.log("evict", f"Removed {FUNCTIONS[victim['function']].name} #{victim['container_id']} "
                     f"to free {FUNCTIONS[victim['function']].memory} MiB. {victim['reason']}",
                     **{key: value for key, value in victim.items() if key != "reason"})

    def on_timeout(self, container_id: int, token: int) -> None:
        c = self.containers[container_id]
        if c["state"] != "IDLE" or c["timer"] != token:
            return
        c["state"] = "EVICTED"
        c["timer"] += 1
        self.evictions += 1
        self.log("timeout", f"{FUNCTIONS[c['function']].name} #{container_id} was idle for "
                 f"{self.timeout_ms} ms, so its fixed timer expired.", container_id=container_id,
                 function=c["function"])

    def run(self) -> dict:
        if self._has_run:
            raise ValueError("Create a new simulator for each run")
        self._has_run = True
        for request in self.trace:
            self.schedule(request["arrival"], 2, "arrival", request)
        last_completion = 0
        while self.queue:
            time, _, _, kind, payload = heappop(self.queue)
            if self.completed_count == len(self.trace) and time > last_completion:
                break  # evaluation ends at the last request completion
            self.advance(time)
            if kind == "arrival":
                self.on_arrival(payload)
            elif kind == "ready":
                c = self.containers[payload]
                if c["state"] == "STARTING":
                    c["state"] = "BUSY"
            elif kind == "complete":
                self.on_complete(*payload)
                last_completion = max(last_completion, self.now)
            elif kind == "timeout":
                self.on_timeout(*payload)
            if self.timeline[-1]["idle_mib"] != self.idle_memory() or self.timeline[-1]["time"] != self.now:
                self.timeline.append({"time": self.now, "idle_mib": self.idle_memory()})
        requests = [self.requests[r["id"]] for r in self.trace]
        latencies = [r["latency_ms"] for r in requests]
        cold = sum(r["cold"] for r in requests)
        interval = last_completion
        # Presentation only: replay arrivals and group all intervening removals.
        # The full chronological event log remains available for research checks.
        demo_steps = []
        decisions = []
        for event in self.events:
            if event["kind"] in ("cold", "warm"):
                demo_steps.append({**event, "title": f"Student {len(demo_steps) + 1}: {FUNCTIONS[event['function']].name}",
                                   "decisions": decisions})
                decisions = []
            elif event["kind"] in ("evict", "timeout"):
                decisions.append(event["message"])
        if self.events:
            demo_steps.append({**self.events[-1], "kind": "finished", "title": "All students have been served",
                               "message": "Every request is finished. These are the containers still kept ready within the memory limit.",
                               "decisions": decisions})
        return {
            "policy": self.policy, "policy_name": POLICY_NAMES[self.policy],
            "summary": {"requests": len(requests), "cold_starts": cold,
                        "cold_start_pct": round(100 * cold / len(requests), 1) if requests else 0,
                        "warm_starts": len(requests) - cold,
                        "startup_overhead_ms": sum(r["startup_ms"] for r in requests),
                        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
                        "p95_latency_ms": percentile(latencies, .95),
                        "p99_latency_ms": percentile(latencies, .99),
                        "idle_mib_ms": self.idle_area,
                        "avg_idle_mib": round(self.idle_area / interval, 1) if interval else 0,
                        "peak_idle_mib": self.peak_idle, "peak_active_mib": self.peak_active,
                        "evictions": self.evictions, "policy_decisions": self.decisions,
                        "controller_ms": round(self.decision_ns / 1_000_000, 3),
                        "evaluation_ms": interval},
            "requests": requests, "events": self.events, "demo_steps": demo_steps,
            "memory_timeline": self.timeline,
        }


def compare(scenario: str = "shift", budget: int = 512) -> dict:
    require_integer(budget, "Idle memory budget")
    if budget not in (0, 128, 256, 384, 512, 768, 1024):
        raise ValueError("Budget must be one of 0, 128, 256, 384, 512, 768, or 1024 MiB")
    trace = workload(scenario)
    return {"scenario": scenario, "scenario_title": SCENARIOS[scenario]["title"],
        "scenario_description": SCENARIOS[scenario]["description"], "budget_mib": budget, "functions": {
        key: {"name": f.name, "memory_mib": f.memory, "startup_ms": f.startup,
              "execution_ms": f.execution} for key, f in FUNCTIONS.items()},
        "trace": trace, "runs": {p: Simulator(trace, p, budget).run() for p in POLICIES},
        "note": "All timings and workloads are simulated. Active memory is tracked separately from the idle retention budget."}
