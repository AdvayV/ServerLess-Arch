"""Dependency-driven financial-aid reassessment in deterministic virtual time.

Cache misses create work. Exact-key subscribers share jobs. Completed component
values belong to a snapshot accumulator even after cache eviction. Corrections
take effect before same-time completions, planning, publication and dispatch.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from heapq import heappop, heappush
import re

from aid_domain import (CHECKS, DEFAULT_COSTS, DEFAULT_RULES, CheckResult,
                        ComponentKey, evaluate_check, reference_evaluate, validate_value)
from result_cache import ResultCache
from simulation_utils import percentile, require_integer


SCHEDULERS = ("fifo", "edf", "fair_edf")
STRATEGIES = ("lru", "gdsf", "no_cache", "full_recompute")
STRATEGY_NAMES = {"lru": "LRU result cache", "gdsf": "Cost-aware result cache",
                  "no_cache": "No result cache", "full_recompute": "Full recomputation"}
SCENARIOS = {
    "reuse": ("Reuse unchanged checks", "Repeated case requests can reuse actual check results."),
    "amendments": ("Overlapping updates", "Grades, evidence and a demo rule change while shared work is running."),
    "pressure": ("Limited result cache", "Repeated requests for eight students compete for result-cache slots."),
}


def workload(name: str) -> dict:
    if name not in SCENARIOS:
        raise ValueError(f"Unknown reassessment scenario: {name}")
    count = 8 if name == "pressure" else (2 if name == "amendments" else 4)
    students = {
        f"S{i:02d}": {"academic": 795 + 10 * ((i - 1) % 4),
                       "income": 280_000 + 15_000 * ((i - 1) % 4), "evidence": i % 3 != 0}
        for i in range(1, count + 1)
    }
    events = []
    for i, student in enumerate(students):
        time = i * 10 if name == "pressure" else 0
        events.append({"kind": "request", "time": time, "id": f"{student}-first",
                       "student": student, "deadline": time + (260 if name == "amendments" else 1200)})
    if name == "amendments":
        events.extend([
            {"kind": "request", "time": 5, "id": "S01-second", "student": "S01", "deadline": 200},
            {"kind": "record_update", "time": 40, "students": ["S01"], "check": "academic", "version": 2, "value": 810},
            {"kind": "rule_update", "time": 80, "check": "academic", "version": 2, "value": 820},
            {"kind": "record_update", "time": 120, "students": ["S01"], "check": "academic", "version": 3, "value": 840},
            {"kind": "record_update", "time": 140, "students": ["S02"], "check": "evidence", "version": 2, "value": False},
        ])
    else:
        for i, student in enumerate(students):
            time = (1500 + i * 10) if name == "pressure" else 600
            events.append({"kind": "request", "time": time, "id": f"{student}-second",
                           "student": student, "deadline": time + 1200})
    return {"name": name, "title": SCENARIOS[name][0], "description": SCENARIOS[name][1],
            "students": students, "rules": dict(DEFAULT_RULES), "costs": dict(DEFAULT_COSTS), "events": events}


def _validate(spec: dict) -> None:
    if not isinstance(spec, dict) or not isinstance(spec.get("students"), dict) or not spec["students"]:
        raise ValueError("Supply at least one synthetic student")
    if not isinstance(spec.get("events"), list):
        raise ValueError("Events must be a list")
    if len(spec["students"]) > 100 or len(spec["events"]) > 5000:
        raise ValueError("Workload exceeds the local simulation limit")
    for student, record in spec["students"].items():
        if not isinstance(student, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", student):
            raise ValueError("Student IDs must use 1-64 letters, digits, underscores or hyphens")
        if not isinstance(record, dict) or set(record) != set(CHECKS):
            raise ValueError("Each student must have academic, income and evidence values")
        for check, value in record.items():
            validate_value(check, value)
    for field_name in ("rules", "costs"):
        if not isinstance(spec.get(field_name), dict) or set(spec[field_name]) != set(CHECKS):
            raise ValueError(f"Supply all three {field_name}")
    for check in CHECKS:
        validate_value(check, spec["rules"][check])
        require_integer(spec["costs"][check], "Check duration", 1)
    request_ids = {}
    cancellations = []
    for event in spec["events"]:
        if not isinstance(event, dict):
            raise ValueError("Events must be objects")
        require_integer(event.get("time"), "Event time")
        kind = event.get("kind")
        if kind == "request":
            if set(event) != {"kind", "time", "id", "student", "deadline"}:
                raise ValueError("A request needs time, id, student and deadline")
            identity = event["id"]
            if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity):
                raise ValueError("Request IDs must use 1-64 letters, digits, underscores or hyphens")
            if identity in request_ids:
                raise ValueError("Request IDs must be unique")
            request_ids[identity] = event["time"]
            if not isinstance(event["student"], str) or event["student"] not in spec["students"]:
                raise ValueError("Unknown request student")
            require_integer(event["deadline"], "Absolute deadline", event["time"])
        elif kind == "cancel":
            if set(event) != {"kind", "time", "id"} or not isinstance(event["id"], str):
                raise ValueError("A cancellation needs time and request id")
            cancellations.append(event)
        elif kind in ("record_update", "rule_update"):
            required = {"kind", "time", "check", "version", "value"}
            if kind == "record_update":
                required.add("students")
            if set(event) != required:
                raise ValueError("Invalid update fields")
            validate_value(event["check"], event["value"])
            require_integer(event["version"], "Version", 1)
            if kind == "record_update":
                students = event["students"]
                if not isinstance(students, list) or not students or any(not isinstance(s, str) for s in students):
                    raise ValueError("Update students must be a nonempty list of IDs")
                if len(students) != len(set(students)) or any(s not in spec["students"] for s in students):
                    raise ValueError("Update students must be unique known IDs")
        else:
            raise ValueError(f"Unknown event kind: {kind}")
    if any(e["id"] not in request_ids or e["time"] < request_ids[e["id"]] for e in cancellations):
        raise ValueError("A cancellation must name a request released by that time")


@dataclass
class _Snapshot:
    id: str
    request_id: str
    student: str
    generation: int
    created: int
    targets: dict[str, ComponentKey]
    values: dict[str, CheckResult] = field(default_factory=dict)
    waiting: dict[str, str] = field(default_factory=dict)
    status: str = "pending"
    completion: int | None = None
    result: dict | None = None


@dataclass
class _Request:
    id: str
    student: str
    release: int
    deadline: int
    snapshot: str
    status: str = "pending"


@dataclass
class _Job:
    id: str
    key: ComponentKey
    observed: int | bool
    requirement: int | bool
    release: int
    duration: int
    sequence: int
    subscribers: set[str] = field(default_factory=set)
    status: str = "queued"
    start: int | None = None
    completion: int | None = None
    result: CheckResult | None = None
    deliveries: int = 0


class ReassessmentSimulator:
    def __init__(self, spec: dict, scheduler: str = "edf", strategy: str = "lru",
                 worker_count: int = 2, cache_capacity: int = 12):
        _validate(spec)
        if scheduler not in SCHEDULERS or strategy not in STRATEGIES:
            raise ValueError("Unknown scheduler or reassessment strategy")
        require_integer(worker_count, "Worker count", 1)
        require_integer(cache_capacity, "Cache capacity")
        if worker_count > 32 or cache_capacity > 4096:
            raise ValueError("Use at most 32 workers and 4096 result-cache slots")
        self.input = deepcopy(spec)
        self.scheduler, self.strategy = scheduler, strategy
        self.worker_count, self.configured_capacity = worker_count, cache_capacity
        self.cache = ResultCache(cache_capacity if strategy in ("lru", "gdsf") else 0,
                                 strategy if strategy in ("lru", "gdsf") else "lru")
        self.records = deepcopy(spec["students"])
        self.rules = dict(spec["rules"])
        self.versions = {(student, check): 1 for student in self.records for check in CHECKS}
        self.rule_versions = {check: 1 for check in CHECKS}
        self.now = self.sequence = 0
        self.events = []
        self.requests: dict[str, _Request] = {}
        self.snapshots: dict[str, _Snapshot] = {}
        self.jobs: dict[str, _Job] = {}
        self.registry: dict[ComponentKey, str] = {}
        self.ready: list[str] = []
        self.active: dict[int, str] = {}
        self.service: dict[str, int] = {}
        self.worker_busy = [0] * worker_count
        self.log = []
        self.saved_compute = self.shared_compute = self.carried = self.peak_accumulator = 0
        self.shared_subscriptions = 0
        self.reattachments = 0
        self._has_run = False
        for event in spec["events"]:
            priority = {"request": 2, "cancel": 3}.get(event["kind"], 0)
            self._schedule(event["time"], priority,
                           event["kind"], deepcopy(event))

    def _schedule(self, time: int, priority: int, kind: str, payload) -> None:
        self.sequence += 1
        heappush(self.events, (time, priority, self.sequence, kind, payload))

    def _log(self, kind: str, message: str, **data) -> None:
        self.log.append({"time": self.now, "kind": kind, "message": message, **data})

    def _key(self, student: str, check: str) -> ComponentKey:
        return ComponentKey(student, check, self.versions[student, check], self.rule_versions[check])

    def _current(self, key: ComponentKey) -> bool:
        return key == self._key(key.student, key.check)

    def _new_snapshot(self, request_id: str, student: str, old: _Snapshot | None = None) -> _Snapshot:
        generation = old.generation + 1 if old else 1
        snapshot = _Snapshot(f"{request_id}:g{generation}", request_id, student, generation,
                             self.now, {check: self._key(student, check) for check in CHECKS})
        if old:
            old.status = "superseded"
            old.completion = self.now
            for job_id in old.waiting.values():
                self.jobs[job_id].subscribers.discard(old.id)
            if self.strategy != "full_recompute":
                snapshot.values = {check: value for check, value in old.values.items()
                                   if old.targets[check] == snapshot.targets[check]}
                self.carried += len(snapshot.values)
            self._log("superseded", f"{old.id} replaced by {snapshot.id}; the absolute deadline is preserved.",
                      snapshot=old.id, replacement=snapshot.id)
        self.snapshots[snapshot.id] = snapshot
        return snapshot

    def _request(self, event: dict) -> None:
        snapshot = self._new_snapshot(event["id"], event["student"])
        self.requests[event["id"]] = _Request(event["id"], event["student"], self.now,
                                               event["deadline"], snapshot.id)
        self._log("request", f"Requested {snapshot.id} for {snapshot.student}.",
                  request=event["id"], snapshot=snapshot.id, deadline=event["deadline"])

    def _cancel(self, event: dict) -> None:
        request = self.requests[event["id"]]
        if request.status != "pending":
            self._log("closed_request", f"{request.id} is already {request.status}.", request=request.id)
            return
        snapshot = self.snapshots[request.snapshot]
        snapshot.status = request.status = "cancelled"
        snapshot.completion = self.now
        for job_id in snapshot.waiting.values():
            self.jobs[job_id].subscribers.discard(snapshot.id)
        self._log("cancel_request", f"Cancelled {request.id}; other subscribers keep their shared work.",
                  request=request.id, snapshot=snapshot.id)

    def _update(self, event: dict) -> None:
        check, version, value = event["check"], event["version"], event["value"]
        students = event.get("students", [])
        current = [(self.versions[s, check], self.records[s][check]) for s in students]
        if event["kind"] == "rule_update":
            current = [(self.rule_versions[check], self.rules[check])]
        if any(version < v or (version == v and value != old) for v, old in current):
            raise ValueError("An update must advance its version; repeated versions must have identical values")
        changed = False
        if event["kind"] == "rule_update":
            changed = version != self.rule_versions[check]
            self.rule_versions[check] = version
            self.rules[check] = value
        else:
            for student in students:
                changed |= version != self.versions[student, check]
                self.versions[student, check] = version
                self.records[student][check] = value
        self.cache.invalidate(self._key)
        self._log("update" if changed else "duplicate_update",
                  f"{event['kind']}: {check} version {version}" + (" applied." if changed else " already current."),
                  check=check, version=version, students=students)

    def _refresh(self) -> None:
        for request in self.requests.values():
            if request.status != "pending":
                continue
            old = self.snapshots[request.snapshot]
            if any(not self._current(key) for key in old.targets.values()):
                request.snapshot = self._new_snapshot(request.id, request.student, old).id

    def _plan(self) -> None:
        # Reattach unchanged work before canceling jobs whose former generation
        # lost its subscriber. Other live cases may still depend on that job.
        for request in self.requests.values():
            if request.status != "pending":
                continue
            snapshot = self.snapshots[request.snapshot]
            for check, key in snapshot.targets.items():
                if check in snapshot.values or check in snapshot.waiting:
                    continue
                result = self.cache.get(key)
                if result is not None:
                    snapshot.values[check] = result
                    self.saved_compute += self.input["costs"][check]
                    self._log("cache_hit", f"{snapshot.id} reused its {check} result.", snapshot=snapshot.id, check=check)
                    continue
                existing = self.registry.get(key) if self.strategy != "full_recompute" else None
                if existing:
                    job = self.jobs[existing]
                    sharing = bool(job.subscribers)
                    if sharing:
                        self.shared_subscriptions += 1
                        self.shared_compute += job.duration
                    else:
                        self.reattachments += 1
                    self._log("join" if sharing else "reattach", f"{snapshot.id} joined {job.id} for {check}.",
                              snapshot=snapshot.id, job=job.id)
                else:
                    self.sequence += 1
                    job = _Job(f"J{len(self.jobs) + 1:05d}", key, self.records[key.student][check], self.rules[check],
                               self.now, self.input["costs"][check], self.sequence)
                    self.jobs[job.id] = job
                    self.ready.append(job.id)
                    if self.strategy != "full_recompute":
                        self.registry[key] = job.id
                    self._log("cache_miss", f"{snapshot.id} needs {check}; created {job.id}.", snapshot=snapshot.id, job=job.id)
                job.subscribers.add(snapshot.id)
                snapshot.waiting[check] = job.id
        for job_id in list(self.ready):
            job = self.jobs[job_id]
            if not job.subscribers:
                self.ready.remove(job_id)
                job.status, job.completion = "cancelled", self.now
                if self.registry.get(job.key) == job_id:
                    del self.registry[job.key]
                self._log("cancelled", f"Cancelled queued {job_id}; no current case needs it.", job=job_id)
        self._storage()

    def _priority(self, job: _Job) -> tuple:
        deadline = min(self.requests[self.snapshots[s].request_id].deadline for s in job.subscribers)
        if self.scheduler == "fifo":
            return job.release, job.sequence
        if self.scheduler == "edf":
            return deadline, job.release, job.sequence
        urgency = max(0, deadline - self.now) + self.service.get(job.key.student, 0) - .5 * (self.now - job.release)
        return urgency, deadline, job.release, job.sequence

    def _dispatch(self) -> None:
        occupied = {(self.jobs[j].key.student, self.jobs[j].key.check) for j in self.active.values()}
        for worker in range(self.worker_count):
            if worker in self.active:
                continue
            choices = [self.jobs[j] for j in self.ready
                       if (self.jobs[j].key.student, self.jobs[j].key.check) not in occupied]
            if not choices:
                continue
            job = min(choices, key=self._priority)
            self.ready.remove(job.id)
            job.status, job.start = "running", self.now
            self.active[worker] = job.id
            occupied.add((job.key.student, job.key.check))
            self.service[job.key.student] = self.service.get(job.key.student, 0) + job.duration
            self._schedule(self.now + job.duration, 1, "complete", worker)
            self._log("dispatch", f"Worker {worker + 1} started {job.id}: {job.key.student} / {job.key.check}.",
                      job=job.id, worker=worker + 1, key=job.key.public())

    def _complete(self, worker: int) -> None:
        job = self.jobs[self.active.pop(worker)]
        job.completion = self.now
        self.worker_busy[worker] += job.duration
        if self.registry.get(job.key) == job.id:
            del self.registry[job.key]
        if not self._current(job.key):
            job.status = "discarded_stale"
            self._log("stale", f"Discarded {job.id}; its record or rule version has changed.", job=job.id)
            return
        job.status = "completed"
        result = evaluate_check(job.key.check, job.observed, job.requirement)
        job.result = result
        self.cache.put(job.key, result, job.duration)
        deliveries = 0
        for snapshot_id in sorted(job.subscribers):
            snapshot = self.snapshots[snapshot_id]
            request = self.requests[snapshot.request_id]
            if (snapshot.status == "pending" and request.snapshot == snapshot_id
                    and snapshot.targets[job.key.check] == job.key):
                snapshot.values[job.key.check] = result
                snapshot.waiting.pop(job.key.check, None)
                deliveries += 1
        job.deliveries = deliveries
        self._log("component", f"{job.id} completed {job.key.check}; delivered to {deliveries} case(s).",
                  job=job.id, subscribers=deliveries, result=result.public())
        self._storage()

    def _storage(self) -> None:
        live = sum(len(s.values) for s in self.snapshots.values() if s.status == "pending")
        self.peak_accumulator = max(self.peak_accumulator, live)

    def _publish(self) -> None:
        for request in self.requests.values():
            if request.status != "pending":
                continue
            snapshot = self.snapshots[request.snapshot]
            if len(snapshot.values) != len(CHECKS):
                continue
            if snapshot.status != "pending" or any(not self._current(k) for k in snapshot.targets.values()):
                raise RuntimeError("Attempted to publish a superseded snapshot")
            actual = {check: snapshot.values[check].passed for check in CHECKS}
            reference = reference_evaluate(self.records[request.student], self.rules)
            if actual != reference["checks"]:
                raise RuntimeError("Cached aggregate differs from the uncached reference evaluator")
            snapshot.result = {"checks": {c: snapshot.values[c].public() for c in CHECKS},
                               "passed_checks": sum(actual.values()), "all_checks_passed": all(actual.values()),
                               "reference_verified": True}
            snapshot.status = request.status = "published"
            snapshot.completion = self.now
            # Detach subscribers: a completed case must never keep an orphan job alive.
            for job in self.jobs.values():
                job.subscribers.discard(snapshot.id)
            self._log("publish", f"Published {snapshot.id}: {sum(actual.values())}/3 demo checks passed; ready for staff review.",
                      snapshot=snapshot.id, request=request.id, generation=snapshot.generation,
                      targets={c: k.public() for c, k in snapshot.targets.items()}, result=snapshot.result)

    def run(self) -> dict:
        if self._has_run:
            raise ValueError("Create a fresh reassessment simulator for every run")
        self._has_run = True
        while self.events:
            self.now = self.events[0][0]
            while self.events and self.events[0][0] == self.now:
                _, _, _, kind, event = heappop(self.events)
                if kind == "complete":
                    self._complete(event)
                elif kind == "request":
                    self._request(event)
                elif kind == "cancel":
                    self._cancel(event)
                else:
                    self._update(event)
            self._refresh()
            self._plan()
            self._publish()
            self._dispatch()
        if self.ready or self.active or any(r.status == "pending" for r in self.requests.values()):
            raise RuntimeError("Simulation ended with unresolved dependencies")
        return self._report()

    def _report(self) -> dict:
        cases = []
        for request in self.requests.values():
            snapshot = self.snapshots[request.snapshot]
            cases.append({"id": request.id, "student": request.student, "snapshot": snapshot.id,
                          "generation": snapshot.generation, "status": request.status,
                          "release": request.release, "deadline": request.deadline, "completion": snapshot.completion,
                          "latency_ms": snapshot.completion - request.release if request.status == "published" else None,
                          "amendment_latency_ms": snapshot.completion - snapshot.created if request.status == "published" else None,
                          "deadline_missed": request.status == "published" and snapshot.completion > request.deadline,
                          "current_at_end": all(self._current(key) for key in snapshot.targets.values()),
                          "targets": {c: k.public() for c, k in snapshot.targets.items()}, "result": snapshot.result})
        published = [case for case in cases if case["status"] == "published"]
        latencies = [case["latency_ms"] for case in published]
        waits = [j.start - j.release for j in self.jobs.values() if j.start is not None]
        counts = {status: sum(j.status == status for j in self.jobs.values())
                  for status in ("completed", "discarded_stale", "cancelled")}
        summary = {
            "requests": len(cases), "published": len(published), "unfinished": 0,
            "cancelled_requests": len(cases) - len(published),
            "deadline_misses": sum(c["deadline_missed"] for c in cases),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            "p95_latency_ms": percentile(latencies, .95), "evaluation_ms": self.now,
            "jobs_created": len(self.jobs), "jobs_completed": counts["completed"],
            "jobs_discarded_stale": counts["discarded_stale"], "jobs_cancelled": counts["cancelled"],
            "superseded_snapshots": sum(s.status == "superseded" for s in self.snapshots.values()),
            "compute_ms": sum(self.worker_busy), "worker_busy_ms": self.worker_busy,
            "stale_compute_ms": sum(j.duration for j in self.jobs.values() if j.status == "discarded_stale"),
            "unused_current_compute_ms": sum(j.duration for j in self.jobs.values()
                                             if j.status == "completed" and not j.deliveries),
            "cache_saved_compute_ms": self.saved_compute, "shared_subscriptions": self.shared_subscriptions,
            "shared_compute_ms": self.shared_compute, "reattached_components": self.reattachments,
            "carried_components": self.carried,
            "avg_queue_wait_ms": round(sum(waits) / len(waits), 2) if waits else 0,
            "cache_hits": self.cache.hits, "cache_misses": self.cache.misses,
            "cache_evictions": self.cache.evictions, "cache_invalidations": self.cache.invalidations,
            "cache_entries": len(self.cache.entries), "cache_capacity": self.cache.capacity,
            "peak_cache_entries": self.cache.peak_entries, "peak_live_accumulator_components": self.peak_accumulator,
            "reference_verified": all(c["result"]["reference_verified"] for c in published),
            "reference_verified_cases": len(published),
        }
        return {"scheduler": self.scheduler, "strategy": self.strategy, "strategy_name": STRATEGY_NAMES[self.strategy],
                "summary": summary, "cases": cases,
                "snapshots": [{"id": s.id, "request": s.request_id, "generation": s.generation,
                               "status": s.status, "created": s.created, "completion": s.completion,
                               "targets": {c: k.public() for c, k in s.targets.items()}} for s in self.snapshots.values()],
                "jobs": [{"id": j.id, "key": j.key.public(), "status": j.status, "release": j.release,
                          "start": j.start, "completion": j.completion, "duration": j.duration,
                          "deliveries": j.deliveries, "result": j.result.public() if j.result else None}
                         for j in self.jobs.values()],
                "events": self.log}


def compare(scenario: str = "amendments", worker_count: int = 2, cache_capacity: int = 12) -> dict:
    spec = workload(scenario)
    runs = {f"{scheduler}_{strategy}": ReassessmentSimulator(spec, scheduler, strategy, worker_count, cache_capacity).run()
            for scheduler in SCHEDULERS for strategy in STRATEGIES}
    return {"input": spec, "worker_count": worker_count, "cache_capacity": cache_capacity,
            "runs": runs, "notice": "Synthetic rules and records. Outputs support staff review, not award decisions."}
