"""Unit tests for the naive baseline schedulers."""

from __future__ import annotations

from app.core.baseline import fifo_schedule, priority_greedy_schedule
from app.core.feasibility import schedule_violations
from app.core.scenario import benchmark_suite, default_live_scenario


def test_baselines_are_feasible() -> None:
    for scenario in benchmark_suite():
        assert schedule_violations(scenario, fifo_schedule(scenario)) == []
        assert schedule_violations(scenario, priority_greedy_schedule(scenario)) == []


def test_baselines_are_deterministic() -> None:
    scenario = default_live_scenario()
    assert fifo_schedule(scenario).allocations == fifo_schedule(scenario).allocations
    assert (
        priority_greedy_schedule(scenario).allocations
        == priority_greedy_schedule(scenario).allocations
    )


def test_baseline_schedules_a_nonempty_subset() -> None:
    scenario = default_live_scenario()
    schedule = fifo_schedule(scenario)
    assert 0 < len(schedule.scheduled_request_ids()) <= len(scenario.requests)


def test_now_boundary_is_respected() -> None:
    scenario = default_live_scenario()
    schedule = fifo_schedule(scenario, now=150)
    assert all(a.start >= 150 for a in schedule.allocations)
