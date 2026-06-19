"""Property-based tests (Hypothesis) for the optimiser's core invariants.

Rather than check a handful of hand-picked cases, these generate thousands of arbitrary but
valid problem instances and assert the properties that must hold for *every* one of them. These
are the guarantees the whole value proposition rests on, so they are tested adversarially.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings

from app.core.baseline import fifo_schedule, priority_greedy_schedule
from app.core.feasibility import (
    find_earliest_start,
    occupied_by_resource,
    placement_is_feasible,
    schedule_violations,
)
from app.core.models import Scenario
from app.core.objective import compute_kpis, schedule_value
from app.core.optimizer import OptimizerConfig, optimize

from .conftest import scenarios

# A lighter optimiser config keeps the property suite fast while still exercising every move.
FAST = OptimizerConfig(seed=0, max_passes=8, restarts=6, kick_strength=3)
SETTINGS = settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@given(scenarios())
@SETTINGS
def test_optimizer_always_feasible(scenario: Scenario) -> None:
    """INVARIANT: the optimiser never returns an infeasible schedule.

    Covers no-double-booking, release/deadline windows, asset online-time and eligibility — the
    full constraint set, checked by the independent validator.
    """
    result = optimize(scenario, FAST)
    assert schedule_violations(scenario, result.schedule) == []


@given(scenarios())
@SETTINGS
def test_optimizer_never_below_baseline(scenario: Scenario) -> None:
    """INVARIANT: the optimiser's objective is never worse than the FIFO baseline."""
    result = optimize(scenario, FAST)
    baseline = schedule_value(scenario, fifo_schedule(scenario))
    assert result.value >= baseline - 1e-6
    assert result.value >= schedule_value(scenario, priority_greedy_schedule(scenario)) - 1e-6


@given(scenarios())
@SETTINGS
def test_optimizer_is_deterministic(scenario: Scenario) -> None:
    """INVARIANT: a fixed seed yields a byte-identical schedule."""
    first = optimize(scenario, FAST)
    second = optimize(scenario, FAST)
    assert first.value == second.value
    assert first.schedule.allocations == second.schedule.allocations


@given(scenarios())
@SETTINGS
def test_each_request_scheduled_at_most_once(scenario: Scenario) -> None:
    """INVARIANT: no tasking appears twice in the schedule."""
    allocs = optimize(scenario, FAST).schedule.allocations
    ids = [a.request_id for a in allocs]
    assert len(ids) == len(set(ids))


@given(scenarios())
@SETTINGS
def test_baselines_always_feasible(scenario: Scenario) -> None:
    """INVARIANT: the naive baselines are themselves always feasible."""
    assert schedule_violations(scenario, fifo_schedule(scenario)) == []
    assert schedule_violations(scenario, priority_greedy_schedule(scenario)) == []


@given(scenarios())
@SETTINGS
def test_kpis_within_bounds(scenario: Scenario) -> None:
    """INVARIANT: derived KPIs are always within their natural bounds."""
    kpis = compute_kpis(scenario, optimize(scenario, FAST).schedule)
    assert 0.0 <= kpis.value_capture_pct <= 100.0 + 1e-6
    assert kpis.served_count <= kpis.total_count
    assert all(0.0 <= u <= 100.0 + 1e-6 for u in kpis.utilization_by_resource.values())


@given(scenarios())
@SETTINGS
def test_find_earliest_start_returns_feasible_placement(scenario: Scenario) -> None:
    """INVARIANT: any start that ``find_earliest_start`` returns is actually placeable."""
    schedule = fifo_schedule(scenario)
    occupied = occupied_by_resource(schedule)
    for resource in scenario.resources:
        intervals = occupied.get(resource.id, [])
        for request in scenario.requests:
            start = find_earliest_start(resource, request, intervals)
            if start is not None:
                assert placement_is_feasible(resource, request, start, intervals)
                assert start >= request.release
                assert start + request.duration <= request.deadline
