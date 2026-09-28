"""Live session orchestration — framework-free.

A :class:`LiveSession` owns two engines running on the *same* scenario and event stream:

* ``engine``  — the optimiser, steerable by the human (this is what the operator sees and acts on);
* ``shadow``  — a plain-FIFO engine, never touched by human commands, that answers the honest
  counterfactual question "what would we have delivered with a naive dispatcher instead?".

Stepping them in lockstep gives a fair, fully-online value-delivered comparison for the
dashboard. The class also caches the *offline* (full-information) optimiser result once per
scenario so the UI can show the theoretical headroom alongside the realised online uplift.

No FastAPI here on purpose: the session is plain Python and is exercised directly by the API
integration tests without any HTTP machinery.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.engine import AllocationEngine, Mode, Policy
from app.core.models import Scenario
from app.core.optimizer import OptimizationResult, optimize
from app.core.scenario import default_live_scenario

ScenarioFactory = Callable[[], Scenario]


class LiveSession:
    """Holds the live optimiser engine, the shadow baseline engine, and playback state."""

    def __init__(
        self,
        scenario_factory: ScenarioFactory = default_live_scenario,
        *,
        minutes_per_tick: int = 3,
        interval_ms: int = 180,
    ) -> None:
        self._factory = scenario_factory
        self.minutes_per_tick = minutes_per_tick
        self.interval_ms = interval_ms
        self.playing = True
        # Populated by reset() below; declared here for the type checker.
        self.engine: AllocationEngine
        self.shadow: AllocationEngine
        self._offline: OptimizationResult
        self.reset()

    @property
    def interval(self) -> float:
        """Playback frame interval in seconds."""
        return self.interval_ms / 1000.0

    def reset(self) -> None:
        """Rebuild both engines from a fresh scenario and restart playback."""
        scenario = self._factory()
        self.engine = AllocationEngine(scenario, mode=Mode.AUTOMATED, policy=Policy.OPTIMIZE)
        self.shadow = AllocationEngine(scenario, mode=Mode.AUTOMATED, policy=Policy.FIFO)
        self._offline = optimize(scenario)  # cached full-information headroom
        self.playing = True

    # -- clock ---------------------------------------------------------------------------

    def advance(self, minutes: int) -> None:
        """Advance both engines by up to ``minutes`` simulated minutes, in lockstep."""
        for _ in range(minutes):
            if self.engine.is_finished:
                break
            self.engine.tick()
            self.shadow.tick()
        if self.engine.is_finished:
            self.playing = False

    # -- control -------------------------------------------------------------------------

    def set_mode(self, mode_name: str) -> bool:
        """Switch the optimiser engine's mode (the shadow stays automated FIFO)."""
        try:
            mode = Mode(mode_name.upper())
        except ValueError:
            return False
        self.engine.set_mode(mode)
        return True

    def set_speed(self, minutes_per_tick: int, interval_ms: int) -> bool:
        """Update playback speed."""
        self.minutes_per_tick = max(1, min(20, minutes_per_tick))
        self.interval_ms = max(30, min(3000, interval_ms))
        return True

    # -- snapshot ------------------------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """Engine snapshot augmented with the live baseline and playback state."""
        snap = self.engine.to_snapshot()

        baseline_delivered = self.shadow.delivered_value()
        optimizer_delivered = self.engine.delivered_value()
        snap["baseline_online"] = {
            "delivered": round(baseline_delivered, 1),
            "served": len(self.shadow.schedule.scheduled_request_ids()),
        }
        snap["headline"] = {
            "optimizer_delivered": round(optimizer_delivered, 1),
            "baseline_delivered": round(baseline_delivered, 1),
            "online_uplift_pct": (
                round(100.0 * (optimizer_delivered - baseline_delivered) / baseline_delivered, 1)
                if baseline_delivered > 0
                else 0.0
            ),
            "offline_uplift_pct": round(self._offline.uplift_vs_baseline_pct, 1),
        }
        snap["playing"] = self.playing
        snap["speed"] = {
            "minutes_per_tick": self.minutes_per_tick,
            "interval_ms": self.interval_ms,
        }
        return snap
