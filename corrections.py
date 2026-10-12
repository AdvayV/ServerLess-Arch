"""Virtual-time simulator for exam correction and selective regrading."""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import ceil
from time import perf_counter_ns


QUESTIONS = ("q1", "q2", "q3", "q4")
SCHEDULERS = ("fifo", "edf", "fair_edf")
CACHE_POLICIES = ("lru", "gdsf")
STUDENT_COUNT = 16
GRADER_VERSION = "grade-v1"

SCENARIOS = {
    "single": {
        "title": "One answer key correction",
        "description": "One question changes. Eight affected submissions must be regraded while the other question results stay reusable.",
        "students": 8,
        "deadline_ms": 500,
        "releases": [0],
    },
    "rush": {
        "title": "Exam board correction rush",
        "description": "All 16 synthetic submissions depend on the corrected question and arrive in one burst with tight deadlines.",
        "students": 16,
        "deadline_ms": 300,
        "releases": [0],
    },
    "shift": {
        "title": "Two correction batches",
        "description": "A question is corrected, then corrected again while the first batch is still running. Old-version work must never publish.",
        "students": 10,
        "deadline_ms": 350,
        "releases": [0, 40],
    },
}

SCHEDULER_NAMES = {"fifo": "First in, first out", "edf": "Earliest deadline first",
                   "fair_edf": "Fair earliest deadline"}
CACHE_NAMES = {"lru": "LRU", "gdsf": "Cost-aware GDSF"}


def workload(name: str) -> dict:
    """Create the same hand-inspectable synthetic input for every policy."""
    if name not in SCENARIOS:
        raise ValueError(f"Unknown correction scenario: {name}")
    spec = SCENARIOS[name]
    corrections = []
    if name == "shift":
        corrections = [
            {"time": spec["releases"][0], "question": "q2", "version": "rubric-v2",
             "students": [f"S{i:02d}" for i in range(1, 11)]},
            {"time": spec["releases"][1], "question": "q2", "version": "rubric-v3",
             "students": [f"S{i:02d}" for i in range(1, 5)]},
        ]
    else:
        corrections = [{"time": 0, "question": "q2", "version": "rubric-v2",
                        "students": [f"S{i:02d}" for i in range(1, spec["students"] + 1)]}]
    return {
        "name": name,
        "title": spec["title"],
        "description": spec["description"],
        "worker_count": 2,
        "cache_capacity": 48,
        "questions": list(QUESTIONS),
        "corrections": corrections,
    }


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, ceil(p * len(ordered)) - 1)]


@dataclass
class _Task:
    id: str
    student: str
    question: str
    version: str
    release: int
    deadline: int
    duration: int
    sequence: int


class _ResultCache:
    """Version-keyed component cache with LRU and GDSF-like eviction."""

    def __init__(self, capacity: int, policy: str):
        self.capacity = capacity
        self.policy = policy
        self.entries: dict[tuple, dict] = {}
        self.frequency: dict[tuple, int] = {}
        self.access_clock: dict[tuple, float] = {}
        self.logical_clock = 0.0
        self.access_sequence = 0
        self.hits = 0
        self.misses = 0
        self.invalidations = 0
        self.evictions = 0
        self.admissions = 0

    @staticmethod
    def key(student: str, question: str, rubric_version: str) -> tuple:
        return ("exam-1", student, "submission-v1", question, rubric_version, GRADER_VERSION)

    def get(self, key: tuple) -> bool:
        entry = self.entries.get(key)
        if entry is None:
            self.misses += 1
            return False
        self.hits += 1
        self.access_sequence += 1
        entry["last"] = self.access_sequence
        entry["accesses"] += 1
        self.frequency[key] = entry["accesses"]
        self.access_clock[key] = self.logical_clock
        return True

    def invalidate(self, question: str, rubric_version: str, students: set[str]) -> int:
        removed = [key for key in self.entries
                   if key[1] in students and key[3] == question and key[4] == rubric_version]
        for key in removed:
            self.entries.pop(key)
            self.frequency.pop(key, None)
            self.access_clock.pop(key, None)
        self.invalidations += len(removed)
        return len(removed)

    def _priority(self, key: tuple) -> float:
        entry = self.entries[key]
        return self.access_clock[key] + self.frequency[key] * entry["cost_ms"]

    def put(self, key: tuple, cost_ms: int, now: int) -> None:
        if self.capacity <= 0:
            return
        if key in self.entries:
            self.access_sequence += 1
            self.entries[key].update(value_version=key[4], last=self.access_sequence)
            return
        while len(self.entries) >= self.capacity:
            if self.policy == "lru":
                victim = min(self.entries, key=lambda item: (self.entries[item]["last"], item))
            else:
                victim = min(self.entries, key=lambda item: (self._priority(item), item))
                self.logical_clock = max(self.logical_clock, self._priority(victim))
            self.entries.pop(victim)
            self.frequency.pop(victim, None)
            self.access_clock.pop(victim, None)
            self.evictions += 1
        self.access_sequence += 1
        self.entries[key] = {"value_version": key[4], "last": self.access_sequence, "accesses": 1,
                             "cost_ms": cost_ms, "size_units": 1}
        self.frequency[key] = 1
        self.access_clock[key] = self.logical_clock
        self.admissions += 1


class CorrectionSimulator:
    def __init__(self, scenario: str, scheduler: str, cache_policy: str):
        self.input = workload(scenario)
        if scheduler not in SCHEDULERS:
            raise ValueError(f"Unknown scheduler: {scheduler}")
        if cache_policy not in CACHE_POLICIES:
            raise ValueError(f"Unknown cache policy: {cache_policy}")
        self.scheduler = scheduler
        self.cache_policy = cache_policy
        self.worker_count = self.input["worker_count"]
        self.cache = _ResultCache(self.input["cache_capacity"], cache_policy)
        self.now = 0
        self.sequence = 0
        self.events: list[tuple] = []
        self.ready: list[_Task] = []
        self.active: dict[int, _Task] = {}
        self.active_keys: set[tuple[str, str]] = set()
        self.deferred: dict[tuple[str, str], list[_Task]] = {}
        self.current_version: dict[tuple[str, str], str] = {
            (f"S{i:02d}", question): "rubric-v1"
            for i in range(1, STUDENT_COUNT + 1) for question in QUESTIONS
        }
        self.virtual_finish: dict[str, float] = {}
        self.task_results: dict[str, dict] = {}
        self.log: list[dict] = []
        self.policy_ns = 0
        self.recompute_count = 0
        self.discarded_stale = 0
        self.tasks_created = 0
        self.worker_busy_ms = [0] * self.worker_count
        for student_index in range(1, STUDENT_COUNT + 1):
            student = f"S{student_index:02d}"
            for question in QUESTIONS:
                key = self.cache.key(student, question, "rubric-v1")
                self.cache.put(key, 50, 0)
        for correction in self.input["corrections"]:
            self._schedule(correction["time"], 1, "correction", correction)

    def _schedule(self, time: int, priority: int, kind: str, payload: object) -> None:
        self.sequence += 1
        heappush(self.events, (time, priority, self.sequence, kind, payload))

    def _log(self, kind: str, message: str, **data: object) -> None:
        self.log.append({"time": self.now, "kind": kind, "message": message, **data})

    def _enqueue(self, student: str, question: str, version: str, release: int, deadline: int) -> None:
        self.sequence += 1
        self.tasks_created += 1
        idx = int(student[1:])
        task = _Task(f"{question}-{student}-{version}", student, question, version,
                     release, deadline, 45 + (idx % 4) * 20, self.sequence)
        identity = (student, question)
        if identity in self.active_keys:
            self.deferred.setdefault(identity, []).append(task)
        else:
            self.ready.append(task)

    def _correction(self, correction: dict) -> None:
        question, new_version = correction["question"], correction["version"]
        old_versions: dict[str, set[str]] = {}
        for student in correction["students"]:
            old = self.current_version[(student, question)]
            old_versions.setdefault(old, set()).add(student)
            self.current_version[(student, question)] = new_version
        invalidated = sum(self.cache.invalidate(question, version, students)
                          for version, students in old_versions.items())
        self._log("correction", f"{question} rubric changed to {new_version}; invalidated {invalidated} cached components.",
                  question=question, old_versions=sorted(old_versions), rubric_version=new_version,
                  affected_students=list(correction["students"]), invalidated=invalidated)
        spec = SCENARIOS[self.input["name"]]
        for student in correction["students"]:
            offset = (120, 0, -80, 60)[(int(student[1:]) - 1) % 4]
            deadline = correction["time"] + spec["deadline_ms"] + offset
            self._enqueue(student, question, new_version, correction["time"], deadline)

    def _priority(self, task: _Task) -> tuple:
        age = max(0, self.now - task.release)
        remaining = max(0, task.deadline - self.now)
        if self.scheduler == "fifo":
            return (task.release, task.sequence, task.id)
        if self.scheduler == "edf":
            return (task.deadline, task.release, task.sequence, task.id)
        # Equal-weight per-student virtual finish, with age reducing deadline pressure.
        finish = self.virtual_finish.get(task.student, 0.0)
        urgency = remaining + finish - age * 0.5
        return (urgency, task.deadline, task.release, task.sequence, task.id)

    def _dispatch(self) -> None:
        for worker in range(self.worker_count):
            if worker in self.active:
                continue
            choices = [task for task in self.ready
                       if (task.student, task.question) not in self.active_keys]
            if not choices:
                continue
            start = perf_counter_ns()
            task = min(choices, key=self._priority)
            self.policy_ns += perf_counter_ns() - start
            self.ready.remove(task)
            selected_priority = self._priority(task)
            identity = (task.student, task.question)
            self.active_keys.add(identity)
            if self.scheduler == "fair_edf":
                self.virtual_finish[task.student] = self.virtual_finish.get(task.student, 0.0) + task.duration
            self.active[worker] = task
            wait = self.now - task.release
            self._log("dispatch", f"Worker {worker + 1} started {task.id} after {wait} ms in queue.",
                      task_id=task.id, student=task.student, question=task.question,
                      rubric_version=task.version, worker=worker + 1, queue_wait_ms=wait,
                      deadline=task.deadline, priority=list(selected_priority))
            self._schedule(self.now + task.duration, 0, "complete", worker)

    def _complete(self, worker: int) -> None:
        task = self.active.pop(worker)
        identity = (task.student, task.question)
        self.active_keys.discard(identity)
        self.worker_busy_ms[worker] += task.duration
        current = self.current_version[identity]
        if task.version == current:
            key = self.cache.key(task.student, task.question, task.version)
            self.cache.put(key, task.duration, self.now)
            self.recompute_count += 1
            self.task_results[task.id] = {"status": "published", "completion": self.now,
                                          "release": task.release, "deadline": task.deadline,
                                          "version": task.version, "student": task.student,
                                          "question": task.question}
            self._log("publish", f"Published corrected result for {task.student}, {task.question}, {task.version}.",
                      task_id=task.id, student=task.student, question=task.question,
                      rubric_version=task.version, deadline_missed=self.now > task.deadline)
        else:
            self.discarded_stale += 1
            self.task_results[task.id] = {"status": "discarded_stale", "completion": self.now,
                                          "release": task.release, "deadline": task.deadline,
                                          "version": task.version, "student": task.student,
                                          "question": task.question}
            self._log("stale", f"Discarded {task.id}; {current} is now current for {task.student}.",
                      task_id=task.id, student=task.student, question=task.question,
                      rubric_version=task.version, current_version=current)
            newer = [t for t in self.deferred.get(identity, [])
                     if t.version == current]
            self.deferred[identity] = [t for t in self.deferred.get(identity, [])
                                       if t.version != current]
            if newer:
                self.ready.extend(newer)
        deferred = self.deferred.pop(identity, [])
        if deferred:
            latest = max(deferred, key=lambda item: (item.release, item.sequence))
            if latest.version == self.current_version[identity]:
                self.ready.append(latest)
        self._dispatch()

    def _consume_unchanged_components(self, correction: dict) -> None:
        question = correction["question"]
        for student in correction["students"]:
            for unchanged in QUESTIONS:
                if unchanged != question:
                    key = self.cache.key(student, unchanged, self.current_version[(student, unchanged)])
                    self.cache.get(key)

    def run(self) -> dict:
        while self.events:
            time, _, _, kind, payload = heappop(self.events)
            self.now = time
            if kind == "complete":
                self._complete(payload)
            elif kind == "correction":
                self._correction(payload)
                self._consume_unchanged_components(payload)
                self._dispatch()
        completed = [r for r in self.task_results.values() if r["status"] == "published"]
        latencies = [r["completion"] - r["release"] for r in completed]
        deadlines_missed = sum(r["completion"] > r["deadline"] for r in completed)
        total_tasks = self.tasks_created
        wait_events = [event["queue_wait_ms"] for event in self.log if event["kind"] == "dispatch"]
        return {
            "scenario": self.input["name"], "scenario_title": self.input["title"],
            "scheduler": self.scheduler, "scheduler_name": SCHEDULER_NAMES[self.scheduler],
            "cache_policy": self.cache_policy, "cache_policy_name": CACHE_NAMES[self.cache_policy],
            "input": self.input,
            "summary": {
                "workers": self.worker_count, "tasks_created": total_tasks,
                "published": len(completed), "discarded_stale": self.discarded_stale,
                "deadline_misses": deadlines_missed,
                "deadline_miss_pct": round(100 * deadlines_missed / len(completed), 1) if completed else 0,
                "avg_completion_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
                "p95_completion_ms": _percentile(latencies, .95),
                "avg_queue_wait_ms": round(sum(wait_events) / len(wait_events), 1) if wait_events else 0,
                "cache_capacity": self.cache.capacity, "cache_entries": len(self.cache.entries),
                "cache_hits": self.cache.hits, "cache_misses": self.cache.misses,
                "cache_invalidations": self.cache.invalidations,
                "cache_evictions": self.cache.evictions, "cache_admissions": self.cache.admissions,
                "recomputed_components": self.recompute_count,
                "worker_busy_ms": self.worker_busy_ms,
                "scheduler_overhead_ms": round(self.policy_ns / 1_000_000, 3),
            },
            "results": self.task_results,
            "events": self.log,
            "note": "Synthetic simulation only. It does not grade real students or measure real learning outcomes.",
        }


def compare(scenario: str = "single") -> dict:
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown correction scenario: {scenario}")
    runs = {}
    for scheduler in SCHEDULERS:
        for cache_policy in CACHE_POLICIES:
            key = f"{scheduler}_{cache_policy}"
            runs[key] = CorrectionSimulator(scenario, scheduler, cache_policy).run()
    return {"scenario": scenario, "scenario_title": SCENARIOS[scenario]["title"],
            "scenario_description": SCENARIOS[scenario]["description"],
            "input": workload(scenario), "runs": runs}
