"""Integration tests for the event-driven engine: modes, commitment, outages, and HITL."""

from __future__ import annotations

import json

from app.core.engine import AllocationEngine, Mode, Policy
from app.core.feasibility import schedule_violations
from app.core.models import AllocationStatus
from app.core.scenario import default_live_scenario


def _run(mode: Mode = Mode.AUTOMATED, policy: Policy = Policy.OPTIMIZE) -> AllocationEngine:
    engine = AllocationEngine(default_live_scenario(), mode=mode, policy=policy)
    engine.run_to_completion()
    return engine


def test_engine_completes_and_is_feasible_in_all_modes() -> None:
    for mode in (Mode.AUTOMATED, Mode.HYBRID, Mode.MANUAL):
        engine = _run(mode)
        assert engine.is_finished
        # The final live plan is feasible against the final (post-outage) asset state.
        assert schedule_violations(engine.current_instance(), engine.schedule) == []


def test_committed_work_is_immovable() -> None:
    engine = AllocationEngine(default_live_scenario(), mode=Mode.AUTOMATED)
    # Advance to mid-shift and snapshot the committed allocations.
    for _ in range(180):
        engine.tick()
    committed = {
        a.request_id: (a.resource_id, a.start, a.end)
        for a in engine.schedule.allocations
        if a.status is AllocationStatus.COMMITTED
    }
    assert committed, "expected some committed work by mid-shift"
    for _ in range(120):
        engine.tick()
    # Everything that was committed remains, unchanged, in the final plan.
    final = {a.request_id: (a.resource_id, a.start, a.end) for a in engine.schedule.allocations}
    for rid, placement in committed.items():
        assert final.get(rid) == placement


def test_delivered_value_is_monotonic() -> None:
    engine = AllocationEngine(default_live_scenario(), mode=Mode.AUTOMATED)
    last = 0.0
    for _ in range(0, 480, 20):
        for _ in range(20):
            engine.tick()
        # Committed value can only grow; the live plan's value should not collapse below it.
        committed_value = sum(
            engine.current_instance().request_index()[a.request_id].weight
            for a in engine.schedule.allocations
            if a.status in (AllocationStatus.COMMITTED, AllocationStatus.COMPLETED)
            and a.request_id in engine.current_instance().request_index()
        )
        assert committed_value >= last - 1e-6
        last = committed_value


def test_lock_is_honoured_by_reoptimization() -> None:
    engine = AllocationEngine(default_live_scenario(), mode=Mode.AUTOMATED)
    for _ in range(90):
        engine.tick()
    planned = next(
        (a for a in engine.schedule.allocations if a.status is AllocationStatus.PLANNED), None
    )
    assert planned is not None
    placement = (planned.resource_id, planned.start, planned.end)
    assert engine.lock(planned.request_id)
    for _ in range(40):
        engine.tick()
    kept = engine.schedule.for_request(planned.request_id)
    assert kept is not None
    assert (kept.resource_id, kept.start, kept.end) == placement


def test_hold_removes_tasking_from_plan() -> None:
    engine = AllocationEngine(default_live_scenario(), mode=Mode.AUTOMATED)
    for _ in range(120):
        engine.tick()
    scheduled = next(
        (a for a in engine.schedule.allocations if a.status is AllocationStatus.PLANNED), None
    )
    if scheduled is None:
        return  # nothing planned to hold at this instant; acceptable
    assert engine.hold(scheduled.request_id)
    assert engine.schedule.for_request(scheduled.request_id) is None
    assert scheduled.request_id in engine.held


def test_override_validates_placement() -> None:
    engine = AllocationEngine(default_live_scenario(), mode=Mode.AUTOMATED)
    for _ in range(60):
        engine.tick()
    # A non-existent resource is rejected.
    some_request = next(iter(engine.known_requests))
    assert not engine.override(some_request, "DOES-NOT-EXIST", engine.clock)


def test_outage_keeps_schedule_feasible() -> None:
    # The default scenario injects unplanned outages; the engine must stay feasible through them.
    engine = AllocationEngine(default_live_scenario(), mode=Mode.AUTOMATED)
    engine.run_to_completion()
    # Re-validate against the final, reduced availability.
    assert schedule_violations(engine.current_instance(), engine.schedule) == []


def test_manual_mode_requires_approval() -> None:
    engine = AllocationEngine(default_live_scenario(), mode=Mode.MANUAL)
    for _ in range(120):
        engine.tick()
    snap = engine.to_snapshot()
    # In MANUAL, re-optimisation accumulates a proposal rather than auto-applying it.
    if snap["proposed"] is not None:
        assert engine.approve()
        assert engine.to_snapshot()["proposed"] is None


def test_snapshot_is_json_serializable() -> None:
    engine = AllocationEngine(default_live_scenario(), mode=Mode.HYBRID)
    for _ in range(100):
        engine.tick()
    snap = engine.to_snapshot()
    # Must round-trip through JSON for the API/report.
    restored = json.loads(json.dumps(snap, default=str))
    for key in ("clock", "mode", "resources", "allocations", "kpis", "optimizer", "events"):
        assert key in restored
