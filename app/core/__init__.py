"""Pure-Python core: domain model, feasibility, objective, schedulers, engine.

Nothing in this sub-package imports FastAPI, uvicorn, matplotlib or any I/O framework. That
boundary is deliberate: it keeps the optimiser deterministic and unit/property testable, and
it mirrors how the production system would keep the optimisation core (Java) independent of
its transport (Kafka) and persistence (Oracle).
"""
