"""Naive baseline schedulers — the bar the optimiser must clear.

A proof of value is only as convincing as the baseline it beats, so we provide two honest,
non-strawman dispatchers built on the *same* placement engine as the optimiser:

* :func:`fifo_schedule` — first-in-first-out, the canonical "naive" operations policy: serve
  taskings in the order they arrive, each to whichever eligible asset frees up earliest.
* :func:`priority_greedy_schedule` — a smarter baseline that sorts by mission value first.
  Beating *this* is the harder, more meaningful test, so the benchmark reports both.

Both are deterministic and feasible by construction.
"""

from __future__ import annotations

from collections.abc import Sequence

from .construct import by_release, by_weight_desc, greedy_insert
from .models import Allocation, Scenario, Schedule


def fifo_schedule(
    scenario: Scenario,
    *,
    now: int = 0,
    fixed: Sequence[Allocation] = (),
) -> Schedule:
    """First-in-first-out dispatch: serve taskings in arrival order.

    This is the "naive baseline" the headline benchmark compares against — it mimics an
    operator working a queue strictly in the order requests land, with no global view of
    which trade-offs would deliver more total mission value.
    """
    return greedy_insert(scenario, by_release, now=now, fixed=fixed)


def priority_greedy_schedule(
    scenario: Scenario,
    *,
    now: int = 0,
    fixed: Sequence[Allocation] = (),
) -> Schedule:
    """Greedy-by-priority dispatch: serve the highest-weight taskings first.

    A stronger, still-myopic baseline. It captures the obvious "do the important ones first"
    heuristic but, lacking any look-back or ejection, still strands value whenever an early
    greedy choice blocks a better combination later — which is exactly the gap the optimiser's
    local-search feedback closes.
    """
    return greedy_insert(scenario, by_weight_desc, now=now, fixed=fixed)
