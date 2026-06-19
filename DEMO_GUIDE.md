# Demo Guide — a guided tour

Two short tours: one for **program managers / non-engineers**, one for **engineers**. Pick yours.

---

## 🧭 For program managers / non-engineers (5 minutes, nothing to install)

**1. Open [`report.html`](report.html).** Double-click it. It opens in any browser, fully
offline — no server, no setup. It is the whole story on one page:

- the **headline result** (more mission value delivered than the naive approach),
- **screenshots** of the live system in each mode,
- the **proof-of-value charts**, and
- plain-language explanations of how it works.

**2. Look at the three dashboard screenshots** in [`artifacts/screenshots/`](artifacts/screenshots/):

- `01_automated_midshift.png` — the system running on its own, mid-shift. Top strip: **mission
  value delivered**, and how far ahead of the naive "first-come-first-served" baseline it is.
- `02_hitl_manual.png` — a human in control: a proposed plan **awaiting approval**, and the
  controls to lock / override / hold an individual tasking.
- `03_hybrid_exceptions.png` — automated, but **flagging exceptions** (high-priority work it
  couldn't fit) for a person to review.

**3. Look at the headline chart**, [`benchmarks/results/value_by_scenario.png`](benchmarks/results/value_by_scenario.png).
Taller blue bars = more mission value delivered by the optimiser vs. the grey baseline, with the
percentage improvement labelled on each.

**What to take away:** this is exactly the kind of *event-driven resource optimisation with
automated / human-in-the-loop / hybrid control* that Roster-5 builds — demonstrated end-to-end,
with the value quantified and the system visibly working.

### If you want to run it (optional, ~1 minute)

```bash
./run.sh
```
Then open http://127.0.0.1:8000, and use the **mode switch** (top-right) to flip between
Automated, Human-in-loop, and Hybrid. Click any block on the timeline to act on a tasking.

---

## 🛠️ For engineers (look here first)

**Start with the algorithm:** [`app/core/optimizer.py`](app/core/optimizer.py). It is an
**Iterated Local Search with ejection chains** (an homage to "Iterated Distributed Feedback"):

- a strong greedy **seed** (and a baseline seed that *guarantees ≥ FIFO*),
- a local search with `INSERT` and `EVICT-AND-INSERT` (ejection-chain) moves,
- **iterated** seeded restarts to escape local optima,
- fully **deterministic** under a fixed seed; objective and constraints documented in-module.

**Then the guarantees:** [`tests/test_properties.py`](tests/test_properties.py). Property-based
(Hypothesis) tests assert, on thousands of random instances, that the optimiser is always
feasible (no double-booking, all windows/eligibility respected), never below baseline, and
deterministic. The independent validation oracle is
[`app/core/feasibility.py`](app/core/feasibility.py) (`schedule_violations`).

**Then the event-driven engine:** [`app/core/engine.py`](app/core/engine.py) — rolling-horizon
re-optimisation, the three operating modes, commitment of in-progress work, outage handling, and
the human-in-the-loop operations (lock / override / hold / approve).

**Then the proof of value:** [`benchmarks/run_benchmarks.py`](benchmarks/run_benchmarks.py) runs
two honest comparisons (offline full-information *and* live online) against FIFO and
priority-greedy baselines, and renders the charts.

**Suggested reading order**

| # | File | Why |
|---|---|---|
| 1 | `app/core/models.py` | The domain and the formal problem statement. |
| 2 | `app/core/optimizer.py` | The algorithm — the centrepiece. |
| 3 | `app/core/feasibility.py` | The constraint rules + the test oracle. |
| 4 | `app/core/engine.py` | Event-driven orchestration + modes + HITL. |
| 5 | `tests/test_properties.py` | The invariants, tested adversarially. |
| 6 | `app/api/server.py` | The thin transport (WebSocket + REST). |
| 7 | `diagrams/*.svg` | The whole system and algorithm at a glance. |

**Run the quality gates:**

```bash
pip install -r requirements-dev.txt
pytest -q          # 55 tests
ruff check .       # clean
mypy               # strict, clean
```

### Things worth noticing

- **The core imports no web framework.** `app/core` is pure Python — that boundary is what keeps
  the optimiser deterministic and trivially testable, and mirrors how a production system keeps a
  Java/C++ optimisation core independent of its Kafka transport and Oracle persistence.
- **The ≥-baseline guarantee is by construction**, not by luck: FIFO is one of the optimiser's
  seeds and every move is non-worsening, so the property test can assert it unconditionally.
- **Offline vs. online uplift are both reported.** Offline (full information) is the controlled
  measure of solution quality (~+31% mean); online (live, rolling-horizon, commit-as-you-go) is
  the realised advantage (~+11% mean). Showing both is the honest story.
- **Determinism is real:** a fixed seed yields a byte-identical schedule, which is what makes the
  benchmarks and screenshots reproducible.
