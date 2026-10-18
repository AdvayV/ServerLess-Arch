"""Behavioral checks for real result reuse and consistent case publication."""

from copy import deepcopy
import random

import pytest

from aid_domain import CHECKS, ComponentKey, CheckResult
from reassessment import ReassessmentSimulator, SCENARIOS, SCHEDULERS, STRATEGIES, compare, workload
from result_cache import ResultCache


def request(identity="r1", time=0, student="S01", deadline=1000):
    return {"kind": "request", "time": time, "id": identity, "student": student, "deadline": deadline}


def update(time, version, value, check="academic", student="S01"):
    return {"kind": "record_update", "time": time, "students": [student],
            "check": check, "version": version, "value": value}


def fixture(events, costs=None):
    return {"students": {"S01": {"academic": 795, "income": 280000, "evidence": True}},
            "rules": {"academic": 800, "income": 300000, "evidence": True},
            "costs": costs or {"academic": 40, "income": 100, "evidence": 60}, "events": events}


def independently_check_publications(spec, run):
    """Reconstruct values at publication without using the production oracle."""
    for case in run["cases"]:
        if case["status"] != "published":
            continue
        record = dict(spec["students"][case["student"]])
        rules = dict(spec["rules"])
        versions = {c: 1 for c in CHECKS}
        rule_versions = dict(versions)
        for event in sorted(spec["events"], key=lambda e: e["time"]):
            if event["time"] > case["completion"]:
                break
            if event["kind"] == "rule_update":
                rules[event["check"]] = event["value"]
                rule_versions[event["check"]] = event["version"]
            elif event["kind"] == "record_update" and case["student"] in event["students"]:
                record[event["check"]] = event["value"]
                versions[event["check"]] = event["version"]
        expected = {"academic": record["academic"] >= rules["academic"],
                    "income": record["income"] <= rules["income"],
                    "evidence": record["evidence"] == rules["evidence"]}
        for check in CHECKS:
            assert case["result"]["checks"][check]["passed"] is expected[check]
            assert case["result"]["checks"][check]["observed"] == record[check]
            assert case["result"]["checks"][check]["requirement"] == rules[check]
            assert case["targets"][check]["record_version"] == versions[check]
            assert case["targets"][check]["rule_version"] == rule_versions[check]
        assert case["result"]["all_checks_passed"] is all(expected.values())


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("scheduler", SCHEDULERS)
@pytest.mark.parametrize("strategy", STRATEGIES)
def test_every_strategy_publishes_complete_consistent_cases(scenario, scheduler, strategy):
    spec = workload(scenario)
    run = ReassessmentSimulator(spec, scheduler, strategy).run()
    independently_check_publications(spec, run)
    summary = run["summary"]
    assert summary["published"] == sum(e["kind"] == "request" for e in spec["events"])
    assert summary["unfinished"] == 0
    assert summary["jobs_created"] == sum(summary[k] for k in ("jobs_completed", "jobs_discarded_stale", "jobs_cancelled"))
    assert summary["compute_ms"] == sum(j["duration"] for j in run["jobs"] if j["start"] is not None)
    assert summary["peak_cache_entries"] <= summary["cache_capacity"]


def test_cache_reuse_reduces_actual_worker_computation():
    data = compare("reuse")
    cached = data["runs"]["edf_lru"]
    uncached = data["runs"]["edf_no_cache"]
    assert cached["summary"]["compute_ms"] == 800
    assert uncached["summary"]["compute_ms"] == 1600
    assert cached["summary"]["jobs_created"] == 12
    assert cached["summary"]["cache_hits"] == 12
    assert [c["result"] for c in cached["cases"]] == [c["result"] for c in uncached["cases"]]
    assert all(c["latency_ms"] == 0 for c in cached["cases"][4:])


@pytest.mark.parametrize("capacity", [0, 1, 4, 12])
def test_eviction_changes_work_without_changing_results(capacity):
    data = compare("reuse", cache_capacity=capacity)
    expected = data["runs"]["edf_full_recompute"]["cases"]
    for method in ("edf_lru", "edf_gdsf"):
        cases = data["runs"][method]["cases"]
        assert [c["result"] for c in cases] == [c["result"] for c in expected]
        if capacity == 0:
            assert data["runs"][method]["summary"]["compute_ms"] == 1600


@pytest.mark.parametrize("capacity", [0, 1])
def test_delivered_values_survive_cache_eviction(capacity):
    run = ReassessmentSimulator(fixture([request()]), worker_count=1, cache_capacity=capacity).run()
    assert run["summary"]["compute_ms"] == 200
    assert run["summary"]["peak_live_accumulator_components"] == 3
    assert len(run["cases"][0]["result"]["checks"]) == 3
    if capacity:
        assert run["summary"]["cache_evictions"] == 2


def test_failed_and_zero_valued_checks_are_reusable_results():
    spec = fixture([request(), request("r2", 600)])
    spec["students"]["S01"] = {"academic": 0, "income": 0, "evidence": False}
    run = ReassessmentSimulator(spec).run()
    assert run["summary"]["jobs_created"] == 3
    assert run["summary"]["cache_hits"] == 3
    assert run["cases"][1]["result"]["checks"]["academic"]["observed"] == 0
    assert run["cases"][1]["result"]["checks"]["evidence"]["passed"] is False


def test_overlapping_requests_share_exact_key_jobs():
    spec = fixture([request(), request("r2", 1)])
    shared = ReassessmentSimulator(spec, strategy="no_cache").run()
    full = ReassessmentSimulator(spec, strategy="full_recompute").run()
    assert shared["summary"]["jobs_created"] == 3
    assert shared["summary"]["shared_subscriptions"] == 3
    assert shared["summary"]["compute_ms"] == 200
    assert full["summary"]["jobs_created"] == 6
    assert full["summary"]["compute_ms"] == 400


def test_cancel_one_subscriber_does_not_cancel_shared_work():
    spec = fixture([request(), request("r2", 1), {"kind": "cancel", "time": 10, "id": "r1"}])
    run = ReassessmentSimulator(spec, strategy="no_cache").run()
    first, second = run["cases"]
    assert first["status"] == "cancelled" and first["result"] is None and first["latency_ms"] is None
    assert second["status"] == "published"
    assert run["summary"]["jobs_cancelled"] == 0
    assert run["summary"]["compute_ms"] == 200
    assert run["summary"]["published"] == run["summary"]["cancelled_requests"] == 1
    independently_check_publications(spec, run)


def test_last_subscriber_cancels_only_queued_work():
    spec = fixture([request(), {"kind": "cancel", "time": 10, "id": "r1"}])
    run = ReassessmentSimulator(spec, worker_count=1).run()
    assert run["summary"]["jobs_cancelled"] == 2
    assert run["summary"]["unused_current_compute_ms"] == 40
    assert run["summary"]["published"] == 0
    assert run["summary"]["deadline_misses"] == 0


def test_updates_win_over_same_time_completions_and_preserve_deadlines():
    spec = fixture([request(deadline=50), update(40, 2, 810)], costs=dict.fromkeys(CHECKS, 40))
    run = ReassessmentSimulator(spec, scheduler="fifo", worker_count=3, cache_capacity=0).run()
    case = run["cases"][0]
    assert case["generation"] == 2 and case["completion"] == 80
    assert case["deadline"] == 50 and case["deadline_missed"] is True
    assert run["summary"]["jobs_discarded_stale"] == 1
    assert run["summary"]["carried_components"] == 2
    assert case["targets"]["academic"]["record_version"] == 2
    assert not any(e["kind"] == "publish" and e["time"] == 40 for e in run["events"])


def test_same_time_requests_observe_updates_regardless_of_input_order():
    events = [request(), update(0, 2, 810)]
    forward = ReassessmentSimulator(fixture(events)).run()
    reverse = ReassessmentSimulator(fixture(list(reversed(events)))).run()
    assert forward["cases"] == reverse["cases"]
    assert forward["cases"][0]["generation"] == 1
    assert forward["cases"][0]["result"]["all_checks_passed"] is True


def test_queued_supersession_keeps_history_unique_and_unchanged_work_alive():
    spec = fixture([request(), update(10, 2, 810), update(20, 3, 820)])
    run = ReassessmentSimulator(spec, worker_count=1, scheduler="fifo", cache_capacity=0).run()
    assert run["cases"][0]["generation"] == 3
    assert run["summary"]["jobs_cancelled"] == 1
    assert run["summary"]["jobs_discarded_stale"] == 1
    assert run["summary"]["jobs_completed"] == 3
    assert len({j["id"] for j in run["jobs"]}) == len(run["jobs"])
    assert run["summary"]["shared_subscriptions"] == 0
    independently_check_publications(spec, run)


def test_duplicate_notices_do_not_create_new_generations_or_jobs():
    base = fixture([request(), update(10, 2, 810)])
    duplicate = deepcopy(base)
    duplicate["events"].append(update(20, 2, 810))
    a, b = [ReassessmentSimulator(s, worker_count=1).run() for s in (base, duplicate)]
    assert a["cases"] == b["cases"]
    assert a["summary"]["jobs_created"] == b["summary"]["jobs_created"]


def test_rule_change_invalidates_only_that_check_and_marks_historical_results():
    spec = fixture([request(), {"kind": "rule_update", "time": 500, "check": "academic", "version": 2, "value": 790},
                    request("r2", 600)])
    run = ReassessmentSimulator(spec).run()
    assert run["summary"]["jobs_created"] == 4
    assert run["summary"]["cache_invalidations"] == 1
    assert run["cases"][0]["current_at_end"] is False
    assert run["cases"][1]["result"]["all_checks_passed"] is True
    independently_check_publications(spec, run)


def test_deterministic_runs_do_not_mutate_the_input_and_cannot_be_reused():
    spec = workload("amendments")
    original = deepcopy(spec)
    engine = ReassessmentSimulator(spec)
    first = engine.run()
    assert spec == original
    assert first == ReassessmentSimulator(spec).run()
    with pytest.raises(ValueError, match="fresh"):
        engine.run()


@pytest.mark.parametrize("policy", ["lru", "gdsf"])
def test_result_cache_overwrite_updates_value_and_cost_without_eviction(policy):
    cache = ResultCache(1, policy)
    key = ComponentKey("S01", "academic", 1, 1)
    cache.put(key, CheckResult(False, 0, 800, "old"), 40)
    cache.put(key, CheckResult(True, 810, 800, "new"), 100)
    assert cache.get(key).observed == 810
    assert cache.entries[key].cost == 100
    assert cache.evictions == 0


def test_gdsf_retains_expensive_results_when_lru_evicts_them():
    keys = [ComponentKey("S01", c, 1, 1) for c in CHECKS]
    victim_sets = []
    for policy in ("lru", "gdsf"):
        cache = ResultCache(2, policy)
        for key, cost in zip(keys, [100, 1, 1]):
            cache.put(key, CheckResult(True, 1, 1, "demo"), cost)
        victim_sets.append(set(cache.entries))
    assert keys[0] not in victim_sets[0] and keys[0] in victim_sets[1]


@pytest.mark.parametrize("options", [{"worker_count": True}, {"worker_count": 0},
                                    {"worker_count": 33}, {"cache_capacity": -1},
                                    {"cache_capacity": 4097}, {"cache_capacity": 1.5}])
def test_invalid_resources_are_rejected(options):
    with pytest.raises(ValueError):
        ReassessmentSimulator(fixture([request()]), **options)


@pytest.mark.parametrize("events", [
    [request(), request()],
    [request(), update(10, 1, 810)],
    [request(), update(10, 3, 810), update(20, 2, 820)],
    [{"kind": "cancel", "time": 1, "id": "missing"}],
    [request(time=20), {"kind": "cancel", "time": 1, "id": "r1"}],
])
def test_invalid_request_ids_versions_and_cancellations_are_rejected(events):
    with pytest.raises(ValueError):
        ReassessmentSimulator(fixture(events)).run()


@pytest.mark.parametrize("seed", range(8))
def test_generated_update_traces_match_independent_oracle_across_resources(seed):
    rng = random.Random(seed)
    events = [request(f"r{i}", i * 17, deadline=150 + i * 19) for i in range(8)]
    for version, time in enumerate([20, 60, 100, 140], start=2):
        events.append(update(time, version, rng.randrange(750, 880)))
    spec = fixture(events)
    for strategy in STRATEGIES:
        run = ReassessmentSimulator(spec, scheduler=SCHEDULERS[seed % 3], strategy=strategy,
                                    worker_count=1 + seed % 3, cache_capacity=seed % 4).run()
        independently_check_publications(spec, run)
        assert run["summary"]["unfinished"] == 0
        assert all(j["status"] in ("completed", "discarded_stale", "cancelled") for j in run["jobs"])
