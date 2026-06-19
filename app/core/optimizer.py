"""The optimising scheduler — an homage to "Iterated Distributed Feedback".

This is the heart of the project. The problem (weighted, prize-collecting interval scheduling
on unrelated parallel machines with release times, deadlines and eligibility constraints) is
NP-hard, so we do not chase a provably optimal solution. Instead — exactly as Roster-5
describes its own algorithm building "'best-known' quality solutions in a handful of seconds"
— we run a fast **anytime metaheuristic**: a strong greedy seed, refined by local search, then
diversified by *iterated* restarts.

Algorithm (Iterated Local Search with ejection chains)
------------------------------------------------------
1. **Seed.** Construct three greedy solutions — value-density-first, weight-first, and FIFO —
   using the shared :mod:`app.core.construct` primitive, and keep the best. Seeding from the
   FIFO construction is also what *guarantees the optimiser can never score below the FIFO
   baseline* (see the invariant note below).
2. **Local search** to a local optimum using two improving moves:
   * **INSERT** — place an unscheduled tasking into free space. Strictly raises the objective.
   * **EVICT-AND-INSERT** (ejection chain) — to fit a high-value tasking, evict the *cheapest*
     set of lower-value taskings it collides with on one asset (never a locked or committed
     one), insert it, then attempt to re-home each evicted tasking on another asset. Accepted
     only when net value strictly rises — which is guaranteed, because we only ever evict a set
     whose total weight is strictly less than the tasking we insert.
3. **Iterate (the "distributed feedback" loop).** Perturb the incumbent with a seeded random
   "kick" (evict a few taskings), re-run local search, and keep the result only if it beats the
   best seen. Repeat. The kick is the *only* source of randomness and is drawn from a seeded
   ``random.Random``, so a fixed seed yields a byte-identical schedule.

Why this reads as "iterated / distributed / feedback"
-----------------------------------------------------
Treat each asset as a local scheduler optimising its own timeline. An eviction on one asset
*propagates* a re-home attempt onto the others (the distributed coupling); the search iterates
these local adjustments, each move's objective delta acting as the feedback signal that decides
acceptance, until the system settles. It is genuinely in the spirit of the iterated-local-search
and consensus/auction (CBBA, Contract-Net) task-allocation literature — see the research brief.

Key invariants (asserted by the property-based tests in ``tests/test_properties.py``)
-------------------------------------------------------------------------------------
* The returned schedule is always **feasible** (no double-booking, all windows/eligibility
  respected) — every move is validated through :mod:`app.core.feasibility`.
* The objective is **never worse than the FIFO baseline**, because FIFO is one of the seeds and
  every move is non-worsening.
* The result is **deterministic** for a fixed ``OptimizerConfig.seed``.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .construct import by_release, by_value_density_desc, by_weight_desc, greedy_insert
from .feasibility import find_earliest_start, is_eligible
from .models import (
    Allocation,
    AllocationStatus,
    Interval,
    Request,
    Resource,
    Scenario,
    Schedule,
)
from .objective import schedule_value

# ----------------------------------------------------------------------------------------
# Configuration and result types
# ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OptimizerConfig:
    """Tunables for the optimiser. Defaults are sized for the demo's synthetic instances.

    Attributes:
        seed: RNG seed for the iterated "kick" moves. Fixing it makes the whole run reproducible.
        max_passes: Cap on local-search sweeps per descent (each sweep is INSERT then EVICT
            over all unscheduled taskings; the loop stops early once a sweep finds no move).
        restarts: Number of iterated perturb-and-reoptimise rounds after the first descent.
        kick_strength: How many taskings a perturbation evicts before re-optimising.
    """

    seed: int = 20240117
    max_passes: int = 16
    restarts: int = 24
    kick_strength: int = 3


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """Outcome of an optimisation run, with enough provenance to chart and audit it."""

    schedule: Schedule
    value: float
    seed_value: float
    baseline_value: float
    passes: int
    moves_applied: int
    history: tuple[float, ...]
    elapsed_ms: float
    config: OptimizerConfig

    @property
    def uplift_vs_baseline_pct(self) -> float:
        """Percentage improvement of the optimiser's objective over the FIFO baseline."""
        if self.baseline_value <= 0:
            return 0.0
        return 100.0 * (self.value - self.baseline_value) / self.baseline_value


# ----------------------------------------------------------------------------------------
# Mutable working state for the local search
# ----------------------------------------------------------------------------------------


class _Search:
    """Mutable schedule used during local search.

    Keeping a mutable representation (per-asset sorted allocation lists, a running objective
    value, and the set of placed taskings) lets each move be applied incrementally instead of
    rebuilding immutable :class:`Schedule` objects in the inner loop. We convert back to an
    immutable :class:`Schedule` only at the boundary, in :meth:`to_schedule`.
    """

    __slots__ = ("assigned", "frozen_ids", "now", "request_index", "resources", "value", "weight")

    def __init__(
        self,
        scenario: Scenario,
        allocations: tuple[Allocation, ...],
        *,
        now: int,
        frozen_ids: frozenset[str],
    ) -> None:
        self.now = now
        self.weight: dict[str, float] = {r.id: r.weight for r in scenario.requests}
        self.request_index: dict[str, Request] = scenario.request_index()
        # Assets are held in a stable, sorted order so every scan is deterministic.
        self.resources: list[Resource] = sorted(scenario.resources, key=lambda r: r.id)
        self.frozen_ids = frozen_ids
        self.assigned: dict[str, list[Allocation]] = {r.id: [] for r in self.resources}
        self.value = 0.0
        for alloc in allocations:
            self.assigned.setdefault(alloc.resource_id, []).append(alloc)
            self.value += self.weight.get(alloc.request_id, 0.0)
        for allocs in self.assigned.values():
            allocs.sort(key=lambda a: a.start)

    # -- queries -------------------------------------------------------------------------

    def placed_ids(self) -> set[str]:
        """The set of request ids currently scheduled."""
        return {a.request_id for allocs in self.assigned.values() for a in allocs}

    def occupied(self, resource_id: str) -> list[Interval]:
        """Sorted occupied intervals on one asset."""
        return [a.interval for a in self.assigned[resource_id]]

    def can_evict(self, alloc: Allocation) -> bool:
        """An allocation may be evicted unless it is locked, committed, or otherwise frozen."""
        return not (
            alloc.locked
            or alloc.status is AllocationStatus.COMMITTED
            or alloc.request_id in self.frozen_ids
        )

    # -- mutations -----------------------------------------------------------------------

    def add(self, alloc: Allocation) -> None:
        """Insert an allocation and update the running objective."""
        bucket = self.assigned[alloc.resource_id]
        bucket.append(alloc)
        bucket.sort(key=lambda a: a.start)
        self.value += self.weight.get(alloc.request_id, 0.0)

    def remove(self, alloc: Allocation) -> None:
        """Remove an allocation and update the running objective."""
        self.assigned[alloc.resource_id].remove(alloc)
        self.value -= self.weight.get(alloc.request_id, 0.0)

    def to_schedule(self) -> Schedule:
        """Snapshot the working state as an immutable :class:`Schedule`."""
        allocs = tuple(a for bucket in self.assigned.values() for a in bucket)
        return Schedule(allocations=allocs)

    # -- moves ---------------------------------------------------------------------------

    def try_insert(self, request: Request) -> bool:
        """INSERT move: place ``request`` in the earliest free slot on any eligible asset.

        Returns True (and mutates) iff a feasible slot existed. Strictly increases the objective.
        """
        best_start: int | None = None
        best_resource: str | None = None
        for resource in self.resources:
            if not is_eligible(resource, request):
                continue
            start = find_earliest_start(
                resource, request, self.occupied(resource.id), not_before=self.now
            )
            if start is not None and (best_start is None or start < best_start):
                best_start, best_resource = start, resource.id
        if best_start is None or best_resource is None:
            return False
        self.add(Allocation(request.id, best_resource, best_start, best_start + request.duration))
        return True

    def evict_and_insert(self, request: Request) -> bool:
        """EVICT-AND-INSERT (ejection chain): make room for a high-value tasking.

        Finds, across all eligible assets and candidate start times, the placement whose set of
        colliding allocations has the smallest total weight strictly below ``request``'s weight
        (never evicting a frozen/locked/committed allocation). It evicts that set, inserts
        ``request``, then tries to re-home each evicted tasking elsewhere (the feedback step).

        Because the evicted weight is strictly less than the inserted weight, the move strictly
        increases the objective even before any re-homing succeeds. Returns True iff applied.
        """
        duration = request.duration
        target_weight = self.weight[request.id]

        best: tuple[str, int, list[Allocation], float] | None = None  # (res, start, evict, w)
        for resource in self.resources:
            if not is_eligible(resource, request):
                continue
            allocs = self.assigned[resource.id]
            for window in resource.availability:
                seg_start = max(window.start, request.release, self.now)
                seg_end = min(window.end, request.deadline)
                if seg_end - seg_start < duration:
                    continue
                # The weight of the colliding set is piecewise-constant in the start time and
                # only changes at allocation boundaries, so these anchors cover every distinct
                # eviction set; we need not scan minute by minute.
                candidates = {seg_start, seg_end - duration}
                for alloc in allocs:
                    for anchor in (alloc.end, alloc.start - duration):
                        if seg_start <= anchor <= seg_end - duration:
                            candidates.add(anchor)
                for start in sorted(candidates):
                    block = Interval(start, start + duration)
                    colliding = [a for a in allocs if a.interval.overlaps(block)]
                    if any(not self.can_evict(a) for a in colliding):
                        continue  # cannot disturb a human lock or in-progress work
                    evicted_weight = sum(self.weight[a.request_id] for a in colliding)
                    if evicted_weight >= target_weight:
                        continue  # not a net win
                    if best is None or evicted_weight < best[3] or (
                        evicted_weight == best[3] and start < best[1]
                    ):
                        best = (resource.id, start, colliding, evicted_weight)

        if best is None:
            return False

        resource_id, start, colliding, _ = best
        evicted_requests = [self.request_index[a.request_id] for a in colliding]
        for alloc in colliding:
            self.remove(alloc)
        self.add(Allocation(request.id, resource_id, start, start + duration))
        # Feedback: re-home the evicted taskings on other assets where they now fit.
        for evicted in sorted(evicted_requests, key=by_value_density_desc):
            self.try_insert(evicted)
        return True


# ----------------------------------------------------------------------------------------
# Local search and the iterated outer loop
# ----------------------------------------------------------------------------------------


def _local_search(
    state: _Search, scenario: Scenario, *, max_passes: int
) -> tuple[int, int, list[float]]:
    """Descend to a local optimum. Returns (passes_run, moves_applied, per-pass values)."""
    history: list[float] = []
    moves = 0
    passes = 0
    improved = True
    while improved and passes < max_passes:
        improved = False
        passes += 1
        placed = state.placed_ids()
        pending = [r for r in scenario.requests if r.id not in placed]
        pending.sort(key=by_value_density_desc)

        # INSERT sweep: fill any free space first (cheap, always-improving).
        for request in pending:
            if request.id not in state.placed_ids() and state.try_insert(request):
                moves += 1
                improved = True
        # EVICT-AND-INSERT sweep: trade cheap work for valuable work.
        for request in pending:
            if request.id not in state.placed_ids() and state.evict_and_insert(request):
                moves += 1
                improved = True
        history.append(state.value)
    return passes, moves, history


def _perturb(state: _Search, rng: random.Random, strength: int) -> None:
    """ILS "kick": evict up to ``strength`` random evictable allocations to escape an optimum."""
    evictable = [
        a for allocs in state.assigned.values() for a in allocs if state.can_evict(a)
    ]
    if not evictable:
        return
    rng.shuffle(evictable)
    for alloc in evictable[: min(strength, len(evictable))]:
        state.remove(alloc)


def optimize(
    scenario: Scenario,
    config: OptimizerConfig | None = None,
    *,
    now: int = 0,
    fixed: tuple[Allocation, ...] = (),
) -> OptimizationResult:
    """Optimise ``scenario`` and return the best schedule found plus run provenance.

    Args:
        scenario: The problem instance to solve.
        config: Optimiser tunables; defaults to :class:`OptimizerConfig` (reproducible).
        now: Lower bound on all start times — the live engine passes the current clock so the
            optimiser only ever (re)plans the future.
        fixed: Allocations that must be preserved exactly (locked human decisions and
            in-progress/committed work). They are treated as immovable and never evicted.

    Returns:
        An :class:`OptimizationResult` whose ``schedule`` is feasible and whose ``value`` is
        guaranteed ``>=`` the FIFO baseline value.
    """
    config = config or OptimizerConfig()
    start_time = time.perf_counter()
    frozen_ids = frozenset(a.request_id for a in fixed)

    # 1. Seeds. Build three greedy constructions; the best becomes the incumbent. Including the
    #    FIFO construction here is what makes the >=-baseline guarantee hold by construction.
    fifo_seed = greedy_insert(scenario, by_release, now=now, fixed=fixed)
    baseline_value = schedule_value(scenario, fifo_seed)
    seeds = [
        greedy_insert(scenario, by_value_density_desc, now=now, fixed=fixed),
        greedy_insert(scenario, by_weight_desc, now=now, fixed=fixed),
        fifo_seed,
    ]
    incumbent = max(seeds, key=lambda s: schedule_value(scenario, s))
    seed_value = schedule_value(scenario, incumbent)

    state = _Search(scenario, incumbent.allocations, now=now, frozen_ids=frozen_ids)
    history: list[float] = [seed_value]

    # 2. First descent to a local optimum.
    passes, moves, descent_history = _local_search(state, scenario, max_passes=config.max_passes)
    history.extend(descent_history)

    best_schedule = state.to_schedule()
    best_value = state.value

    # 3. Iterated local search: perturb, re-optimise, keep the best seen. Deterministic per seed.
    rng = random.Random(config.seed)
    for _ in range(config.restarts):
        _perturb(state, rng, config.kick_strength)
        p, m, _ = _local_search(state, scenario, max_passes=config.max_passes)
        passes += p
        moves += m
        if state.value > best_value + 1e-9:
            best_value = state.value
            best_schedule = state.to_schedule()
        else:
            # Reject the kick: restore the incumbent before the next perturbation.
            state = _Search(
                scenario, best_schedule.allocations, now=now, frozen_ids=frozen_ids
            )
        history.append(best_value)

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    # Safety net for the headline guarantee: never return below the FIFO baseline.
    if best_value < baseline_value:  # pragma: no cover - unreachable given FIFO is a seed
        best_schedule, best_value = fifo_seed, baseline_value

    return OptimizationResult(
        schedule=best_schedule,
        value=best_value,
        seed_value=seed_value,
        baseline_value=baseline_value,
        passes=passes,
        moves_applied=moves,
        history=tuple(history),
        elapsed_ms=elapsed_ms,
        config=config,
    )
