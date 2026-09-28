"""FastAPI application: live WebSocket stream, HITL control API, and the static dashboard.

Architecture (kept deliberately small):

* A single module-level :class:`LiveSession` holds the optimiser engine and the shadow FIFO
  baseline. A background asyncio task advances the simulated clock and broadcasts a fresh
  snapshot to every connected WebSocket client.
* The REST endpoints are the human-in-the-loop surface (mode, playback, lock/override/hold,
  approve/reject). Each mutates the session under an ``asyncio.Lock`` — so a control action can
  never interleave with a clock tick — and then broadcasts the updated snapshot immediately, so
  the UI reflects the change without waiting for the next frame.
* The dashboard is served as static files; there is no build step and no CDN.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .schemas import CommandResult, ModeBody, OverrideBody, SpeedBody
from .session import LiveSession

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
BENCHMARK_RESULTS = (
    Path(__file__).resolve().parent.parent.parent
    / "benchmarks"
    / "results"
    / "benchmark_results.json"
)

# Single live session shared by all clients (this is a single-operator demo console).
session = LiveSession()


class ConnectionHub:
    """Tracks connected WebSocket clients and fans out snapshots to them."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, payload: dict[str, object]) -> None:
        message = json.dumps(payload, default=str)
        for client in list(self._clients):
            try:
                await client.send_text(message)
            except Exception:
                self.disconnect(client)


hub = ConnectionHub()


async def _play_loop() -> None:
    """Background task: advance the clock and broadcast while the session is playing."""
    while True:
        await asyncio.sleep(session.interval)
        if not session.playing:
            continue
        async with _lock:
            session.advance(session.minutes_per_tick)
            payload = session.snapshot()
        await hub.broadcast(payload)


_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start the background play loop for the lifetime of the server."""
    task = asyncio.create_task(_play_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Roster-5 Allocation Engine",
    description="Event-driven resource-allocation engine with a live operations dashboard.",
    version="1.0.0",
    lifespan=lifespan,
)


async def _mutate(action: Callable[[], bool]) -> CommandResult:
    """Run a state mutation under the lock, then broadcast the fresh snapshot to all clients."""
    async with _lock:
        ok = action()
        payload = session.snapshot()
    await hub.broadcast(payload)
    return CommandResult(ok=ok)


# ----------------------------------------------------------------------------------------
# State + playback control
# ----------------------------------------------------------------------------------------


@app.get("/api/state")
async def get_state() -> dict[str, object]:
    """Return the current engine snapshot (used for the initial page load)."""
    async with _lock:
        return session.snapshot()


@app.post("/api/control/play")
async def play() -> CommandResult:
    """Resume the simulated clock."""

    def _do() -> bool:
        session.playing = True
        return True

    return await _mutate(_do)


@app.post("/api/control/pause")
async def pause() -> CommandResult:
    """Pause the simulated clock."""

    def _do() -> bool:
        session.playing = False
        return True

    return await _mutate(_do)


@app.post("/api/control/step")
async def step() -> CommandResult:
    """Advance one frame while paused (single-stepping the clock)."""

    def _do() -> bool:
        session.advance(session.minutes_per_tick)
        return True

    return await _mutate(_do)


@app.post("/api/control/reset")
async def reset() -> CommandResult:
    """Restart the scenario from t=0."""

    def _do() -> bool:
        session.reset()
        return True

    return await _mutate(_do)


@app.post("/api/control/mode")
async def set_mode(body: ModeBody) -> CommandResult:
    """Switch operating mode (AUTOMATED / MANUAL / HYBRID)."""
    return await _mutate(lambda: session.set_mode(body.mode))


@app.post("/api/control/speed")
async def set_speed(body: SpeedBody) -> CommandResult:
    """Adjust playback speed."""
    return await _mutate(lambda: session.set_speed(body.minutes_per_tick, body.interval_ms))


# ----------------------------------------------------------------------------------------
# Human-in-the-loop task control
# ----------------------------------------------------------------------------------------


@app.post("/api/task/{request_id}/lock")
async def lock_task(request_id: str) -> CommandResult:
    """Pin a tasking's allocation so re-optimisation cannot move it."""
    return await _mutate(lambda: session.engine.lock(request_id))


@app.post("/api/task/{request_id}/unlock")
async def unlock_task(request_id: str) -> CommandResult:
    """Release a previously locked tasking."""
    return await _mutate(lambda: session.engine.unlock(request_id))


@app.post("/api/task/{request_id}/hold")
async def hold_task(request_id: str) -> CommandResult:
    """Operator decision not to service a tasking (remove it from planning)."""
    return await _mutate(lambda: session.engine.hold(request_id))


@app.post("/api/task/{request_id}/release")
async def release_task(request_id: str) -> CommandResult:
    """Return a held tasking to the planning pool."""
    return await _mutate(lambda: session.engine.release_hold(request_id))


@app.post("/api/task/override")
async def override_task(body: OverrideBody) -> CommandResult:
    """Force a tasking onto a specific asset at a specific start minute (validated)."""
    return await _mutate(
        lambda: session.engine.override(body.request_id, body.resource_id, body.start)
    )


@app.post("/api/proposal/approve")
async def approve_proposal() -> CommandResult:
    """Approve and apply the pending proposed plan (MANUAL mode)."""
    return await _mutate(session.engine.approve)


@app.post("/api/proposal/reject")
async def reject_proposal() -> CommandResult:
    """Discard the pending proposed plan (MANUAL mode)."""
    return await _mutate(session.engine.reject_proposal)


# ----------------------------------------------------------------------------------------
# Benchmark results (proof-of-value), if generated
# ----------------------------------------------------------------------------------------


@app.get("/api/benchmark")
async def get_benchmark() -> JSONResponse:
    """Return committed benchmark results if present, else an empty payload."""
    if BENCHMARK_RESULTS.exists():
        return JSONResponse(json.loads(BENCHMARK_RESULTS.read_text()))
    return JSONResponse({"available": False})


# ----------------------------------------------------------------------------------------
# WebSocket live stream
# ----------------------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Push live snapshots to a client; the channel is server->client only."""
    await hub.connect(websocket)
    try:
        async with _lock:
            initial = session.snapshot()
        await websocket.send_text(json.dumps(initial, default=str))
        while True:
            # We don't expect client messages, but awaiting keeps the socket open and lets us
            # notice disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except Exception:
        hub.disconnect(websocket)


# ----------------------------------------------------------------------------------------
# Static dashboard
# ----------------------------------------------------------------------------------------


@app.get("/")
async def index() -> FileResponse:
    """Serve the single-page operations dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
