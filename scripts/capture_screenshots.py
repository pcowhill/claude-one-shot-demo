"""Capture real headless-Chromium screenshots of the running dashboard.

Self-contained: it starts the FastAPI app in a background thread, drives it through each of the
three operating modes via the real REST API, screenshots the rendered page with headless
Chromium (Playwright), then shuts the server down. The PNGs are committed so a non-engineer can
see the system working without running anything.

Usage (browsers are pre-fetched in this environment under /opt/pw-browsers):
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python scripts/capture_screenshots.py

Set ``CAPTURE_BASE_URL`` to point at an already-running server instead of self-hosting.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
from playwright.sync_api import Page, sync_playwright

HOST = "127.0.0.1"
PORT = int(os.environ.get("CAPTURE_PORT", "8023"))
BASE_URL = os.environ.get("CAPTURE_BASE_URL", f"http://{HOST}:{PORT}")
OUT_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "screenshots"
VIEWPORT = {"width": 1600, "height": 950}


# ----------------------------------------------------------------------------------------
# In-process server (a daemon thread, so there is no lingering background process)
# ----------------------------------------------------------------------------------------


def serve_in_thread() -> tuple[uvicorn.Server, threading.Thread]:
    """Start uvicorn in a daemon thread and wait until it is accepting connections."""
    config = uvicorn.Config("app.api.server:app", host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(150):
        if server.started:
            break
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("server did not start in time")
    return server, thread


# ----------------------------------------------------------------------------------------
# Server control helpers (REST)
# ----------------------------------------------------------------------------------------


def _post(path: str, body: dict[str, object] | None = None) -> None:
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(
        f"{BASE_URL}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def _state() -> dict[str, object]:
    with urllib.request.urlopen(f"{BASE_URL}/api/state", timeout=10) as resp:
        return json.loads(resp.read())


def _reset_paused() -> None:
    _post("/api/control/reset")
    _post("/api/control/pause")
    _post("/api/control/speed", {"minutes_per_tick": 6, "interval_ms": 120})


def _step_to(target: int) -> None:
    guard = 0
    while int(_state()["clock"]) < target and guard < 400:
        _post("/api/control/step")
        guard += 1


def _settle(page: Page, ms: int = 700) -> None:
    page.wait_for_timeout(ms)


def _shot(page: Page, name: str) -> None:
    path = OUT_DIR / name
    page.screenshot(path=str(path))
    print(f"  captured {path.relative_to(OUT_DIR.parent.parent)}")


# ----------------------------------------------------------------------------------------
# Capture sequence
# ----------------------------------------------------------------------------------------


def capture(page: Page) -> None:
    """Produce the committed screenshot set across all three modes."""
    page.goto(BASE_URL, wait_until="networkidle")

    # 1) AUTOMATED — mid-shift, the headline live view.
    _reset_paused()
    _post("/api/control/mode", {"mode": "AUTOMATED"})
    _step_to(246)
    _settle(page)
    _shot(page, "01_automated_midshift.png")

    # 2) HYBRID — automated with surfaced exceptions after outages/load.
    _reset_paused()
    _post("/api/control/mode", {"mode": "HYBRID"})
    _step_to(360)
    _settle(page)
    _shot(page, "03_hybrid_exceptions.png")

    # 3) HUMAN-IN-THE-LOOP (MANUAL) — a proposed plan awaiting approval, with a tasking selected
    #    so the lock / override / hold controls are visible.
    _reset_paused()
    _step_to(210)
    _post("/api/control/mode", {"mode": "MANUAL"})
    _step_to(252)
    _settle(page)
    blocks = page.query_selector_all(".alloc.alloc-planned") or page.query_selector_all(".alloc")
    if blocks:
        try:
            blocks[len(blocks) // 2].click()
        except Exception as exc:
            print(f"  (selection click skipped: {exc})")
    _settle(page, 500)
    _shot(page, "02_hitl_manual.png")

    # 4) Completed shift — the full timeline, AUTOMATED.
    _reset_paused()
    _post("/api/control/mode", {"mode": "AUTOMATED"})
    _step_to(480)
    _settle(page)
    _shot(page, "04_completed_shift.png")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    server: uvicorn.Server | None = None
    if "CAPTURE_BASE_URL" not in os.environ:
        print(f"Starting in-process server on {BASE_URL} ...")
        server, _ = serve_in_thread()
    # Allow pointing at a pre-fetched Chromium (handy when the pip-pinned build differs from the
    # one available on disk). Falls back to Playwright's bundled browser.
    chrome_path = os.environ.get("CHROME_PATH") or None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"], executable_path=chrome_path)
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
            capture(page)
            browser.close()
    finally:
        if server is not None:
            server.should_exit = True
            time.sleep(0.5)
    print("Screenshots complete.")


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"Done in {time.time() - t0:.1f}s")
