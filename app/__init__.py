"""Event-driven resource-allocation engine — flagship demo package.

The package is organised in layers so the interesting part (the optimiser) stays pure,
deterministic and independently testable:

* ``app.core``   — pure-Python domain model, feasibility rules, objective/KPIs, the
  baseline schedulers, the optimising scheduler, the synthetic scenario generator, and the
  event-driven engine that ties them together. No web framework is imported here.
* ``app.api``    — a thin FastAPI layer that streams the engine over WebSockets, exposes a
  REST surface for the human-in-the-loop controls, and serves the static dashboard.
* ``app.static`` — the vanilla-JS operations dashboard (no build step, no CDN).

See ``docs/RESEARCH_BRIEF.md`` for the problem framing and ``docs/PRODUCTION_MAPPING.md``
for how each layer maps onto Roster-5's production stack (Java / Kafka / Oracle / AWS).
"""

__version__ = "1.0.0"
