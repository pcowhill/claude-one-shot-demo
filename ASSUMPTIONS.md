# Assumptions

Every assumption made while building this, recorded explicitly. The brief authorised reasonable
assumptions provided they were written down.

## Scope & framing

- **Synthetic, unclassified only.** No proprietary or classified data, and nothing about
  Roster-5's real systems, is used or implied. The flagship algorithm "Iterated Distributed
  Feedback" is reproduced *in spirit* from Roster-5's **public** website description and the open
  task-allocation literature (Contract-Net, CBBA, iterated local search) — not from any internal
  knowledge. Roster-5's actual internals are proprietary and undisclosed; this is an independent
  homage, not a reconstruction.
- **Civilian analogue chosen for the scenario.** A heterogeneous fleet of observation assets
  (satellites, UAVs, aircraft, a ground station) servicing prioritised, deadline-bound observation
  requests. This is a tasteful, unclassified stand-in for the ISR collection-management / sensor-
  tasking problems the domain is known for; it is structurally identical (and the engine transfers
  directly to emergency dispatch, ground-station contact scheduling, field-crew assignment, etc.).
- **The company facts** used in the docs (Louisville CO, founded 2012, VOSB, IC focus, stack,
  the algorithm description) are taken from Roster-5's public website and corroborating public
  listings. Specific contracts/customers were **not** publicly verifiable and are not claimed.
  See `docs/RESEARCH_BRIEF.md` for sourcing and the fact-vs-inference split.

## Problem model

- **Time is integer minutes** over a finite horizon (default an 8-hour, 480-minute shift). This
  keeps the geometry exact and the property tests crisp.
- **Unit capacity per asset** — an asset services one tasking at a time. This makes the
  "no double-booking" invariant clean to state and verify. Multi-capacity assets (a satellite
  with several apertures, a crew of N) are noted as a production extension, not implemented.
- **Eligibility has two dimensions** — sensing *capability* (EO/IR/SAR/RF/FMV) and *region* — so
  the constraint structure is non-trivial without being baroque.
- **Objective: maximise total weighted on-time value** (prize-collecting). Because feasibility
  forbids late completion, a tasking is simply *served* or *missed* — no partial credit, no
  lateness penalty. Mission value (`weight`) is seeded from the priority tier plus noise.
- **Each generated tasking is feasible for at least one asset** (it is derived from a randomly
  chosen capable asset). So misses are attributable to genuine time/contention pressure, not to
  impossible asks — which keeps the value-capture metric meaningful. Real systems would also see
  un-serviceable requests; that is a straightforward generator tweak.
- **Scenarios are deliberately oversubscribed** (demand exceeds capacity). That is the whole
  point — *which* taskings you drop is where an optimiser earns its keep.

## Algorithm

- **Heuristic, not exact.** The problem is NP-hard, so we use a fast anytime metaheuristic
  (iterated local search with ejection chains) rather than a MILP/CP solver. This matches
  Roster-5's public "best-known quality solutions in a handful of seconds" description and keeps
  the project dependency-free (no solver to install). An exact CP-SAT formulation is described as
  a production option in the research brief but intentionally not added as a dependency.
- **The ≥-baseline guarantee** holds for the *offline* objective (FIFO is one of the optimiser's
  seeds and moves are non-worsening). **Online** (live, commit-as-you-go) it is empirically but
  not provably ahead — an honest property of online optimisation, and reported as such.
- **Determinism** is achieved with a single seeded RNG (only the iterated "kick" is random). The
  default tunables are sized for the demo's instance sizes, not theoretically optimal.

## Engine & modes

- **Rolling-horizon re-optimisation:** the whole future is re-solved on each event; committed
  (in-progress) work and human locks are held fixed. This is simpler and, for these sizes, as
  good as incremental repair.
- **Unplanned outages never disturb in-progress work** (the outage is clipped past committed
  tasks); a human lock invalidated by an outage is released for re-decision.
- **MANUAL mode requires operator approval** to apply re-optimised plans (after an initial
  bootstrap plan), so left unattended it intentionally does little — that is the human-in-the-loop
  gate, exercised by the screenshot/demo flows.

## Platform & runtime

- **Python 3.11+ is the only requirement to run.** The dashboard uses **FastAPI + uvicorn**
  (the two runtime deps). The UI is vanilla HTML/CSS/JS with **no build step and no CDN**, so no
  Node and no internet are needed.
- **No database.** State is in-memory (single-operator demo console). A persistence layer maps to
  Oracle in production (notes only). No Postgres/Redis is used, so there is nothing heavier to gate
  behind a flag.
- **Single shared live session.** The server hosts one engine + one shadow-FIFO baseline engine,
  broadcast to all connected browsers. This is a demonstration console, not a multi-tenant service.
- **Default port 8000** (override with `PORT`). Binds to `127.0.0.1` (localhost only).

## Artifacts & verification

- **Screenshots are real**, captured from the running app with headless Chromium via Playwright.
  Playwright is a *dev* dependency used only to regenerate artifacts; it is not needed to run or
  view the project. (In this build environment the pre-fetched Chromium build differs from the
  pip-pinned one, so the capture script accepts a `CHROME_PATH` override; on a normal machine
  `playwright install chromium` suffices.)
- **Diagrams are hand-generated SVG** (pure Python) rather than Graphviz, so regenerating them
  needs no system binaries and the output is fully deterministic and style-matched to the app.
- **`report.html` inlines everything** (CSS, charts as base64, SVG diagrams) so it opens over
  `file://` with no server and no network.
- **Benchmarks, charts, and screenshots are committed** so the project is fully judgeable without
  running anything.
