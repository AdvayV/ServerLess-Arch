import pytest

from simulator import POLICIES, Simulator, compare, workload


def request(number, time, function):
    return {"id": f"R{number}", "arrival": time, "function": function}


def test_cold_then_warm_and_zero_budget():
    trace = [request(1, 0, "timetable"), request(2, 500, "timetable")]
    warm = Simulator(trace, "adaptive", 128).run()
    cold = Simulator(trace, "adaptive", 0).run()
    assert [r["cold"] for r in warm["requests"]] == [True, False]
    assert [r["latency_ms"] for r in warm["requests"]] == [120, 20]
    assert [r["cold"] for r in cold["requests"]] == [True, True]
    assert cold["summary"]["peak_idle_mib"] == 0


@pytest.mark.parametrize("policy", POLICIES)
def test_overlap_creates_second_container_and_never_evicts_active(policy):
    trace = [request(1, 0, "assignment"), request(2, 1, "assignment")]
    result = Simulator(trace, policy, 0).run()
    assert result["summary"]["cold_starts"] == 2
    assert result["requests"][0]["container_id"] != result["requests"][1]["container_id"]
    for event in result["events"]:
        if event["kind"] == "evict":
            assert all(not (r["container_id"] == event["container_id"] and
                            r["arrival"] <= event["time"] < r["completion"])
                       for r in result["requests"])


def test_memory_budget_and_oversized_container():
    trace = [request(1, 0, "assignment"), request(2, 1800, "timetable")]
    result = Simulator(trace, "lru", 256).run()
    assert result["summary"]["evictions"] == 1
    assert all(e["idle_mib"] <= 256 for e in result["events"])
    oversized = Simulator([request(1, 0, "report")], "adaptive", 256).run()
    assert oversized["summary"]["cold_starts"] == 1
    assert oversized["summary"]["peak_idle_mib"] == 0


def test_timeout_and_idle_memory_integration():
    trace = [request(1, 0, "timetable"), request(2, 1000, "timetable")]
    result = Simulator(trace, "timeout", 128, timeout_ms=200).run()
    assert result["summary"]["cold_starts"] == 2
    assert result["summary"]["idle_mib_ms"] == 128 * 200
    assert result["summary"]["evaluation_ms"] == 1120


def test_same_trace_replayed_and_adaptive_cannot_see_future():
    trace = workload("shift")
    result = compare("shift", 512)
    assert all(run["summary"]["requests"] == len(trace) for run in result["runs"].values())
    assert compare("shift", 512)["runs"]["adaptive"]["requests"] == result["runs"]["adaptive"]["requests"]
    prefix = [request(1, 0, "timetable")]
    with_future = prefix + [request(2, 10000, "assignment")]
    first = Simulator(prefix, "adaptive", 128).run()["events"]
    second = Simulator(with_future, "adaptive", 128).run()["events"]
    assert [(e["kind"], e["message"]) for e in first if e["time"] <= 120] == [
        (e["kind"], e["message"]) for e in second if e["time"] <= 120]


def test_equal_time_completion_before_arrival_and_validation():
    result = Simulator([request(1, 0, "timetable"), request(2, 120, "timetable")], "lru", 128).run()
    assert not result["requests"][1]["cold"]
    for trace in ([request(1, -1, "timetable")], [request(1, 0, "unknown")],
                  [request(1, 0, "timetable"), request(1, 1, "timetable")]):
        try:
            Simulator(trace, "lru", 128)
            assert False, "Expected validation error"
        except ValueError:
            pass


def test_example_score_arithmetic_independently():
    values = [(0.95, 100, 128), (0.70, 1500, 256), (0.05, 2500, 512), (0.40, 800, 128)]
    scores = [p * startup / memory for p, startup, memory in values]
    assert [round(score, 3) for score in scores] == [0.742, 4.102, 0.244, 2.5]


def test_empty_trace_and_budget_pressure_all_policies():
    for policy in POLICIES:
        empty = Simulator([], policy, 0).run()
        assert empty["summary"]["requests"] == 0
        result = Simulator(workload("burst"), policy, 256).run()
        assert all(e["idle_mib"] <= 256 for e in result["events"])


def test_faascache_cost_frequency_and_logical_clock_determine_eviction():
    # The frequent but cheap timetable loses to the expensive fee service.
    # After eviction, timetable frequency restarts and its new access clock
    # comes from the previous victim's priority, not from elapsed milliseconds.
    trace = [request(1, 0, "timetable"), request(2, 200, "timetable"),
             request(3, 1000, "payment"), request(4, 2000, "timetable")]
    sim = Simulator(trace, "faascache", 128)
    result = sim.run()
    removals = [e for e in result["events"] if e["kind"] == "evict"]
    assert [e["function"] for e in removals] == ["timetable", "timetable"]
    assert removals[0]["gdsf_priority"] == 1.5625  # 2 * 100 / 128
    assert removals[1]["gdsf_priority"] == 2.3438  # 1.5625 + 100 / 128
    assert sim.gdsf_clock == 2.34375
    assert sim.gdsf_frequency["timetable"] == 0
    assert sim.gdsf_frequency["payment"] == 1
    assert [r["cold"] for r in result["requests"]] == [True, False, True, True]


def test_faascache_frequency_survives_until_last_replica_is_removed():
    trace = [request(1, 0, "assignment"), request(2, 1, "assignment"),
             request(3, 2, "assignment")]
    sim = Simulator(trace, "faascache", 256)
    result = sim.run()
    assert sim.gdsf_frequency["assignment"] == 3
    assert result["summary"]["evictions"] == 2
    assert sum(c["state"] == "IDLE" for c in sim.containers.values()) == 1
    # A subsequent high-cost payment displaces the last assignment copy.
    sim = Simulator(trace + [request(4, 2000, "payment")], "faascache", 256)
    sim.run()
    assert sim.gdsf_frequency["assignment"] == 0
    assert sim.gdsf_frequency["payment"] == 1


def test_short_replay_preserves_all_removal_explanations_and_final_pool():
    for run in compare("shift", 512)["runs"].values():
        steps = run["demo_steps"]
        assert len(steps) == len(run["requests"]) + 1
        assert [e["request_id"] for e in steps[:-1]] == [r["id"] for r in run["requests"]]
        assert [message for step in steps for message in step["decisions"]] == [
            e["message"] for e in run["events"] if e["kind"] in ("evict", "timeout")]
        assert steps[-1]["kind"] == "finished"
        assert steps[-1]["containers"] == run["events"][-1]["containers"]
        assert run["summary"]["startup_overhead_ms"] == sum(r["startup_ms"] for r in run["requests"])
