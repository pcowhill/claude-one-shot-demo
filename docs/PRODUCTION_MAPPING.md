# Production Mapping

How each part of this demo maps onto the production stack Roster-5 publicly describes
(**Java/C++ · Apache Kafka · Oracle · AWS**). These are **design notes only** — none of these
systems are runtime dependencies of the demo, which runs on Python alone. The point is to show the
demo is architected the way a production system would be, with clean seams at each boundary.

```
                          ┌─────────────────────────── DEMO (this repo) ──────────────────────────┐
   field events  ─────▶   │  Simulator → Engine (core) → FastAPI (WS/REST) → vanilla-JS dashboard  │
                          └───────────────┬───────────────┬───────────────┬───────────────────────┘
                                          │ maps to        │ maps to        │ maps to
                          ┌───────────────▼───────────────▼───────────────▼───────────────────────┐
   field events  ─────▶   │  Apache Kafka  →  Java/C++ optimisation core  →  Oracle  →  secure web  │  on AWS GovCloud
                          └───────────────────────────────────────────────────────────────────────┘
```

See `diagrams/architecture.svg` for the rendered version.

## Component-by-component

### 1. Event stream — `app/core/scenario.py` (simulator) → **Apache Kafka**

- **Demo:** a seeded simulator emits an ordered event stream (tasking arrivals, unplanned
  outages, re-taskings); the engine consumes it as the clock advances.
- **Production:** taskings, asset-status changes, and re-prioritisations arrive as **Kafka**
  topics. Kafka is "an open-source distributed event streaming platform … for high-performance
  data pipelines, streaming analytics … and mission-critical applications" with "latencies as low
  as 2 ms," and durable, replayable logs [Kafka]. The engine becomes a stream processor:
  re-optimise per event, publish the new plan to an `allocations` topic.
- **Seam in the code:** `Event` / `EventType` and `AllocationEngine.tick()` already model
  discrete event consumption; swapping the simulator for a Kafka consumer touches only the engine's
  event source, not the optimiser.

### 2. Optimisation core — `app/core/optimizer.py` + `engine.py` → **Java / C++**

- **Demo:** pure-Python core, deliberately importing no web framework, so it is deterministic and
  testable in isolation.
- **Production:** port the hot path to **Java/C++** for low-latency, GC-pause-free execution on the
  critical path [Java-vs-C++]. The anytime metaheuristic stays; a CP-SAT/MILP lane can be added for
  periodic offline re-baselining and optimality-gap estimation.
- **Seam in the code:** the `app/core` boundary is exactly this seam. `optimize()` is a pure
  function of `(Scenario, OptimizerConfig, now, fixed)`; it has no I/O and could be reimplemented
  behind the same contract in another language.
- **Distribution ("distributed feedback"):** partition by region / asset-class so each partition
  runs local search independently, with a coordinator reconciling cross-partition ejection chains —
  the CBBA-with-local-replanning pattern from the research brief.

### 3. Persistence — in-memory session → **Oracle**

- **Demo:** state is in-memory (single-operator console); nothing is persisted.
- **Production:** plans, the full audit trail, and **every human decision** (locks, overrides,
  holds, approvals) are written to **Oracle**, whose transactions "comply with the … ACID
  properties," using rollback/undo and write-ahead logging for atomicity and durability [Oracle].
  This matters here specifically because human-in-the-loop decisions are accountable actions that
  must be durable and auditable.
- **Seam in the code:** `Allocation` already carries `locked` / `status`; the engine's HITL methods
  are the natural transaction boundaries. A zero-dependency SQLite adapter (gated behind a flag)
  would be the local stand-in; the demo simply omits persistence to keep "Python only" true.

### 4. Transport — `app/api/server.py` (FastAPI) → **secure web tier on AWS**

- **Demo:** FastAPI serves a WebSocket live stream + a REST control surface + the static UI.
- **Production:** the same shapes behind an API gateway, with **Angular** operator clients
  (Roster-5's stated web stack), authn/z, and audit. Deploy to **AWS GovCloud (US)** — "isolated
  AWS Regions designed to allow U.S. government agencies … to move sensitive workloads into the
  cloud," meeting FedRAMP High and DoD SRG IL2/4/5, "administered exclusively by U.S. citizens"
  [AWS-GovCloud].
- **Seam in the code:** the transport is already thin and stateless-per-request; the engine is the
  only stateful component, and it is framework-free.

### 5. Dashboard — `app/static/` (vanilla JS) → **Angular operator clients**

- **Demo:** zero-build vanilla HTML/CSS/JS, no CDN, so it runs with Python alone.
- **Production:** a hardened Angular application (per Roster-5's public stack) with role-based
  controls and SSO. The data contract is the same JSON snapshot the demo already broadcasts.

## What stays the same from demo to production

The **domain model, the objective, the constraint rules, the optimiser's move set, and the three
control modes** are the actual product logic and would transfer essentially unchanged — only the
*language*, *transport*, *persistence*, and *deployment* swap underneath them. That is the whole
reason the core is kept pure and the seams kept clean.

---

### Sources

- **Kafka** — https://kafka.apache.org/intro/
- **Oracle** (ACID transactions) — https://docs.oracle.com/cd/E11882_01/server.112/e40540/transact.htm
- **AWS GovCloud (US)** — https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-compliance.html
- **Java vs C++ for low-latency** — https://stackoverflow.blog/2021/02/22/choosing-java-instead-of-c-for-low-latency-systems/
- Roster-5 public stack — https://roster5.com/
