"""Pydantic request/response models for the control API.

These give the HITL endpoints validated, self-documenting payloads (and show up in the
auto-generated OpenAPI docs at ``/docs``). Snapshots themselves are returned as plain JSON
dictionaries assembled by the engine, so the wire format has a single source of truth.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModeBody(BaseModel):
    """Switch the engine's operating mode."""

    mode: str = Field(description="One of AUTOMATED, MANUAL, HYBRID")


class SpeedBody(BaseModel):
    """Adjust playback speed of the simulated clock."""

    minutes_per_tick: int = Field(ge=1, le=20, description="Simulated minutes advanced per frame")
    interval_ms: int = Field(ge=30, le=3000, description="Wall-clock milliseconds between frames")


class OverrideBody(BaseModel):
    """Operator override: force a tasking onto a specific asset at a specific start minute."""

    request_id: str
    resource_id: str
    start: int = Field(ge=0, description="Start minute for the forced placement")


class CommandResult(BaseModel):
    """Generic result for a control/HITL command."""

    ok: bool
    message: str = ""
