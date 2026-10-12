"""Checks for scoped invalidation and publication under overlapping corrections."""

import pytest

from corrections import CACHE_POLICIES, SCHEDULERS, CorrectionSimulator, _ResultCache, compare


@pytest.mark.parametrize("scheduler", SCHEDULERS)
@pytest.mark.parametrize("cache_policy", CACHE_POLICIES)
def test_overlapping_corrections_complete_every_affected_student(scheduler, cache_policy):
    run = CorrectionSimulator("shift", scheduler, cache_policy).run()
    published = {(result["student"], result["question"]): result["version"]
                 for result in run["results"].values() if result["status"] == "published"}
    assert published == {
        (f"S{i:02d}", "q2"): "rubric-v3" if i <= 4 else "rubric-v2"
        for i in range(1, 11)
    }
    assert len(run["results"]) == run["summary"]["tasks_created"]
    summary = run["summary"]
    assert summary["recomputed_components"] == summary["published"] + summary["discarded_stale"]
    assert summary["tasks_created"] == summary["recomputed_components"] + summary["superseded_jobs"]
    running = set()
    for event in run["events"]:
        if event["kind"] == "dispatch":
            assert event["task_id"] not in running
            running.add(event["task_id"])
            assert len(running) <= run["summary"]["workers"]
        elif event["kind"] in ("publish", "stale"):
            running.remove(event["task_id"])
    assert not running


def test_invalidation_preserves_other_students_questions_and_versions():
    cache = _ResultCache(10, "lru")
    keys = [cache.key("S01", "q2", "rubric-v1"),
            cache.key("S02", "q2", "rubric-v1"),
            cache.key("S01", "q1", "rubric-v1"),
            cache.key("S01", "q2", "rubric-v2")]
    for key in keys:
        cache.put(key, 50)
    assert cache.invalidate("q2", "rubric-v1", {"S01"}) == 1
    assert not cache.get(keys[0])
    assert all(cache.get(key) for key in keys[1:])


def test_deadline_order_differs_from_fifo_on_identical_work():
    comparison = compare("rush")
    assert all(run["input"] == comparison["input"] for run in comparison["runs"].values())
    orders = {
        policy: [event["student"] for event in comparison["runs"][policy]["events"]
                 if event["kind"] == "dispatch"]
        for policy in ("fifo_lru", "edf_lru")
    }
    assert orders["fifo_lru"] != orders["edf_lru"]
    for run in comparison["runs"].values():
        assert run["summary"]["published"] == 16
        assert run["summary"]["cache_entries"] <= run["summary"]["cache_capacity"]


def test_cache_overwrite_refreshes_cost_and_priority():
    cache = _ResultCache(2, "gdsf")
    first = cache.key("S01", "q1", "rubric-v1")
    second = cache.key("S02", "q1", "rubric-v1")
    third = cache.key("S03", "q1", "rubric-v1")
    cache.put(first, 5)
    cache.put(second, 50)
    cache.put(first, 1000)
    cache.put(third, 50)
    assert cache.get(first)
    assert not cache.get(second)
    assert cache.get(third)


def test_queued_old_version_is_removed_before_same_time_dispatch():
    simulator = CorrectionSimulator("single", "fifo", "lru")
    simulator._schedule(65, 1, "correction", {
        "time": 65, "question": "q2", "version": "rubric-v3", "students": ["S03"],
    })
    run = simulator.run()
    old_id = "q2-S03-rubric-v2"
    assert run["results"][old_id]["status"] == "superseded"
    assert all(event.get("task_id") != old_id for event in run["events"] if event["kind"] == "dispatch")
    assert run["results"]["q2-S03-rubric-v3"]["status"] == "published"
    assert len(run["results"]) == run["summary"]["tasks_created"]


def test_repeated_correction_notice_does_not_duplicate_jobs_or_cache_reads():
    simulator = CorrectionSimulator("single", "fifo", "lru")
    correction = simulator.input["corrections"][0]
    simulator._schedule(0, 1, "correction", correction)
    run = simulator.run()
    baseline = CorrectionSimulator("single", "fifo", "lru").run()
    assert run["results"] == baseline["results"]
    assert run["summary"]["cache_hits"] == baseline["summary"]["cache_hits"]
    assert run["summary"]["tasks_created"] == 8


def test_cache_initialization_evictions_are_separate_from_runtime_evictions():
    run = CorrectionSimulator("single", "fifo", "lru").run()
    assert run["summary"]["initial_cache_evictions"] == 16
    # Four affected components survived seeding; eight new results need four evictions.
    assert run["summary"]["cache_evictions"] == 4


def test_correction_simulator_cannot_be_reused():
    simulator = CorrectionSimulator("single", "fifo", "lru")
    simulator.run()
    with pytest.raises(ValueError, match="new simulator"):
        simulator.run()
