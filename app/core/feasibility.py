"""Feasibility rules and interval geometry shared by every scheduler.

Keeping these predicates in one place is what lets the baseline, the optimiser and the live
engine all agree on exactly what "legal" means. Every move the optimiser makes is validated
through :func:`placement_is_feasible`, and :func:`validate_schedule` is the single source of
truth that the property-based tests assert against.

The hot path is :func:`find_earliest_start`: given an asset, a tasking and the intervals
already occupied on that asset, it returns the earliest legal start time (or ``None``). It is
an exact, gap-aware sweep — not a fixed-step scan — so it is both correct and fast.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import pairwise

from .models import Allocation, Interval, Request, Resource, Scenario, Schedule

# ----------------------------------------------------------------------------------------
# Eligibility (which assets *could* ever service a tasking)
# ----------------------------------------------------------------------------------------


def is_eligible(resource: Resource, request: Request) -> bool:
    """True iff ``resource`` has the capability and regional coverage ``request`` requires.

    This is the static, time-independent half of feasibility. The dynamic half (does it fit
    in time without colliding?) is handled by :func:`placement_is_feasible`.
    """
    if request.capability not in resource.capabilities:
        return False
    if request.region is not None and request.region not in resource.regions:  # noqa: SIM103
        return False
    return True


def eligible_resources(resources: Iterable[Resource], request: Request) -> list[Resource]:
    """All assets statically eligible for ``request`` (capability + region)."""
    return [r for r in resources if is_eligible(r, request)]


# ----------------------------------------------------------------------------------------
# Placement feasibility (does a concrete interval fit, legally?)
# ----------------------------------------------------------------------------------------


def placement_is_feasible(
    resource: Resource,
    request: Request,
    start: int,
    occupied: Sequence[Interval],
) -> bool:
    """True iff ``request`` may start at ``start`` on ``resource`` given ``occupied`` intervals.

    Checks, in order: static eligibility, the tasking's own release/deadline window, the
    asset being online for the whole service interval, and no overlap with already-occupied
    intervals on that asset.
    """
    if not is_eligible(resource, request):
        return False
    interval = Interval(start, start + request.duration)
    if start < request.release or interval.end > request.deadline:
        return False
    if not resource.is_online(interval):
        return False
    return all(not interval.overlaps(o) for o in occupied)


def find_earliest_start(
    resource: Resource,
    request: Request,
    occupied: Sequence[Interval],
    not_before: int = 0,
) -> int | None:
    """Earliest legal start for ``request`` on ``resource``, or ``None`` if it cannot fit.

    The search is an exact sweep over the asset's availability windows. Within each window we
    intersect with the tasking's ``[release, deadline)`` and ``not_before`` bound, then walk
    the occupied intervals keeping a ``cursor`` at the earliest still-free minute, returning as
    soon as a gap of at least ``duration`` opens up. Complexity is ``O(W + k log k)`` per call
    for ``W`` windows and ``k`` occupied intervals — not a minute-by-minute scan.

    Args:
        resource: The candidate asset.
        request: The tasking to place.
        occupied: Intervals already busy on this asset (any order; not required disjoint).
        not_before: Hard lower bound on the start (e.g. the current clock, so we never plan
            into the past). Defaults to 0.

    Returns:
        The earliest feasible start minute, or ``None`` if no legal placement exists.
    """
    # Self-contained contract: a returned start is always *fully* placeable, so an ineligible
    # asset yields None even if it has free time. Callers typically pre-filter on eligibility;
    # this guard keeps the function correct on its own (and keeps the property tests honest).
    if not is_eligible(resource, request):
        return None
    duration = request.duration
    lower = max(request.release, not_before)

    # Availability windows are expected sorted by start; sort defensively so callers that
    # build resources by hand (tests) cannot trip the sweep.
    for window in sorted(resource.availability, key=lambda w: w.start):
        seg_start = max(window.start, lower)
        seg_end = min(window.end, request.deadline)
        if seg_end - seg_start < duration:
            continue  # window too small once clipped to the tasking's legal range

        cursor = seg_start
        blockers = sorted(
            (o for o in occupied if o.end > seg_start and o.start < seg_end),
            key=lambda o: o.start,
        )
        for blocker in blockers:
            if blocker.start - cursor >= duration:
                return cursor  # a gap before this blocker is large enough
            cursor = max(cursor, blocker.end)
            if cursor + duration > seg_end:
                break  # no room left in this window
        else:
            # No blocker forced us out: the tail of the window is free.
            if cursor + duration <= seg_end:
                return cursor
            continue
        if cursor + duration <= seg_end:
            return cursor
    return None


# ----------------------------------------------------------------------------------------
# Whole-schedule validation (the property-test oracle)
# ----------------------------------------------------------------------------------------


def schedule_violations(scenario: Scenario, schedule: Schedule) -> list[str]:
    """Return a list of human-readable constraint violations (empty means feasible).

    This is intentionally exhaustive and independent of how a schedule was produced, so it can
    serve as the oracle for both unit and property-based tests. It checks:

    * every allocation references a known request and resource;
    * each request is allocated at most once;
    * the service interval respects release/deadline and the asset's online windows;
    * the asset is statically eligible (capability + region);
    * the interval length equals the tasking's duration;
    * no two allocations on the same asset overlap (the no-double-booking invariant).
    """
    violations: list[str] = []
    resources = scenario.resource_index()
    requests = scenario.request_index()

    seen_requests: set[str] = set()
    for alloc in schedule.allocations:
        req = requests.get(alloc.request_id)
        res = resources.get(alloc.resource_id)
        if req is None:
            violations.append(f"{alloc.request_id}: unknown request")
            continue
        if res is None:
            violations.append(f"{alloc.resource_id}: unknown resource")
            continue
        if alloc.request_id in seen_requests:
            violations.append(f"{alloc.request_id}: allocated more than once")
        seen_requests.add(alloc.request_id)

        if alloc.end - alloc.start != req.duration:
            violations.append(
                f"{alloc.request_id}: interval length {alloc.end - alloc.start} "
                f"!= duration {req.duration}"
            )
        if alloc.start < req.release or alloc.end > req.deadline:
            violations.append(
                f"{alloc.request_id}: [{alloc.start},{alloc.end}) outside "
                f"window [{req.release},{req.deadline})"
            )
        if not is_eligible(res, req):
            violations.append(f"{alloc.request_id}: {alloc.resource_id} not eligible")
        if not res.is_online(alloc.interval):
            violations.append(
                f"{alloc.request_id}: {alloc.resource_id} offline during {alloc.interval}"
            )

    # No-double-booking: pairwise overlap check within each asset.
    for resource_id, allocs in schedule.by_resource().items():
        ordered = sorted(allocs, key=lambda a: a.start)
        for prev, nxt in pairwise(ordered):
            if prev.interval.overlaps(nxt.interval):
                violations.append(
                    f"{resource_id}: double-booked — {prev.request_id} {prev.interval} "
                    f"overlaps {nxt.request_id} {nxt.interval}"
                )
    return violations


def is_feasible(scenario: Scenario, schedule: Schedule) -> bool:
    """Convenience wrapper: True iff the schedule has zero violations."""
    return not schedule_violations(scenario, schedule)


def occupied_by_resource(schedule: Schedule) -> dict[str, list[Interval]]:
    """Map resource id -> sorted occupied intervals, for incremental placement."""
    occ: dict[str, list[Interval]] = {}
    for alloc in schedule.allocations:
        occ.setdefault(alloc.resource_id, []).append(alloc.interval)
    for intervals in occ.values():
        intervals.sort(key=lambda i: i.start)
    return occ


def allocations_to_intervals(allocs: Iterable[Allocation]) -> list[Interval]:
    """Project allocations onto their occupied intervals."""
    return [a.interval for a in allocs]
