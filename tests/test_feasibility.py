"""Unit tests for interval geometry, eligibility, and placement feasibility."""

from __future__ import annotations

from app.core.feasibility import (
    find_earliest_start,
    is_eligible,
    placement_is_feasible,
    schedule_violations,
)
from app.core.models import (
    Allocation,
    Capability,
    Interval,
    Priority,
    Request,
    Resource,
    ResourceKind,
    Scenario,
    Schedule,
)


def _resource(windows: tuple[Interval, ...], caps=(Capability.EO,), regions=("NORTH",)) -> Resource:
    return Resource("R1", "R1", ResourceKind.UAV, frozenset(caps), frozenset(regions), windows)


def _request(duration=30, release=0, deadline=480, cap=Capability.EO, region="NORTH") -> Request:
    return Request("J1", "J1", cap, region, 10.0, Priority.ROUTINE, release, deadline, duration)


def test_interval_overlap_is_half_open() -> None:
    # Touching intervals do NOT overlap (back-to-back tasking is legal).
    assert not Interval(0, 10).overlaps(Interval(10, 20))
    assert Interval(0, 11).overlaps(Interval(10, 20))
    assert Interval(0, 20).contains(Interval(5, 15))
    assert not Interval(0, 20).contains(Interval(15, 25))


def test_eligibility_requires_capability_and_region() -> None:
    res = _resource((Interval(0, 480),), caps=(Capability.EO,), regions=("NORTH",))
    assert is_eligible(res, _request(cap=Capability.EO, region="NORTH"))
    assert not is_eligible(res, _request(cap=Capability.SAR, region="NORTH"))
    assert not is_eligible(res, _request(cap=Capability.EO, region="SOUTH"))
    # A region-agnostic tasking only needs the capability.
    assert is_eligible(res, _request(cap=Capability.EO, region=None))


def test_find_earliest_start_in_empty_resource() -> None:
    res = _resource((Interval(60, 480),))
    # Release is 0 but the asset is not online until 60.
    assert find_earliest_start(res, _request(duration=30, release=0), []) == 60


def test_find_earliest_start_respects_not_before() -> None:
    res = _resource((Interval(0, 480),))
    assert find_earliest_start(res, _request(duration=30), [], not_before=100) == 100


def test_find_earliest_start_fits_into_gap() -> None:
    res = _resource((Interval(0, 480),))
    occupied = [Interval(0, 50), Interval(80, 120)]
    # A 30-min task does not fit in [50,80) (gap 30 — actually exactly fits), check exact-fit gap.
    assert find_earliest_start(res, _request(duration=30), occupied) == 50
    # A 31-min task cannot fit [50,80); next free is after 120.
    assert find_earliest_start(res, _request(duration=31), occupied) == 120


def test_find_earliest_start_returns_none_when_no_room() -> None:
    res = _resource((Interval(0, 60),))
    occupied = [Interval(0, 60)]
    assert find_earliest_start(res, _request(duration=10), occupied) is None
    # Window too small for the duration (deadline far away, but the asset is only online briefly).
    small = _resource((Interval(0, 30),))
    assert find_earliest_start(small, _request(duration=40, release=0, deadline=480), []) is None


def test_find_earliest_start_returns_none_for_ineligible_asset() -> None:
    # Time is free, but the asset lacks the capability — no legal placement.
    res = _resource((Interval(0, 480),), caps=(Capability.IR,))
    assert find_earliest_start(res, _request(cap=Capability.EO), []) is None


def test_find_earliest_start_spans_only_within_single_window() -> None:
    # A task may not straddle a gap between two availability windows.
    res = _resource((Interval(0, 40), Interval(60, 200)))
    # A 45-min task cannot fit the 40-min first window, so it lands at the start of the second.
    assert find_earliest_start(res, _request(duration=45), []) == 60


def test_placement_feasible_checks_all_rules() -> None:
    res = _resource((Interval(0, 100),))
    req = _request(duration=30, release=10, deadline=100)
    assert placement_is_feasible(res, req, 10, [])
    assert not placement_is_feasible(res, req, 5, [])  # before release
    assert not placement_is_feasible(res, req, 80, [])  # would end at 110 > deadline/window
    assert not placement_is_feasible(res, req, 10, [Interval(20, 40)])  # overlap


def test_schedule_violations_detects_double_booking() -> None:
    res = _resource((Interval(0, 480),))
    req_a = Request("A", "A", Capability.EO, "NORTH", 1.0, Priority.ROUTINE, 0, 480, 30)
    req_b = Request("B", "B", Capability.EO, "NORTH", 1.0, Priority.ROUTINE, 0, 480, 30)
    scenario = Scenario("s", 480, 0, (res,), (req_a, req_b))
    overlapping = Schedule((Allocation("A", "R1", 0, 30), Allocation("B", "R1", 15, 45)))
    violations = schedule_violations(scenario, overlapping)
    assert any("double-booked" in v for v in violations)

    legal = Schedule((Allocation("A", "R1", 0, 30), Allocation("B", "R1", 30, 60)))
    assert schedule_violations(scenario, legal) == []
