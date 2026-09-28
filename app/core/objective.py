"""Objective function and operational KPIs.

The objective the optimiser maximises is deliberately simple and explainable:

    maximise  Σ  weight(j)   over taskings j that are serviced on time.

Every serviced tasking finishes inside its ``[release, deadline)`` window by construction (the
feasibility rules forbid anything else), so "value delivered" and "weighted on-time value" are
the same number — there is no partial credit and no lateness, only *served* vs *missed*. That
makes the proof-of-value story crisp: the optimiser's job is to miss as little high-value work
as possible given limited assets.

:func:`compute_kpis` derives the operator-facing metrics (value capture, fill rate, asset
utilisation, per-priority breakdown) that the dashboard and the benchmark charts display.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Priority, Scenario, Schedule

# ----------------------------------------------------------------------------------------
# The objective
# ----------------------------------------------------------------------------------------


def schedule_value(scenario: Scenario, schedule: Schedule) -> float:
    """Total mission value delivered — the objective the optimiser maximises.

    Sums the weight of every tasking that has an allocation. Because feasibility guarantees
    on-time completion, this is exactly the weighted on-time value.
    """
    requests = scenario.request_index()
    return sum(
        requests[a.request_id].weight for a in schedule.allocations if a.request_id in requests
    )


# ----------------------------------------------------------------------------------------
# KPI report
# ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KpiReport:
    """Operator-facing performance summary for one schedule on one scenario."""

    total_value: float
    possible_value: float
    value_capture_pct: float
    served_count: int
    total_count: int
    served_pct: float
    mean_utilization_pct: float
    utilization_by_resource: dict[str, float] = field(default_factory=dict)
    value_by_priority: dict[str, float] = field(default_factory=dict)
    served_by_priority: dict[str, tuple[int, int]] = field(default_factory=dict)
    dropped_request_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly representation for the API and report generator."""
        return {
            "total_value": round(self.total_value, 2),
            "possible_value": round(self.possible_value, 2),
            "value_capture_pct": round(self.value_capture_pct, 1),
            "served_count": self.served_count,
            "total_count": self.total_count,
            "served_pct": round(self.served_pct, 1),
            "mean_utilization_pct": round(self.mean_utilization_pct, 1),
            "utilization_by_resource": {
                k: round(v, 1) for k, v in self.utilization_by_resource.items()
            },
            "value_by_priority": {k: round(v, 2) for k, v in self.value_by_priority.items()},
            "served_by_priority": {
                k: list(v) for k, v in self.served_by_priority.items()
            },
            "dropped_request_ids": list(self.dropped_request_ids),
        }


def compute_kpis(scenario: Scenario, schedule: Schedule) -> KpiReport:
    """Derive the full KPI report for ``schedule`` against ``scenario``.

    Utilisation is busy-minutes / online-minutes per asset (idle assets and assets with no
    availability report 0%). The per-priority breakdown is what lets a program manager see at
    a glance that the optimiser protects FLASH/IMMEDIATE work first.
    """
    scheduled_ids = schedule.scheduled_request_ids()

    total_value = schedule_value(scenario, schedule)
    possible_value = scenario.total_possible_value
    total_count = len(scenario.requests)
    served_count = len(scheduled_ids)

    # Utilisation per asset.
    busy: dict[str, int] = {r.id: 0 for r in scenario.resources}
    for alloc in schedule.allocations:
        busy[alloc.resource_id] = busy.get(alloc.resource_id, 0) + (alloc.end - alloc.start)
    utilization: dict[str, float] = {}
    for resource in scenario.resources:
        available = resource.total_available_minutes
        utilization[resource.id] = (100.0 * busy[resource.id] / available) if available else 0.0
    mean_util = sum(utilization.values()) / len(utilization) if utilization else 0.0

    # Per-priority breakdowns.
    value_by_priority: dict[str, float] = {p.name: 0.0 for p in Priority}
    served_by_priority: dict[str, list[int]] = {p.name: [0, 0] for p in Priority}
    for req in scenario.requests:
        tier = req.priority.name
        served_by_priority[tier][1] += 1
        if req.id in scheduled_ids:
            served_by_priority[tier][0] += 1
            value_by_priority[tier] += req.weight

    dropped = tuple(sorted(r.id for r in scenario.requests if r.id not in scheduled_ids))

    return KpiReport(
        total_value=total_value,
        possible_value=possible_value,
        value_capture_pct=(100.0 * total_value / possible_value) if possible_value else 0.0,
        served_count=served_count,
        total_count=total_count,
        served_pct=(100.0 * served_count / total_count) if total_count else 0.0,
        mean_utilization_pct=mean_util,
        utilization_by_resource=utilization,
        value_by_priority=value_by_priority,
        served_by_priority={k: (v[0], v[1]) for k, v in served_by_priority.items()},
        dropped_request_ids=dropped,
    )
