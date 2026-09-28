"""FastAPI layer: streams the engine over WebSockets and serves the dashboard.

This package is intentionally thin. All scheduling logic lives in :mod:`app.core`; here we only
wire it to HTTP/WebSocket transport and to the static UI. The framework-free orchestration (the
live session that advances the clock and keeps a shadow FIFO baseline) lives in
:mod:`app.api.session`, so the transport in :mod:`app.api.server` stays small and testable.
"""
