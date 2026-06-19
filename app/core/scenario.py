"""Synthetic scenario generation — reproducible problem instances and event streams.

Everything here is invented from a seed; no proprietary, sensitive, or real-world data is
used. The scenario is a tasteful, unclassified analogue of a sensor-tasking / collection-
management problem: a heterogeneous pool of observation assets (satellites, UAVs, aircraft, a
ground station) services a stream of prioritised, deadline-bound observation requests.

Two design goals shape the generator:

* **Oversubscription.** Demand deliberately exceeds capacity, so not every tasking can be
  served. That is the whole point — *which* taskings you drop is where an optimiser earns its
  keep, and it is what lets the benchmark show daylight over FIFO.
* **Guaranteed eligibility.** Each request is derived *from* a randomly chosen capable asset,
  so every tasking has at least one asset that could (capability- and region-wise) service it.
  Misses therefore come from genuine time/contention pressure, not from impossible asks — which
  keeps the value-capture metric meaningful.

The generator also emits an **event stream**: request arrivals over time, a couple of unplanned
asset outages, and a re-tasking (priority bump). The live engine consumes this stream so that
information arrives incrementally, the way it would in an operations centre.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .models import (
    Capability,
    Event,
    EventType,
    Interval,
    OutagePayload,
    Priority,
    PriorityChangePayload,
    Request,
    Resource,
    ResourceKind,
    Scenario,
)

# Numeric mission value seeded by priority tier (actual weight is jittered around these).
PRIORITY_BASE_WEIGHT: dict[Priority, float] = {
    Priority.ROUTINE: 10.0,
    Priority.PRIORITY: 25.0,
    Priority.IMMEDIATE: 60.0,
    Priority.FLASH: 120.0,
}

# Tier mix: most taskings are routine, a precious few are FLASH. This skew is what makes the
# "protect the high-value work" behaviour visible in the per-priority KPI breakdown.
PRIORITY_WEIGHTS: list[tuple[Priority, float]] = [
    (Priority.ROUTINE, 0.46),
    (Priority.PRIORITY, 0.30),
    (Priority.IMMEDIATE, 0.17),
    (Priority.FLASH, 0.07),
]

REGIONS: tuple[str, ...] = ("NORTH", "SOUTH", "EAST", "WEST", "CENTRAL")


@dataclass(frozen=True, slots=True)
class _AssetTemplate:
    """Blueprint for one asset class, expanded into concrete :class:`Resource` objects."""

    kind: ResourceKind
    capabilities: frozenset[Capability]
    region_span: int  # how many regions this asset class covers


# The standing asset catalogue. Capabilities and coverage differ by class, which is what makes
# eligibility a real (non-trivial) constraint: a SAR tasking cannot go to a pure-RF ground
# station, an FMV tasking only fits the UAVs, and so on.
ASSET_CATALOGUE: tuple[_AssetTemplate, ...] = (
    _AssetTemplate(ResourceKind.SATELLITE, frozenset({Capability.EO, Capability.IR}), 5),
    _AssetTemplate(ResourceKind.SATELLITE, frozenset({Capability.SAR, Capability.EO}), 5),
    _AssetTemplate(ResourceKind.UAV, frozenset({Capability.EO, Capability.IR, Capability.FMV}), 3),
    _AssetTemplate(ResourceKind.UAV, frozenset({Capability.EO, Capability.FMV}), 2),
    _AssetTemplate(
        ResourceKind.AIRCRAFT, frozenset({Capability.EO, Capability.IR, Capability.SAR}), 3
    ),
    _AssetTemplate(ResourceKind.GROUND_STATION, frozenset({Capability.RF}), 5),
)


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    """Knobs for :func:`generate_scenario`."""

    name: str = "Live Operations"
    seed: int = 7
    horizon: int = 480  # an 8-hour operations shift, in minutes
    n_resources: int = 6
    n_requests: int = 84  # deliberately oversubscribed: demand outstrips asset capacity
    outages: int = 2  # unplanned asset outages injected into the stream
    priority_bumps: int = 3  # mid-mission re-taskings injected into the stream


# ----------------------------------------------------------------------------------------
# Asset (resource) construction
# ----------------------------------------------------------------------------------------


def _availability_for(kind: ResourceKind, horizon: int, rng: random.Random) -> tuple[Interval, ...]:
    """Generate availability windows whose shape reflects the asset class.

    Satellites get intermittent "passes" (short online windows with gaps), aircraft and UAVs
    get one or two long sorties with a turnaround gap, and the ground station is continuous.
    The gaps are exactly what make scheduling around limited availability non-trivial.
    """
    if kind is ResourceKind.SATELLITE:
        windows: list[Interval] = []
        cursor = rng.randint(0, 40)
        while cursor < horizon:
            length = rng.randint(55, 95)
            end = min(cursor + length, horizon)
            if end - cursor >= 30:
                windows.append(Interval(cursor, end))
            cursor = end + rng.randint(35, 70)  # eclipse / out-of-view gap
        return tuple(windows)
    if kind in (ResourceKind.UAV, ResourceKind.AIRCRAFT):
        # One long sortie, occasionally split by a refuel/turnaround gap.
        start = rng.randint(0, 50)
        if rng.random() < 0.6:
            mid = rng.randint(horizon // 2 - 30, horizon // 2 + 30)
            gap = rng.randint(30, 60)
            return (Interval(start, mid), Interval(min(mid + gap, horizon - 1), horizon))
        return (Interval(start, horizon),)
    # Ground station: always on.
    return (Interval(0, horizon),)


def _build_resources(config: ScenarioConfig, rng: random.Random) -> tuple[Resource, ...]:
    """Expand the asset catalogue into ``config.n_resources`` concrete assets."""
    resources: list[Resource] = []
    kind_counters: dict[ResourceKind, int] = {}
    for i in range(config.n_resources):
        template = ASSET_CATALOGUE[i % len(ASSET_CATALOGUE)]
        kind_counters[template.kind] = kind_counters.get(template.kind, 0) + 1
        idx = kind_counters[template.kind]
        prefix = {
            ResourceKind.SATELLITE: "SAT",
            ResourceKind.UAV: "UAV",
            ResourceKind.AIRCRAFT: "AIR",
            ResourceKind.GROUND_STATION: "GND",
        }[template.kind]
        resource_id = f"{prefix}-{idx}"

        # Region coverage: a contiguous-ish span sampled deterministically.
        span = min(template.region_span, len(REGIONS))
        start = rng.randint(0, len(REGIONS) - span)
        regions = frozenset(REGIONS[start : start + span])

        resources.append(
            Resource(
                id=resource_id,
                name=f"{template.kind.value.title()} {prefix}-{idx}",
                kind=template.kind,
                capabilities=template.capabilities,
                regions=regions,
                availability=_availability_for(template.kind, config.horizon, rng),
            )
        )
    return tuple(resources)


# ----------------------------------------------------------------------------------------
# Request (tasking) construction
# ----------------------------------------------------------------------------------------


def _weighted_priority(rng: random.Random) -> Priority:
    """Sample a priority tier from the skewed operational mix."""
    roll = rng.random()
    cumulative = 0.0
    for tier, share in PRIORITY_WEIGHTS:
        cumulative += share
        if roll <= cumulative:
            return tier
    return Priority.ROUTINE


def _build_requests(
    config: ScenarioConfig, resources: tuple[Resource, ...], rng: random.Random
) -> tuple[Request, ...]:
    """Generate taskings, each derived from a capable asset so eligibility is guaranteed."""
    requests: list[Request] = []
    for i in range(config.n_requests):
        # Anchor on a random asset, then pick a capability/region it actually supports. This is
        # what guarantees >=1 eligible asset and keeps misses attributable to contention.
        anchor = rng.choice(resources)
        capability = rng.choice(sorted(anchor.capabilities, key=lambda c: c.value))
        region = rng.choice(sorted(anchor.regions)) if rng.random() < 0.6 else None

        priority = _weighted_priority(rng)
        weight = round(PRIORITY_BASE_WEIGHT[priority] * rng.uniform(0.8, 1.25), 1)

        duration = rng.choice([15, 20, 25, 30, 35, 40, 50])
        latest_release = max(0, config.horizon - duration)
        release = rng.randint(0, latest_release)
        # Tight, varied windows: many taskings have little slack, so an early greedy commitment
        # frequently blocks a better combination later — precisely the gap the optimiser closes.
        slack = rng.choice([0, 0, 10, 15, 20, 30, 45, 70])
        deadline = min(config.horizon, release + duration + slack)

        requests.append(
            Request(
                id=f"REQ-{i:04d}",
                name=f"{capability.value} collect · {region or 'ANY'}",
                capability=capability,
                region=region,
                weight=weight,
                priority=priority,
                release=release,
                deadline=deadline,
                duration=duration,
            )
        )
    return tuple(requests)


# ----------------------------------------------------------------------------------------
# Event stream construction
# ----------------------------------------------------------------------------------------


def _build_events(
    config: ScenarioConfig,
    resources: tuple[Resource, ...],
    requests: tuple[Request, ...],
    rng: random.Random,
) -> tuple[Event, ...]:
    """Assemble the ordered event stream that reveals information over time."""
    events: list[Event] = [
        Event(
            time=req.release,
            type=EventType.REQUEST_ARRIVED,
            payload=req,
            description=f"{req.priority.name} tasking {req.id} ({req.name}) arrived",
        )
        for req in requests
    ]

    # Unplanned outages: knock an asset offline for a stretch in the middle of the shift, biased
    # toward assets that carry real load so the re-optimisation has something to fix.
    outage_assets = [r for r in resources if r.kind is not ResourceKind.GROUND_STATION]
    rng.shuffle(outage_assets)
    for asset in outage_assets[: config.outages]:
        onset = rng.randint(config.horizon // 5, config.horizon // 2)
        length = rng.randint(60, 120)
        window = Interval(onset, min(onset + length, config.horizon))
        events.append(
            Event(
                time=onset,
                type=EventType.RESOURCE_OFFLINE,
                payload=OutagePayload(resource_id=asset.id, window=window),
                description=f"UNPLANNED OUTAGE: {asset.id} offline {window.start}–{window.end}",
            )
        )

    # Re-taskings: bump a few routine taskings up to FLASH shortly after they arrive.
    bumpable = [r for r in requests if r.priority <= Priority.PRIORITY]
    rng.shuffle(bumpable)
    for req in bumpable[: config.priority_bumps]:
        when = min(config.horizon - 1, req.release + rng.randint(1, 15))
        new_weight = round(PRIORITY_BASE_WEIGHT[Priority.FLASH] * rng.uniform(0.9, 1.2), 1)
        events.append(
            Event(
                time=when,
                type=EventType.PRIORITY_CHANGED,
                payload=PriorityChangePayload(req.id, Priority.FLASH, new_weight),
                description=f"RE-TASK: {req.id} elevated to FLASH",
            )
        )

    # Stable ordering: by time, then a fixed type priority, then a stable tiebreak.
    type_order = {
        EventType.RESOURCE_OFFLINE: 0,
        EventType.PRIORITY_CHANGED: 1,
        EventType.REQUEST_ARRIVED: 2,
        EventType.RESOURCE_ONLINE: 3,
        EventType.OPERATOR_COMMAND: 4,
        EventType.TICK: 5,
    }
    events.sort(key=lambda e: (e.time, type_order[e.type], e.description))
    return tuple(events)


# ----------------------------------------------------------------------------------------
# Public entry points
# ----------------------------------------------------------------------------------------


def generate_scenario(config: ScenarioConfig | None = None) -> Scenario:
    """Generate a complete, reproducible scenario from ``config`` (or sensible defaults)."""
    config = config or ScenarioConfig()
    rng = random.Random(config.seed)
    resources = _build_resources(config, rng)
    requests = _build_requests(config, resources, rng)
    events = _build_events(config, resources, requests, rng)
    return Scenario(
        name=config.name,
        horizon=config.horizon,
        seed=config.seed,
        resources=resources,
        requests=requests,
        events=events,
    )


def default_live_scenario() -> Scenario:
    """The scenario the dashboard boots with — lively but readable on a single screen.

    Tuned (seed/size) so the live, fully-online optimiser holds a solid margin over the shadow
    FIFO baseline throughout the shift and visibly protects more high-priority taskings.
    """
    return generate_scenario(
        ScenarioConfig(name="Live Operations", seed=24, n_resources=6, n_requests=90)
    )


def benchmark_suite() -> tuple[Scenario, ...]:
    """A spread of scenarios (size, seed, pressure) for the proof-of-value benchmark.

    Varying both the seed and the oversubscription level demonstrates that the optimiser's
    advantage is systematic, not a fluke of one lucky instance.
    """
    configs = [
        ScenarioConfig(name="Light load", seed=11, n_resources=6, n_requests=60, outages=1),
        ScenarioConfig(name="Nominal", seed=12, n_resources=6, n_requests=84, outages=2),
        ScenarioConfig(name="Heavy load", seed=13, n_resources=6, n_requests=110, outages=2),
        ScenarioConfig(name="Surge", seed=14, n_resources=7, n_requests=140, outages=3),
        ScenarioConfig(name="Constrained fleet", seed=15, n_resources=4, n_requests=90, outages=1),
        ScenarioConfig(name="Wide fleet", seed=16, n_resources=9, n_requests=190, outages=3),
    ]
    return tuple(generate_scenario(c) for c in configs)
