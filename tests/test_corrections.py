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
        cache.put(key, 50, 0)
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
