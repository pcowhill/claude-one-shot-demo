"""Run the dashboard with ``python -m app``.

Reads ``HOST``/``PORT`` from the environment (defaults: 127.0.0.1:8000) and starts uvicorn. This
is the single command the README points at; nothing else is required to see the live system.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    """Entry point: launch the FastAPI app on localhost."""
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"\n  Roster-5 Allocation Engine — live dashboard at http://{host}:{port}\n")
    uvicorn.run("app.api.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
