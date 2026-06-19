"""Unit tests for the optimising scheduler's contract and key behaviours."""

from __future__ import annotations

from itertools import pairwise

import pytest

from app.core.baseline import fifo_schedule, priority_greedy_schedule
from app.core.feasibility import schedule_violations
from app.core.models import Scenario
from app.core.objective import schedule_value
from app.core.optimizer import OptimizerConfig, optimize
from app.core.scenario import (
    ScenarioConfig,
    benchmark_suite,
    default_live_scenario,
    generate_scenario,
)


def test_optimizer_output_is_feasible() -> None:
    scenario = default_live_scenario()
    result = optimize(scenario)
    assert schedule_violations(scenario, result.schedule) == []


def test_optimizer_never_worse_than_baselines() -> None:
    for scenario in benchmark_suite():
        result = optimize(scenario)
        fifo = schedule_value(scenario, fifo_schedule(scenario))
        prio = schedule_value(scenario, priority_greedy_schedule(scenario))
        assert result.value >= fifo - 1e-6
        assert result.value >= prio - 1e-6
        # The reported value matches the actual schedule it returns.
        assert result.value == pytest.approx(schedule_value(scenario, result.schedule))
        # And it matches the baseline value the run recorded.
        assert result.baseline_value == pytest.approx(fifo)


def test_optimizer_beats_fifo_under_oversubscription() -> None:
    # On a deliberately contended instance the optimiser should find real, not marginal, gains.
    scenario = generate_scenario(ScenarioConfig(seed=13, n_resources=6, n_requests=110))
    result = optimize(scenario)
    assert result.uplift_vs_baseline_pct > 5.0


def test_optimizer_is_deterministic() -> None:
    scenario = default_live_scenario()
    a = optimize(scenario)
    b = optimize(scenario)
    assert a.value == b.value
    assert a.schedule.allocations == b.schedule.allocations


def test_optimizer_respects_now_boundary() -> None:
    scenario = default_live_scenario()
    result = optimize(scenario, now=200)
    assert all(a.start >= 200 for a in result.schedule.allocations)


def test_optimizer_preserves_fixed_allocations() -> None:
    scenario = default_live_scenario()
    seed_schedule = fifo_schedule(scenario)
    pinned = seed_schedule.allocations[0].with_locked(True)
    result = optimize(scenario, fixed=(pinned,))
    kept = result.schedule.for_request(pinned.request_id)
    assert kept is not None
    assert (kept.resource_id, kept.start, kept.end) == (
        pinned.resource_id,
        pinned.start,
        pinned.end,
    )
    assert schedule_violations(scenario, result.schedule) == []


def test_optimizer_handles_empty_scenario() -> None:
    empty = Scenario("empty", 480, 0, default_live_scenario().resources, ())
    result = optimize(empty)
    assert result.value == 0
    assert result.schedule.allocations == ()


def test_history_is_monotonic_nondecreasing() -> None:
    # The recorded best-so-far trace never goes down (we keep the incumbent on a failed kick).
    result = optimize(generate_scenario(ScenarioConfig(seed=14, n_requests=120)))
    history = result.history
    assert all(b >= a - 1e-6 for a, b in pairwise(history))


def test_small_config_still_feasible_and_bounded() -> None:
    scenario = default_live_scenario()
    result = optimize(scenario, OptimizerConfig(seed=1, max_passes=3, restarts=2, kick_strength=2))
    assert schedule_violations(scenario, result.schedule) == []
    assert result.value >= result.baseline_value - 1e-6
