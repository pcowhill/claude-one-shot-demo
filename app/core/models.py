"""Domain model for the resource-allocation problem.

The scenario is a deliberately *civilian, unclassified* analogue of a sensor-tasking /
collection-management problem: a pool of heterogeneous observation **assets** (resources)
must service a stream of prioritised, deadline-bound observation **requests** (taskings).
It is structurally identical to a large family of operations problems — emergency dispatch,
ground-station contact scheduling, field-crew assignment — so the engine transfers directly.

Formally this is *weighted job scheduling on unrelated parallel machines with release times,
deadlines, machine-eligibility constraints and a prize-collecting objective*: choose a subset
of taskings and assign each to one capable asset over a contiguous time interval inside the
asset's online window, without overlapping other taskings on that asset, to maximise the total
weight (mission value) of serviced taskings. That problem is NP-hard in general, which is what
justifies a real optimiser rather than a sort (see ``docs/RESEARCH_BRIEF.md``).

Design choices worth knowing:

* **Time is integer minutes** over a finite horizon (default an 8-hour, 480-minute shift).
  Integers make the geometry exact and the property-based tests crisp — no float fuzz.
* **Unit capacity per asset** (one tasking at a time). This makes the "no double-booking"
  invariant clean to state and verify; multi-capacity is noted as a production extension.
* **Two eligibility dimensions** — sensing *capability* and *region* — so the constraint
  structure is non-trivial (a tasking can usually go to several, but not all, assets).

All domain types are plain frozen dataclasses: cheap to hash, safe to share between the
synchronous optimiser and the async engine, and free of any framework coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum

# ----------------------------------------------------------------------------------------
# Enumerations
# ----------------------------------------------------------------------------------------


class Capability(str, Enum):
    """Sensing modality a tasking needs and an asset may provide.

    Values mirror real remote-sensing modalities so the scenario reads like an operations
    picture, but they carry no special meaning beyond set-membership matching.
    """

    EO = "EO"  # electro-optical (daylight imagery)
    IR = "IR"  # infrared (thermal / night)
    SAR = "SAR"  # synthetic-aperture radar (all-weather)
    RF = "RF"  # radio-frequency survey
    FMV = "FMV"  # full-motion video


class ResourceKind(str, Enum):
    """Class of observation asset. Affects only presentation and scenario generation."""

    SATELLITE = "SATELLITE"
    UAV = "UAV"
    AIRCRAFT = "AIRCRAFT"
    GROUND_STATION = "GROUND_STATION"


class Priority(IntEnum):
    """Operational priority tier.

    Modelled as an ``IntEnum`` so tiers order naturally (FLASH > IMMEDIATE > ...). The tier
    drives the colour coding in the dashboard and seeds the numeric ``weight`` (the actual
    quantity the objective maximises), but the optimiser only ever reasons about ``weight``.
    """

    ROUTINE = 1
    PRIORITY = 2
    IMMEDIATE = 3
    FLASH = 4


class EventType(str, Enum):
    """Kinds of event that drive the engine's re-optimisation.

    The stream is what makes the system *event-driven*: state changes (a new tasking, an
    asset dropping offline, a priority bump, or a human command) arrive over time and each
    one can trigger a fresh optimisation of the remaining horizon.
    """

    REQUEST_ARRIVED = "REQUEST_ARRIVED"
    RESOURCE_OFFLINE = "RESOURCE_OFFLINE"
    RESOURCE_ONLINE = "RESOURCE_ONLINE"
    PRIORITY_CHANGED = "PRIORITY_CHANGED"
    OPERATOR_COMMAND = "OPERATOR_COMMAND"
    TICK = "TICK"


class AllocationStatus(str, Enum):
    """Lifecycle of a single allocation as the simulated clock advances."""

    PLANNED = "PLANNED"  # proposed for the future; may still be re-optimised away
    COMMITTED = "COMMITTED"  # start time reached; in progress; immutable
    COMPLETED = "COMPLETED"  # finished on time


# ----------------------------------------------------------------------------------------
# Geometry: time intervals
# ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Interval:
    """A half-open time interval ``[start, end)`` in integer minutes.

    Half-open semantics mean two intervals that merely *touch* (``a.end == b.start``) do **not**
    overlap, which is exactly what we want for back-to-back taskings on one asset.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"Interval end {self.end} precedes start {self.start}")

    @property
    def duration(self) -> int:
        """Length of the interval in minutes."""
        return self.end - self.start

    def overlaps(self, other: Interval) -> bool:
        """True iff the two half-open intervals share at least one minute."""
        return self.start < other.end and other.start < self.end

    def contains(self, other: Interval) -> bool:
        """True iff ``other`` lies entirely within this interval."""
        return self.start <= other.start and other.end <= self.end


# ----------------------------------------------------------------------------------------
# Resources (assets) and requests (taskings)
# ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Resource:
    """An observation asset with limited, time-windowed availability.

    Attributes:
        id: Stable identifier (e.g. ``"SAT-1"``).
        name: Human-friendly label for the dashboard.
        kind: Asset class (satellite, UAV, ...). Presentation/scenario only.
        capabilities: Sensing modalities the asset can provide. A tasking is eligible only if
            its required capability is in this set.
        regions: Geographic regions the asset can cover. A tasking with a region constraint is
            eligible only if its region is in this set.
        availability: Disjoint online windows. The asset can be tasked only *inside* one of
            these windows; the gaps model maintenance, eclipse, downlink, or transit.
    """

    id: str
    name: str
    kind: ResourceKind
    capabilities: frozenset[Capability]
    regions: frozenset[str]
    availability: tuple[Interval, ...]

    def is_online(self, interval: Interval) -> bool:
        """True iff ``interval`` fits entirely inside a single availability window."""
        return any(window.contains(interval) for window in self.availability)

    def online_window_containing(self, t: int) -> Interval | None:
        """Return the availability window covering minute ``t``, if any."""
        for window in self.availability:
            if window.start <= t < window.end:
                return window
        return None

    @property
    def total_available_minutes(self) -> int:
        """Sum of online minutes — the denominator for utilisation."""
        return sum(w.duration for w in self.availability)


@dataclass(frozen=True, slots=True)
class Request:
    """A single tasking to be serviced by exactly one capable asset.

    Attributes:
        id: Stable identifier (e.g. ``"REQ-0007"``).
        name: Human-friendly label / target description.
        capability: Sensing modality required.
        region: Region constraint, or ``None`` if the tasking can be serviced anywhere.
        weight: Mission value delivered if the tasking is serviced on time. This is the
            quantity the objective maximises; it is seeded from ``priority`` plus noise.
        priority: Operational tier (presentation + colour + weight seed).
        release: Earliest minute the tasking may start (when it becomes known/feasible).
        deadline: Latest minute by which it must *finish*.
        duration: Service time in minutes.
    """

    id: str
    name: str
    capability: Capability
    region: str | None
    weight: float
    priority: Priority
    release: int
    deadline: int
    duration: int

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ValueError(f"{self.id}: duration must be positive, got {self.duration}")
        if self.release + self.duration > self.deadline:
            raise ValueError(
                f"{self.id}: infeasible by construction "
                f"(release {self.release} + duration {self.duration} > deadline {self.deadline})"
            )

    @property
    def feasible_window(self) -> Interval:
        """The window ``[release, deadline)`` in which the tasking must be placed."""
        return Interval(self.release, self.deadline)

    @property
    def slack(self) -> int:
        """Scheduling freedom in minutes: how much the start can float and still fit."""
        return self.deadline - self.release - self.duration

    @property
    def value_density(self) -> float:
        """Mission value per minute of asset time — the core greedy ranking signal."""
        return self.weight / self.duration


# ----------------------------------------------------------------------------------------
# Allocations and schedules (solutions)
# ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Allocation:
    """An assignment of one request to one resource over a concrete time interval.

    ``locked`` marks a human decision: a locked allocation is treated as a hard constraint by
    every subsequent re-optimisation (the engine plans *around* it). ``status`` tracks the
    lifecycle as the simulated clock advances.
    """

    request_id: str
    resource_id: str
    start: int
    end: int
    locked: bool = False
    status: AllocationStatus = AllocationStatus.PLANNED

    @property
    def interval(self) -> Interval:
        """The occupied time interval."""
        return Interval(self.start, self.end)

    def with_status(self, status: AllocationStatus) -> Allocation:
        """Return a copy with a new lifecycle status."""
        return replace(self, status=status)

    def with_locked(self, locked: bool) -> Allocation:
        """Return a copy with the lock flag toggled."""
        return replace(self, locked=locked)


@dataclass(frozen=True, slots=True)
class Schedule:
    """An immutable set of allocations — a *solution* to one instance.

    A ``Schedule`` is intentionally dumb: it stores allocations and offers read-only views.
    Feasibility checking lives in :mod:`app.core.feasibility` and value/KPIs in
    :mod:`app.core.objective`, so the data type stays free of policy.
    """

    allocations: tuple[Allocation, ...] = ()

    def by_resource(self) -> dict[str, list[Allocation]]:
        """Group allocations by resource id, each list sorted by start time."""
        grouped: dict[str, list[Allocation]] = {}
        for alloc in self.allocations:
            grouped.setdefault(alloc.resource_id, []).append(alloc)
        for allocs in grouped.values():
            allocs.sort(key=lambda a: a.start)
        return grouped

    def scheduled_request_ids(self) -> frozenset[str]:
        """The set of request ids that have an allocation in this schedule."""
        return frozenset(a.request_id for a in self.allocations)

    def for_request(self, request_id: str) -> Allocation | None:
        """Return the allocation for a given request id, if present."""
        return next((a for a in self.allocations if a.request_id == request_id), None)

    def replace_allocations(self, allocations: tuple[Allocation, ...]) -> Schedule:
        """Return a new schedule with a different allocation set."""
        return Schedule(allocations=allocations)


# ----------------------------------------------------------------------------------------
# Scenario: a complete problem instance plus its event stream
# ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Event:
    """A single event in the simulated stream.

    ``payload`` carries event-specific data (the new ``Request`` for an arrival, the asset id
    and outage ``Interval`` for an outage, etc.). It is typed as ``object`` here and narrowed
    at the use site; the engine owns the small, closed set of payload shapes.
    """

    time: int
    type: EventType
    payload: object = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class OutagePayload:
    """An unplanned asset outage: ``resource_id`` is unavailable during ``window``.

    Carried by :attr:`EventType.RESOURCE_OFFLINE` events. The engine subtracts ``window`` from
    the asset's availability and re-optimises, which is what forces in-flight re-homing of any
    work that had been planned on the now-unavailable asset.
    """

    resource_id: str
    window: Interval


@dataclass(frozen=True, slots=True)
class PriorityChangePayload:
    """A re-tasking signal: ``request_id`` is re-prioritised to ``priority`` / ``weight``.

    Carried by :attr:`EventType.PRIORITY_CHANGED` events. Bumping a routine tasking to FLASH
    mid-mission is a classic trigger that should make the optimiser reshuffle the plan.
    """

    request_id: str
    priority: Priority
    weight: float


@dataclass(frozen=True, slots=True)
class Scenario:
    """A complete, self-contained problem instance.

    Attributes:
        name: Human-friendly scenario label.
        horizon: Length of the operational window in minutes (taskings live in ``[0, horizon)``).
        seed: RNG seed used to generate it — the whole instance is reproducible from this.
        resources: The asset pool.
        requests: Every tasking that will ever be known (the full, offline-omniscient set).
        events: The ordered event stream that *reveals* taskings/outages over time. Batch
            solvers ignore the stream and use ``requests`` directly; the live engine consumes
            the stream so that information arrives incrementally, as it would in the field.
    """

    name: str
    horizon: int
    seed: int
    resources: tuple[Resource, ...]
    requests: tuple[Request, ...]
    events: tuple[Event, ...] = field(default_factory=tuple)

    def resource_index(self) -> dict[str, Resource]:
        """Map resource id -> Resource for O(1) lookup."""
        return {r.id: r for r in self.resources}

    def request_index(self) -> dict[str, Request]:
        """Map request id -> Request for O(1) lookup."""
        return {j.id: j for j in self.requests}

    @property
    def total_possible_value(self) -> float:
        """Upper bound on deliverable value (every tasking serviced). The KPI denominator."""
        return sum(j.weight for j in self.requests)
