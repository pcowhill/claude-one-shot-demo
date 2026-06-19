# Research Brief — Event-Driven Resource Allocation

A concise, cited write-up of the problem domain, why event-driven optimisation matters for an
organisation like Roster-5, the approach taken in this project, and how it would extend to
production scale. **Verified public facts are kept separate from reasonable inference**, and only
public/synthetic information is used — no proprietary or classified specifics.

---

## 1. Summary

Roster-5 Software's flagship is an event-driven scheduling/resource-management algorithm called
**"Iterated Distributed Feedback."** The underlying technical problem — deciding, in real time,
which limited assets service which prioritised, deadline-bound demands — is a classic of
combinatorial optimisation and is **NP-hard**. This project builds a faithful, unclassified
demonstration of that capability: an event-driven allocation engine with automated,
human-in-the-loop, and hybrid control, on synthetic data.

## 2. Roster-5 Software, Inc. — context

### Verified public facts (with sources)

- Veteran- and employee-owned small business, **founded 2012**, **Louisville, Colorado**;
  market focus is the **U.S. Intelligence Community** [1][2].
- Stated mission: "solve the Nation's most pressing challenges in **resource optimization,
  information sharing, and big-data analytics**" [1].
- Flagship algorithm **"Iterated Distributed Feedback"**, described (verbatim) as: *"has been
  proven in commercial applications … builds 'best-known' quality solutions in a handful of
  seconds, and can operate as part of a fully-automated closed-loop scheduling system, with human
  inputs, or as part of a hybrid system with both manual inputs and automated resource management
  taking place simultaneously"* [1].
- Public technology stack: **Java, C/C++, Python, Angular/JavaScript, AWS, Oracle, SQL/PL-SQL**;
  DevOps with Kubernetes/Docker; relational + big-data technologies; AWS and Oracle Cloud
  including **GovCloud** [1].

### Reasonable inference (explicitly *not* verified fact)

- "Iterated Distributed Feedback" *resonates with* the published distributed/iterative
  task-allocation literature (auction/market-based allocation, consensus-based methods, iterated
  local search) — see §7. Roster-5's actual internals are proprietary and undisclosed; this
  project only claims to be built **in the spirit of** these public ideas.
- The IC mission language and "GovCloud / secure cloud" references are consistent with ISR
  collection-management / sensor-and-asset tasking (the canonical government instance of online
  scheduling optimisation), though the site does not name it. Specific contracts/customers were
  **not** publicly verifiable.

## 3. Why event-driven optimisation matters

Event-driven architecture (EDA) is "a software design model built around the publication,
capture, processing and storage of events," letting systems "respond and react to them in real
time" [4]. Producers emit events to loosely-coupled consumers that "operate independently while
reacting to events in real time" [5]. For operations specifically, EDA lets organisations "detect
emerging situations, act in real time, automate decision-making," shifting "from reactive to
proactive" [6]. A resource-allocation engine is a perfect fit: taskings arrive, assets fail,
priorities change — and the plan must continuously catch up.

## 4. The optimisation problem

The core is **weighted, prize-collecting job/interval scheduling on parallel/unrelated machines
with release times, deadlines, weights, and machine-eligibility** — choose a subset of jobs and
schedule each, non-preemptively, on an eligible machine inside its window, to maximise the weight
of jobs completed on time [7][10].

- **Hardness.** Single-machine scheduling with rejection is "NP-hard in the strong sense, and
  there is no polynomial-time algorithm that can guarantee a constant-factor approximation (unless
  P=NP)" [8]; even simple special cases reduce to bin packing. So **fast heuristic / local-search
  engines, not exact solvers, win in real time** — which matches Roster-5's "best-known solutions
  in a handful of seconds" framing.
- **Online with commitment.** Because information arrives over time and decisions are
  (eventually) irrevocable, this is also an online scheduling problem studied under competitive
  analysis [9].
- **Production-realistic pattern: rolling-horizon re-optimisation.** The standard dynamic
  approach updates the schedule on each significant event (or when reality drifts past a
  tolerance), combining MILP/CP submodels and metaheuristics [11]. This project re-optimises the
  remaining horizon on every event, holding committed work and human decisions fixed.

## 5. Government instance and civilian analogues

- **Government instance — ISR collection management / satellite & sensor tasking.** Assigning
  observations to spacecraft "is equivalent to the precedence-constrained scheduling problem,
  which has been shown to be NP-complete," and limited ground-station capacity makes it an
  "oversubscribed problem" [12]. Satellite **range / ground-station contact scheduling** is its
  own recognised optimisation area [13], as is earth-observation tasking as combinatorial
  optimisation [14a].
- **Tasteful civilian analogues** (what this demo uses): **wildfire/disaster monitoring** with
  drones and sensors — "real-time reconnaissance, thermal hotspot detection, fire-perimeter
  mapping," needing multi-UAV cooperation and therefore task allocation/scheduling [14][15] — plus
  ground-station contact scheduling and emergency (computer-aided) dispatch. All are
  "which asset does which task, when, under hard constraints" problems.

## 6. Human-in-the-loop, human-on-the-loop, hybrid

Roster-5's "fully-automated / with human inputs / hybrid" modes map directly onto the standard
autonomy spectrum:

- **Human-in-the-loop (HITL):** the system "requires human approval prior to action" [16-style];
  a person reviews/approves before anything proceeds.
- **Human-on-the-loop (HOTL):** "the human receiving updates … and in turn providing guidance" —
  a supervisor who "steps in only for exceptions or critical adjustments" [16].
- **Full autonomy:** closed-loop, no human in the decision path.

This project implements all three (Automated, Human-in-the-loop/MANUAL, Hybrid). Hybrid is HOTL:
auto-apply, but surface exceptions (a dropped high-priority tasking) for human attention.

## 7. "Iterated Distributed Feedback" — the public lineage it resonates with

*(Used only to justify "in the spirit of"; not a claim about Roster-5's internals.)* The name
maps cleanly onto a real research lineage of **iterative, distributed, feedback-driven task
allocation**:

- **Consensus-Based Bundle Algorithm (CBBA):** "a decentralized, auction-based algorithm … for
  dynamic multi-agent task allocation," alternating bundle-construction and consensus-based
  conflict-resolution phases with "guaranteed convergence to conflict-free allocations" [17];
  extended with **local replanning** for dynamic, time-sensitive environments [18].
- **Market/auction-based allocation and the Contract-Net Protocol** are the broader lineage [17].
- **Iterated local search** is the metaheuristic family our optimiser belongs to.

## 8. The approach taken here

- **Model:** weighted prize-collecting interval scheduling on unrelated parallel machines with
  release times, deadlines, and capability/region eligibility; unit-capacity assets; integer-minute
  horizon. (`app/core/models.py`.)
- **Optimiser (`app/core/optimizer.py`) — an homage to Iterated Distributed Feedback:** a greedy
  seed (value-density / weight / FIFO), refined by local search with **ejection chains**
  (`EVICT-AND-INSERT`: evict the cheapest colliding set, insert the valuable tasking, re-home the
  evicted work — the "distributed feedback" coupling between assets), diversified by **iterated**
  seeded restarts. Each accepted move strictly improves the objective (the "feedback signal"), and
  seeding from FIFO guarantees the result never falls below the baseline. Deterministic per seed.
- **Engine (`app/core/engine.py`):** event-driven, rolling-horizon re-optimisation with the three
  control modes and human-in-the-loop locks/overrides; in-progress work is immutable.
- **Proof of value:** vs. FIFO and priority-greedy baselines, offline (full information) and online
  (live). Across six synthetic scenarios the optimiser delivered **~+31% more mission value on
  average (up to +56%)** offline, and **~+11% (up to +18%)** in the fully-online setting.

## 9. Extending to production scale

- **Throughput & latency:** move the hot optimisation core to **Java/C++** (no GC pauses on the
  critical path) [22]; keep the anytime metaheuristic but add a **CP-SAT/MILP** lane for offline
  re-baselining and bound estimation [11].
- **Event backbone:** ingest taskings/outages/re-taskings over **Apache Kafka** (≈2 ms latencies,
  durable, replayable) [19]; the engine becomes a stream processor that re-optimises per event.
- **Persistence:** plans, audit trail, and human decisions in **Oracle** (ACID durability) [20].
- **Deployment:** **AWS GovCloud** for FedRAMP-High/secure workloads [21].
- **Scale of the algorithm:** partition by region/asset-class for distributed local search
  (genuinely "distributed feedback"), with a coordinator reconciling cross-partition ejection
  chains — directly in the CBBA-with-replanning spirit [18].
- **Multi-capacity assets, setup/transition times, uncertainty:** natural model extensions noted in
  `ASSUMPTIONS.md`.

See `docs/PRODUCTION_MAPPING.md` for the component-by-component mapping.

---

## References

1. Roster-5 Software — official site (rendered). https://roster5.com/
2. Roster-5 Software — LinkedIn. https://www.linkedin.com/company/roster5
3. Roster-5 Software — Dun & Bradstreet profile.
   https://www.dnb.com/business-directory/company-profiles.roster-5_software_inc.095bc3d619ebb87721434a36294ae4a5.html
4. IBM — "What Is Event-Driven Architecture?" https://www.ibm.com/think/topics/event-driven-architecture
5. Confluent — "Event-Driven Architecture: A Complete Introduction." https://www.confluent.io/learn/event-driven-architecture/
6. MIT Technology Review — "Enabling real-time responsiveness with event-driven architecture."
   https://www.technologyreview.com/2025/10/06/1124323/enabling-real-time-responsiveness-with-event-driven-architecture/
7. Springer — "Scheduling With Release Times and Deadlines on a Minimum Number of Machines."
   https://link.springer.com/chapter/10.1007/1-4020-8141-3_18
8. Elsevier EJOR — "Single-machine scheduling with release times, deadlines, setup times, and rejection."
   https://ideas.repec.org/a/eee/ejores/v291y2021i2p629-639.html
9. arXiv — "Maximizing Online Utilization with Commitment." https://arxiv.org/pdf/1904.06150
10. ScienceDirect — "Interval scheduling on related machines."
    https://www.sciencedirect.com/science/article/abs/pii/S0305054811000670
11. arXiv — "Learning-Guided Rolling Horizon Optimization for Flexible Job-Shop Scheduling."
    https://arxiv.org/pdf/2502.15791
12. HEC Montréal — "Satellite Scheduling Problems: a Survey."
    https://chairelogistique.hec.ca/wp-content/uploads/2024/02/Survey_on_observation_scheduling.pdf
13. Springer — "Reviews and prospects in satellite range scheduling problem."
    https://link.springer.com/article/10.1007/s43684-023-00054-6
14a. arXiv — "A Maximum Independent Set Method for Scheduling Earth-Observing Satellite Constellations."
    https://arxiv.org/pdf/2008.08446
14. Wikipedia — "Drones in wildfire management." https://en.wikipedia.org/wiki/Drones_in_wildfire_management
15. arXiv — "Scheduling of Emergency Tasks for Multiservice UAVs in Post-Disaster Scenarios."
    https://arxiv.org/pdf/2010.10939
16. Joint Air Power Competence Centre — "Human-On-the-Loop." https://www.japcc.org/essays/human-on-the-loop/
17. EmergentMind — "Consensus-Based Bundle Algorithm (CBBA)."
    https://www.emergentmind.com/topics/consensus-based-bundle-algorithm-cbba
18. Springer (J. Supercomputing) — "CBBA with local replanning for heterogeneous multi-UAV system."
    https://link.springer.com/article/10.1007/s11227-021-03940-z
19. Apache Kafka — official introduction. https://kafka.apache.org/intro/
20. Oracle — Database Transactions (ACID) documentation.
    https://docs.oracle.com/cd/E11882_01/server.112/e40540/transact.htm
21. AWS — GovCloud (US) compliance documentation.
    https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-compliance.html
22. Stack Overflow Blog — "Choosing Java instead of C++ for low-latency systems."
    https://stackoverflow.blog/2021/02/22/choosing-java-instead-of-c-for-low-latency-systems/

*Public sources, accessed June 2026. Company facts are from Roster-5's public website and
corroborating public listings; unverifiable specifics are labelled as such.*
