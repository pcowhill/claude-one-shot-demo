"""Greedy constructive scheduling — the shared building block.

Both the naive baselines and the optimiser's *seed* solution are built by the same primitive:
take the taskings in some priority order and insert each one greedily into the asset/time slot
that becomes free earliest. What changes between them is only the **order** the taskings are
considered in:

* FIFO baseline      -> arrival order (release time);
* priority baseline  -> highest weight first;
* optimiser seed     -> highest value-density (weight per minute) first.

Sharing one constructor keeps the comparison honest (everyone uses the same placement rule)
and means the no-double-booking and window constraints are enforced in exactly one place.
The constructor also accepts ``now`` (never plan into the past) and ``fixed`` allocations
(locked or already-committed work), so the live engine reuses it verbatim for re-optimisation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from .feasibility import find_earliest_start, is_eligible, occupied_by_resource
from .models import Allocation, Interval, Request, Scenario, Schedule

# A request-ordering key: maps a Request to a sort key (lower sorts first).
OrderKey = Callable[[Request], tuple[object, ...]]


def by_release(request: Request) -> tuple[object, ...]:
    """FIFO ordering: earliest release first, ties broken by id for determinism."""
    return (request.release, request.id)


def by_weight_desc(request: Request) -> tuple[object, ...]:
    """Priority ordering: highest weight first, then earliest deadline (EDD), then id."""
    return (-request.weight, request.deadline, request.id)


def by_value_density_desc(request: Request) -> tuple[object, ...]:
    """Optimiser seed ordering: highest value-density first.

    Value-density (weight / duration) is the classic ranking for prize-collecting packing: it
    prefers taskings that deliver the most mission value per minute of scarce asset time. Ties
    break toward the more urgent (earlier deadline) tasking, then id.
    """
    return (-request.value_density, request.deadline, request.id)


def greedy_insert(
    scenario: Scenario,
    order_key: OrderKey,
    *,
    now: int = 0,
    fixed: Sequence[Allocation] = (),
) -> Schedule:
    """Construct a feasible schedule by greedy earliest-slot insertion.

    Args:
        scenario: The problem instance.
        order_key: Sort key deciding the order taskings are attempted in.
        now: Lower bound on every start time (the current clock). Nothing is planned earlier.
        fixed: Pre-placed allocations that must be respected — locked human decisions and
            already-committed in-progress work. Their assets/intervals are treated as occupied
            and their requests are considered already served.

    Returns:
        A feasible :class:`Schedule` containing ``fixed`` plus every tasking that could be
        greedily placed. By construction it never double-books an asset or violates a window.
    """
    # Assets are considered in a stable order so placement (and tie-breaking) is deterministic.
    resources = sorted(scenario.resources, key=lambda r: r.id)
    occupied = occupied_by_resource(Schedule(tuple(fixed)))
    placed: set[str] = {a.request_id for a in fixed}
    allocations: list[Allocation] = list(fixed)

    for request in sorted(scenario.requests, key=order_key):
        if request.id in placed:
            continue

        best_start: int | None = None
        best_resource: str | None = None
        for resource in resources:
            if not is_eligible(resource, request):
                continue
            start = find_earliest_start(
                resource, request, occupied.get(resource.id, ()), not_before=now
            )
            if start is None:
                continue
            # Prefer the earliest feasible start; break ties on resource id for determinism.
            if best_start is None or start < best_start:
                best_start, best_resource = start, resource.id

        if best_start is not None and best_resource is not None:
            end = best_start + request.duration
            allocations.append(Allocation(request.id, best_resource, best_start, end))
            occupied.setdefault(best_resource, []).append(Interval(best_start, end))
            occupied[best_resource].sort(key=lambda i: i.start)
            placed.add(request.id)

    return Schedule(tuple(allocations))


def unscheduled_requests(scenario: Scenario, schedule: Schedule) -> list[Request]:
    """Return the taskings that ``schedule`` did not place (the missed work)."""
    done = schedule.scheduled_request_ids()
    return [r for r in scenario.requests if r.id not in done]


def order_requests(requests: Iterable[Request], order_key: OrderKey) -> list[Request]:
    """Sort ``requests`` by ``order_key`` — small helper used by the optimiser's move loop."""
    return sorted(requests, key=order_key)
