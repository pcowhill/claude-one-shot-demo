"""Shared test fixtures and a Hypothesis strategy for random, *valid* scenarios.

The strategy is the backbone of the property-based tests: it manufactures arbitrary but
internally-consistent problem instances (assets with capabilities/regions/availability, taskings
with feasible release/deadline windows) so we can assert the optimiser's invariants hold on
thousands of inputs, not just the hand-picked ones.
"""

from __future__ import annotations

import hypothesis.strategies as st
import pytest
from hypothesis import strategies

from app.core.models import (
    Capability,
    Interval,
    Priority,
    Request,
    Resource,
    ResourceKind,
    Scenario,
)
from app.core.scenario import ScenarioConfig, generate_scenario

REGIONS = ["NORTH", "SOUTH", "EAST", "WEST", "CENTRAL"]


@pytest.fixture
def small_scenario() -> Scenario:
    """A small, fixed, reproducible scenario for deterministic unit tests."""
    return generate_scenario(ScenarioConfig(name="test", seed=1, n_resources=4, n_requests=30))


@st.composite
def _availability(draw: st.DrawFn, horizon: int) -> tuple[Interval, ...]:
    """Generate 1–3 disjoint online windows within ``[0, horizon)``."""
    windows: list[Interval] = []
    cursor = draw(st.integers(min_value=0, max_value=30))
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        length = draw(st.integers(min_value=20, max_value=160))
        if cursor + length > horizon:
            break
        windows.append(Interval(cursor, cursor + length))
        cursor = cursor + length + draw(st.integers(min_value=5, max_value=40))
    return tuple(windows) if windows else (Interval(0, horizon),)


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    """A Hypothesis strategy yielding arbitrary but feasible-by-construction scenarios."""
    horizon = draw(st.integers(min_value=120, max_value=480))

    resources: list[Resource] = []
    for i in range(draw(st.integers(min_value=1, max_value=5))):
        caps = draw(st.sets(st.sampled_from(list(Capability)), min_size=1, max_size=3))
        regions = draw(st.sets(st.sampled_from(REGIONS), min_size=1, max_size=5))
        resources.append(
            Resource(
                id=f"R{i}",
                name=f"R{i}",
                kind=ResourceKind.UAV,
                capabilities=frozenset(caps),
                regions=frozenset(regions),
                availability=draw(_availability(horizon)),
            )
        )

    requests: list[Request] = []
    for j in range(draw(st.integers(min_value=0, max_value=22))):
        duration = draw(st.integers(min_value=5, max_value=max(5, min(60, horizon // 2))))
        release = draw(st.integers(min_value=0, max_value=max(0, horizon - duration)))
        deadline = min(horizon, release + duration + draw(st.integers(min_value=0, max_value=120)))
        requests.append(
            Request(
                id=f"J{j}",
                name=f"J{j}",
                capability=draw(st.sampled_from(list(Capability))),
                region=draw(st.one_of(st.none(), st.sampled_from(REGIONS))),
                weight=draw(
                    strategies.floats(
                        min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False
                    )
                ),
                priority=draw(st.sampled_from(list(Priority))),
                release=release,
                deadline=deadline,
                duration=duration,
            )
        )

    return Scenario(
        name="hypothesis",
        horizon=horizon,
        seed=0,
        resources=tuple(resources),
        requests=tuple(requests),
    )
