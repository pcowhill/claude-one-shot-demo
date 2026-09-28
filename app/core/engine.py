"""The event-driven allocation engine — orchestration, modes, and human-in-the-loop control.

This is the layer that turns a static optimiser into a live, streaming operations system. It
advances a simulated clock minute by minute; as events arrive (taskings appear, an asset drops
offline, a tasking is re-prioritised, or a human issues a command) it **re-optimises the
remaining horizon** around everything already committed or locked. That rolling-horizon,
re-solve-on-event pattern is the production-realistic way to run optimisation in a changing
world (see the research brief).

It supports the three operating modes Roster-5 calls out for Iterated Distributed Feedback:

* :attr:`Mode.AUTOMATED` — fully automated closed loop: every event re-optimises and applies.
* :attr:`Mode.MANUAL`    — human-in-the-loop: re-optimisation produces a *proposed* plan that a
  person must approve; the operator can also lock, override, or hold individual taskings.
* :attr:`Mode.HYBRID`    — automated by default, but high-impact changes (a dropped FLASH
  tasking, outage-forced re-homing) are surfaced as exceptions for a human to notice.

Invariants the engine preserves:

* In-progress / committed work is immutable — the optimiser only ever (re)plans the future
  (``now = clock``), so nothing is rescheduled into the past.
* Human **locks** and **overrides** are hard constraints honoured by every re-optimisation.
* Outages never disturb work already in progress (the cut is clipped past committed tasks).
"""

from __future__ import annotations

from dataclasses import replace
from enum import Enum

from .baseline import fifo_schedule
from .feasibility import is_eligible, placement_is_feasible
from .models import (
    Allocation,
    AllocationStatus,
    Event,
    EventType,
    Interval,
    OutagePayload,
    Priority,
    PriorityChangePayload,
    Request,
    Resource,
    Scenario,
    Schedule,
)
from .objective import compute_kpis, schedule_value
from .optimizer import OptimizationResult, OptimizerConfig, optimize

# Priorities the HYBRID mode treats as exception-worthy if they go unserved.
_HIGH_PRIORITY = frozenset({Priority.IMMEDIATE, Priority.FLASH})
_MAX_EVENT_LOG = 60


class Mode(str, Enum):
    """Operating mode for the engine — the human-autonomy spectrum."""

    AUTOMATED = "AUTOMATED"
    MANUAL = "MANUAL"
    HYBRID = "HYBRID"


class Policy(str, Enum):
    """Which scheduler the engine re-plans with.

    The default is the optimiser. ``FIFO`` exists so a *shadow* engine can be run in lockstep on
    the identical event stream, giving an honest, fully-online "what if we'd just used FIFO?"
    baseline for the dashboard's value-delivered comparison.
    """

    OPTIMIZE = "OPTIMIZE"
    FIFO = "FIFO"


def _subtract_interval(windows: tuple[Interval, ...], cut: Interval) -> tuple[Interval, ...]:
    """Remove ``cut`` from a set of availability windows, returning the remaining online time."""
    remaining: list[Interval] = []
    for window in windows:
        if not window.overlaps(cut):
            remaining.append(window)
            continue
        if window.start < cut.start:
            remaining.append(Interval(window.start, cut.start))
        if cut.end < window.end:
            remaining.append(Interval(cut.end, window.end))
    return tuple(sorted(remaining, key=lambda i: i.start))


class AllocationEngine:
    """A live, event-driven scheduler over one scenario.

    Typical use: construct with a scenario and a mode, then drive the clock with :meth:`tick`
    (one simulated minute) or :meth:`run_to_completion` (the whole shift). At any point,
    :meth:`to_snapshot` returns a JSON-friendly view for the dashboard, and the HITL methods
    (:meth:`lock`, :meth:`override`, :meth:`hold`, :meth:`approve`, :meth:`set_mode`) let a
    human steer it.
    """

    def __init__(
        self,
        scenario: Scenario,
        *,
        mode: Mode = Mode.AUTOMATED,
        policy: Policy = Policy.OPTIMIZE,
        optimizer_config: OptimizerConfig | None = None,
    ) -> None:
        self.scenario = scenario
        self.mode = mode
        self.policy = policy
        self.optimizer_config = optimizer_config or OptimizerConfig()

        self.clock: int = 0
        self.resources: dict[str, Resource] = {r.id: r for r in scenario.resources}
        self.known_requests: dict[str, Request] = {}
        self.committed: dict[str, Allocation] = {}
        self.locks: dict[str, Allocation] = {}
        self.held: set[str] = set()  # operator-held / rejected taskings, excluded from planning

        self.schedule: Schedule = Schedule(())
        self.proposed: Schedule | None = None
        self.last_result: OptimizationResult | None = None
        self.flags: list[str] = []
        self.event_log: list[Event] = []

        self._pending_events: list[Event] = sorted(
            scenario.events, key=lambda e: (e.time, e.description)
        )
        self._dirty = True  # force an initial optimisation at t=0

        # Bootstrap so the board is never empty: compute and apply the opening plan even in
        # MANUAL (subsequent changes will then require approval).
        self._reoptimize(bootstrap=True)
        self._dirty = False

    # ------------------------------------------------------------------------------------
    # Instance assembly
    # ------------------------------------------------------------------------------------

    def _current_instance(self) -> Scenario:
        """A scenario snapshot of what is *known right now*: live assets + revealed taskings."""
        requests = tuple(
            req for rid, req in sorted(self.known_requests.items()) if rid not in self.held
        )
        return Scenario(
            name=self.scenario.name,
            horizon=self.scenario.horizon,
            seed=self.scenario.seed,
            resources=tuple(self.resources[rid] for rid in sorted(self.resources)),
            requests=requests,
        )

    def _fixed_allocations(self) -> tuple[Allocation, ...]:
        """Allocations the optimiser must preserve: committed work plus human locks."""
        fixed = dict(self.committed)
        for rid, alloc in self.locks.items():
            fixed.setdefault(rid, alloc)
        return tuple(fixed.values())

    # ------------------------------------------------------------------------------------
    # Re-optimisation and application
    # ------------------------------------------------------------------------------------

    def _reoptimize(self, *, bootstrap: bool = False) -> None:
        """Re-solve the remaining horizon and either apply or propose the result per mode."""
        instance = self._current_instance()
        fixed = self._fixed_allocations()

        if self.policy is Policy.FIFO:
            # Shadow-baseline mode: re-plan with plain FIFO so this engine reproduces, fully
            # online, what a naive dispatcher would have delivered on the same event stream.
            schedule = fifo_schedule(instance, now=self.clock, fixed=fixed)
            value = schedule_value(instance, schedule)
            self.last_result = OptimizationResult(
                schedule=schedule,
                value=value,
                seed_value=value,
                baseline_value=value,
                passes=0,
                moves_applied=0,
                history=(value,),
                elapsed_ms=0.0,
                config=self.optimizer_config,
            )
            self._apply(schedule)
            return

        result = optimize(instance, self.optimizer_config, now=self.clock, fixed=fixed)
        self.last_result = result

        if self.mode is Mode.MANUAL and not bootstrap:
            # Human-in-the-loop: hold the new plan for approval rather than applying it.
            self.proposed = self._stamp(result.schedule)
            return

        self._apply(result.schedule)
        self.flags = (
            self._compute_flags(instance, result.schedule) if self.mode is Mode.HYBRID else []
        )

    def _stamp(self, schedule: Schedule) -> Schedule:
        """Annotate each allocation with its lifecycle status and lock flag for display."""
        stamped: list[Allocation] = []
        for alloc in schedule.allocations:
            locked = alloc.request_id in self.locks
            if alloc.start <= self.clock < alloc.end:
                status = AllocationStatus.COMMITTED
            elif alloc.end <= self.clock:
                status = AllocationStatus.COMPLETED
            elif alloc.start <= self.clock:
                status = AllocationStatus.COMMITTED
            else:
                status = AllocationStatus.PLANNED
            stamped.append(replace(alloc, locked=locked, status=status))
        return Schedule(tuple(stamped))

    def _apply(self, schedule: Schedule) -> None:
        """Make ``schedule`` the live plan and clear any pending proposal."""
        self.schedule = self._stamp(schedule)
        self.proposed = None

    def _compute_flags(self, instance: Scenario, schedule: Schedule) -> list[str]:
        """HYBRID exceptions: high-priority taskings the optimiser had to drop."""
        scheduled = schedule.scheduled_request_ids()
        return [
            f"{req.priority.name} tasking {req.id} unserved — review"
            for req in instance.requests
            if req.priority in _HIGH_PRIORITY and req.id not in scheduled
        ]

    # ------------------------------------------------------------------------------------
    # Event handling and the simulated clock
    # ------------------------------------------------------------------------------------

    def _handle_event(self, event: Event) -> None:
        """Mutate live state in response to a single stream event."""
        if event.type is EventType.REQUEST_ARRIVED and isinstance(event.payload, Request):
            self.known_requests[event.payload.id] = event.payload
            self._dirty = True
        elif event.type is EventType.RESOURCE_OFFLINE and isinstance(event.payload, OutagePayload):
            self._apply_outage(event.payload)
            self._dirty = True
        elif event.type is EventType.PRIORITY_CHANGED and isinstance(
            event.payload, PriorityChangePayload
        ):
            self._apply_priority_change(event.payload)
            self._dirty = True
        self._log_event(event)

    def _apply_outage(self, payload: OutagePayload) -> None:
        """Apply an unplanned outage, protecting in-progress work and invalidating bad locks."""
        resource = self.resources.get(payload.resource_id)
        if resource is None:
            return
        # Do not cut into work already running on this asset, nor into the past.
        in_progress_end = max(
            (
                a.end
                for a in self.committed.values()
                if a.resource_id == resource.id and a.start <= self.clock < a.end
            ),
            default=payload.window.start,
        )
        cut_start = max(payload.window.start, in_progress_end, self.clock)
        if cut_start >= payload.window.end:
            return
        cut = Interval(cut_start, payload.window.end)
        self.resources[resource.id] = replace(
            resource, availability=_subtract_interval(resource.availability, cut)
        )
        # Any human lock that the outage makes infeasible is released for re-decision.
        for rid, alloc in list(self.locks.items()):
            if alloc.resource_id == resource.id and alloc.interval.overlaps(cut):
                del self.locks[rid]
                self.flags.append(f"Lock on {rid} released — invalidated by {resource.id} outage")

    def _apply_priority_change(self, payload: PriorityChangePayload) -> None:
        """Re-task: bump a known tasking's priority/weight (a frozen Request is replaced)."""
        req = self.known_requests.get(payload.request_id)
        if req is None:
            return
        self.known_requests[payload.request_id] = replace(
            req, priority=payload.priority, weight=payload.weight
        )

    def _advance_statuses(self) -> None:
        """Promote allocations to COMMITTED/COMPLETED as the clock passes their boundaries."""
        updated: list[Allocation] = []
        for alloc in self.schedule.allocations:
            if alloc.end <= self.clock:
                status = AllocationStatus.COMPLETED
            elif alloc.start <= self.clock:
                status = AllocationStatus.COMMITTED
                self.committed[alloc.request_id] = replace(alloc, status=status)
            else:
                status = alloc.status
            updated.append(replace(alloc, status=status))
        self.schedule = Schedule(tuple(updated))

    def _log_event(self, event: Event) -> None:
        """Append to the rolling event log shown on the dashboard."""
        self.event_log.append(event)
        if len(self.event_log) > _MAX_EVENT_LOG:
            self.event_log = self.event_log[-_MAX_EVENT_LOG:]

    def tick(self) -> None:
        """Advance the simulated clock by one minute, processing events and re-optimising."""
        if self.is_finished:
            return
        triggered = False
        while self._pending_events and self._pending_events[0].time <= self.clock:
            self._handle_event(self._pending_events.pop(0))
            triggered = True
        if triggered or self._dirty:
            self._reoptimize()
            self._dirty = False
        self._advance_statuses()
        self.clock += 1

    @property
    def is_finished(self) -> bool:
        """True once the clock has run out and no events remain to process."""
        return self.clock >= self.scenario.horizon and not self._pending_events

    def current_instance(self) -> Scenario:
        """Public view of the currently-known problem instance (live assets + revealed work)."""
        return self._current_instance()

    def delivered_value(self) -> float:
        """Total mission value the live plan has secured against the revealed taskings so far."""
        return schedule_value(self._current_instance(), self.schedule)

    def run_to_completion(self) -> None:
        """Drive the clock to the end of the horizon (used by tests and offline runs)."""
        guard = self.scenario.horizon + 2
        while not self.is_finished and guard > 0:
            self.tick()
            guard -= 1

    # ------------------------------------------------------------------------------------
    # Human-in-the-loop controls
    # ------------------------------------------------------------------------------------

    def set_mode(self, mode: Mode) -> None:
        """Switch operating mode and re-evaluate the plan under the new policy."""
        self.mode = mode
        self._reoptimize()

    def lock(self, request_id: str) -> bool:
        """Pin a tasking's current allocation so re-optimisation must keep it exactly."""
        alloc = self.schedule.for_request(request_id)
        if alloc is None:
            return False
        self.locks[request_id] = replace(alloc, locked=True)
        self._reoptimize()
        return True

    def unlock(self, request_id: str) -> bool:
        """Release a previously locked tasking back to the optimiser's discretion."""
        if request_id not in self.locks:
            return False
        del self.locks[request_id]
        self._reoptimize()
        return True

    def override(self, request_id: str, resource_id: str, start: int) -> bool:
        """Operator override: force a tasking onto a specific asset at a specific time.

        The placement is validated against current availability, the tasking's window, and all
        committed/locked work; an infeasible override is rejected (returns False) rather than
        corrupting the plan. A successful override is locked in as a hard constraint.
        """
        req = self.known_requests.get(request_id)
        resource = self.resources.get(resource_id)
        if req is None or resource is None or req.id in self.held:
            return False
        # Build the set of intervals the override must avoid (everything fixed except this req).
        occupied = [
            a.interval
            for a in (*self.committed.values(), *self.locks.values())
            if a.resource_id == resource_id and a.request_id != request_id
        ]
        if not placement_is_feasible(resource, req, start, occupied):
            return False
        self.locks[request_id] = Allocation(
            request_id, resource_id, start, start + req.duration, locked=True
        )
        self._reoptimize()
        return True

    def hold(self, request_id: str) -> bool:
        """Operator decision to *not* service a tasking (remove it from planning)."""
        if request_id in self.committed:
            return False  # cannot un-task work already in progress
        self.held.add(request_id)
        self.locks.pop(request_id, None)
        self._reoptimize()
        return True

    def release_hold(self, request_id: str) -> bool:
        """Return a held tasking to the planning pool."""
        if request_id not in self.held:
            return False
        self.held.discard(request_id)
        self._reoptimize()
        return True

    def approve(self) -> bool:
        """Approve and apply the pending proposed plan (MANUAL mode)."""
        if self.proposed is None:
            return False
        self._apply(self.proposed)
        return True

    def reject_proposal(self) -> bool:
        """Discard the pending proposed plan, keeping the current one (MANUAL mode)."""
        if self.proposed is None:
            return False
        self.proposed = None
        return True

    # ------------------------------------------------------------------------------------
    # Snapshot for the API / dashboard
    # ------------------------------------------------------------------------------------

    def to_snapshot(self) -> dict[str, object]:
        """A complete, JSON-serialisable view of the engine state for the UI and report."""
        instance = self._current_instance()
        req_index = {r.id: r for r in self.scenario.requests}
        req_index.update(self.known_requests)  # priority bumps override the original

        # Compare like-for-like: FIFO planning the *same* problem the optimiser just solved —
        # identical revealed taskings, identical committed/locked work, identical clock. This is
        # exactly the baseline the optimiser guarantees it will never fall below, so the live
        # uplift is fair and provably non-negative (same methodology as the offline benchmark).
        fixed = self._fixed_allocations()
        kpis = compute_kpis(instance, self.schedule)
        baseline = fifo_schedule(instance, now=self.clock, fixed=fixed)
        baseline_kpis = compute_kpis(instance, baseline)
        opt_value = (
            self.last_result.value if self.last_result else schedule_value(instance, self.schedule)
        )
        base_value = (
            self.last_result.baseline_value
            if self.last_result
            else schedule_value(instance, baseline)
        )

        allocations = [self._allocation_view(a, req_index) for a in self.schedule.allocations]
        allocations.sort(key=lambda a: (a["resource_id"], a["start"]))

        scheduled_ids = self.schedule.scheduled_request_ids()
        pending_requests = [
            req
            for req in instance.requests
            if req.id not in scheduled_ids and req.release <= self.clock
        ]
        # Sort the typed requests (value first, then urgency) before projecting to view dicts.
        pending_requests.sort(key=lambda req: (-req.weight, req.deadline))
        pending = [self._request_view(req) for req in pending_requests]

        return {
            "scenario": {
                "name": self.scenario.name,
                "horizon": self.scenario.horizon,
                "seed": self.scenario.seed,
            },
            "clock": self.clock,
            "mode": self.mode.value,
            "finished": self.is_finished,
            "resources": [
                self._resource_view(self.resources[rid]) for rid in sorted(self.resources)
            ],
            "allocations": allocations,
            "pending": pending,
            "kpis": kpis.to_dict(),
            "baseline_kpis": baseline_kpis.to_dict(),
            "uplift": {
                "optimizer_value": round(opt_value, 1),
                "baseline_value": round(base_value, 1),
                "uplift_pct": round(100.0 * (opt_value - base_value) / base_value, 1)
                if base_value > 0
                else 0.0,
            },
            "optimizer": self._optimizer_view(),
            "events": [
                {"time": e.time, "type": e.type.value, "description": e.description}
                for e in reversed(self.event_log)
            ],
            "flags": list(self.flags),
            "locks": sorted(self.locks),
            "held": sorted(self.held),
            "proposed": self._proposed_view(req_index),
        }

    # -- view helpers --------------------------------------------------------------------

    def _allocation_view(
        self, alloc: Allocation, req_index: dict[str, Request]
    ) -> dict[str, object]:
        req = req_index.get(alloc.request_id)
        return {
            "request_id": alloc.request_id,
            "resource_id": alloc.resource_id,
            "start": alloc.start,
            "end": alloc.end,
            "status": alloc.status.value,
            "locked": alloc.locked,
            "name": req.name if req else alloc.request_id,
            "priority": req.priority.name if req else "ROUTINE",
            "weight": req.weight if req else 0.0,
            "capability": req.capability.value if req else "",
            "region": (req.region if req and req.region else "ANY"),
        }

    def _request_view(self, req: Request) -> dict[str, object]:
        eligible = [r.id for r in self.resources.values() if is_eligible(r, req)]
        return {
            "request_id": req.id,
            "name": req.name,
            "priority": req.priority.name,
            "weight": req.weight,
            "capability": req.capability.value,
            "region": req.region or "ANY",
            "release": req.release,
            "deadline": req.deadline,
            "duration": req.duration,
            "eligible_resources": eligible,
        }

    def _resource_view(self, resource: Resource) -> dict[str, object]:
        return {
            "id": resource.id,
            "name": resource.name,
            "kind": resource.kind.value,
            "capabilities": sorted(c.value for c in resource.capabilities),
            "regions": sorted(resource.regions),
            "availability": [[w.start, w.end] for w in resource.availability],
        }

    def _optimizer_view(self) -> dict[str, object]:
        if self.last_result is None:
            return {}
        r = self.last_result
        return {
            "value": round(r.value, 1),
            "seed_value": round(r.seed_value, 1),
            "baseline_value": round(r.baseline_value, 1),
            "uplift_vs_baseline_pct": round(r.uplift_vs_baseline_pct, 1),
            "passes": r.passes,
            "moves_applied": r.moves_applied,
            "elapsed_ms": round(r.elapsed_ms, 1),
            "history": [round(v, 1) for v in r.history],
        }

    def _proposed_view(self, req_index: dict[str, Request]) -> dict[str, object] | None:
        if self.proposed is None:
            return None
        current_ids = self.schedule.scheduled_request_ids()
        proposed_ids = self.proposed.scheduled_request_ids()
        return {
            "allocations": [self._allocation_view(a, req_index) for a in self.proposed.allocations],
            "added": sorted(proposed_ids - current_ids),
            "removed": sorted(current_ids - proposed_ids),
            "change_count": len(proposed_ids ^ current_ids),
        }
