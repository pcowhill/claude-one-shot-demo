"""Unit tests for the domain model invariants and the objective / KPI computation."""

from __future__ import annotations

import pytest

from app.core.baseline import fifo_schedule
from app.core.models import Interval, Priority, Request, Schedule
from app.core.objective import compute_kpis, schedule_value
from app.core.optimizer import optimize
from app.core.scenario import ScenarioConfig, default_live_scenario, generate_scenario


def test_interval_rejects_negative_length() -> None:
    with pytest.raises(ValueError, match="precedes start"):
        Interval(10, 5)


def test_request_rejects_infeasible_window() -> None:
    # release + duration must fit before the deadline.
    with pytest.raises(ValueError, match="infeasible"):
        Request("X", "X", None, None, 1.0, Priority.ROUTINE, release=0, deadline=20, duration=30)  # type: ignore[arg-type]


def test_request_derived_properties() -> None:
    req = Request("X", "X", None, None, 60.0, Priority.FLASH, 10, 100, 30)  # type: ignore[arg-type]
    assert req.feasible_window == Interval(10, 100)
    assert req.slack == 60  # 100 - 10 - 30
    assert req.value_density == pytest.approx(2.0)  # 60 / 30


def test_schedule_value_sums_served_weight() -> None:
    scenario = generate_scenario(ScenarioConfig(seed=3, n_requests=20))
    schedule = optimize(scenario).schedule
    expected = sum(
        scenario.request_index()[a.request_id].weight for a in schedule.allocations
    )
    assert schedule_value(scenario, schedule) == pytest.approx(expected)


def test_kpis_are_well_formed() -> None:
    scenario = default_live_scenario()
    kpis = compute_kpis(scenario, optimize(scenario).schedule)
    assert 0 <= kpis.value_capture_pct <= 100
    assert 0 <= kpis.served_pct <= 100
    assert kpis.served_count <= kpis.total_count
    assert all(0 <= u <= 100 for u in kpis.utilization_by_resource.values())
    # Every tasking is either served or dropped, never both.
    assert kpis.served_count + len(kpis.dropped_request_ids) == kpis.total_count


def test_kpis_priority_breakdown_totals_match() -> None:
    scenario = default_live_scenario()
    kpis = compute_kpis(scenario, fifo_schedule(scenario))
    totals = sum(total for _, total in kpis.served_by_priority.values())
    assert totals == len(scenario.requests)
    served = sum(s for s, _ in kpis.served_by_priority.values())
    assert served == kpis.served_count


def test_empty_schedule_has_zero_value() -> None:
    scenario = generate_scenario(ScenarioConfig(seed=9, n_requests=10))
    kpis = compute_kpis(scenario, Schedule(()))
    assert kpis.total_value == 0
    assert kpis.served_count == 0
    assert kpis.mean_utilization_pct == 0
