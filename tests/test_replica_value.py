"""Independent concurrency and conservation checks for candidate estimators."""

from math import exp

import pytest

from replica_value import ShadowWarmPool, erlang_marginal_rates, expected_extra_hits, nonincreasing_rates
from simulator import Simulator
from experiments.priority_study import TrialSimulator


def test_serial_calls_need_one_warm_slot_despite_slow_cold_start():
    pool = ShadowWarmPool(80)
    assert [pool.observe(time) for time in (0, 100, 200)] == [1, 1, 1]
    assert len(pool.rates(200)) == 1


def test_true_overlap_and_exact_completion_boundary():
    pool = ShadowWarmPool(80)
    assert [pool.observe(time) for time in (0, 50, 80)] == [1, 2, 1]
    simultaneous = ShadowWarmPool(80)
    assert [simultaneous.observe(0) for _ in range(3)] == [1, 2, 3]


def test_decay_reduces_value_without_changing_the_observed_capacity():
    pool = ShadowWarmPool(80, 2600)
    pool.observe(0)
    first = pool.rates(0)[0]
    assert pool.rates(2600)[0] == pytest.approx(first * exp(-1))
    assert len(pool.slots) == 1


def test_isotonic_projection_preserves_total_rate_and_monotonicity():
    values = nonincreasing_rates([3.0, 1.0, 2.0, 0.5])
    assert values == [3.0, 1.5, 1.5, 0.5]
    assert sum(values) == 6.5


def test_erlang_marginal_rates_match_known_unit_load():
    rates = erlang_marginal_rates(1.0, 1, 3)
    assert rates == pytest.approx([0.5, 0.3, 0.1375])
    assert sum(rates) == pytest.approx(1 - 1 / 16)


def test_active_capacity_is_credited_only_after_it_returns():
    rates = [1.0, 0.25]
    assert expected_extra_hits(rates, 1, [50], 0, 100) == 62.5
    assert expected_extra_hits(rates, 1, [150], 0, 100) == 100
    assert expected_extra_hits(rates, 2, [], 0, 100) == 25
    assert expected_extra_hits(rates, 3, [], 0, 100) == 0


def test_production_policy_evicts_startup_only_replicas_and_saves_a_cold_start():
    calls = [(0, "assignment"), (100, "assignment"), (200, "assignment"),
             (1500, "timetable"), (1900, "timetable"), (2100, "assignment")]
    trace = [{"id": f"R{i}", "arrival": time, "function": function}
             for i, (time, function) in enumerate(calls)]
    old = Simulator(trace, "adaptive", 512).run()
    new = Simulator(trace, "marginal", 512).run()
    assert old["summary"]["cold_starts"] == 5
    assert new["summary"]["cold_starts"] == 4
    assert not new["requests"][-2]["cold"] and not new["requests"][-1]["cold"]
    assert {event["function"] for event in new["events"] if event["kind"] == "evict"} == {"assignment"}


def test_marginal_rank_is_recomputed_after_each_eviction():
    trace = [{"id": f"R{i}", "arrival": 0, "function": "assignment"} for i in range(3)]
    simulator = Simulator(trace, "marginal", 1024)
    simulator.run()
    simulator.budget = 256
    removed = simulator.enforce_budget()
    assert [item["idle_copies_before_eviction"] for item in removed] == [3, 2]
    assert simulator.idle_memory() == 256


@pytest.mark.parametrize("policy", ["marginal", "erlang"])
def test_replica_priority_cannot_observe_future_arrivals(policy):
    prefix = [{"id": f"R{i}", "arrival": time, "function": "assignment"}
              for i, time in enumerate((0, 100, 200))]
    first = Simulator(prefix, policy, 256).run()
    longer = Simulator(prefix + [{"id": "future", "arrival": 50000, "function": "report"}], policy, 256).run()
    end = max(request["completion"] for request in first["requests"])
    assert first["events"] == [event for event in longer["events"] if event["time"] <= end]


@pytest.mark.parametrize("policy,expected", [("timeout", 128 * 200), ("marginal", 128 * 880)])
def test_benchmark_uses_a_common_idle_observation_window(policy, expected):
    trace = [{"id": "R1", "arrival": 0, "function": "timetable"}]
    summary = TrialSimulator(trace, policy, 128, timeout_ms=200).summary_at(1000)
    assert summary["common_observation_ms"] == 1000
    assert summary["common_idle_mib_ms"] == expected
