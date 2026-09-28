"""Generate the system diagrams as committed SVG — pure Python, no graphviz/binary needed.

We emit SVG by hand so the diagrams are fully portable (no system Graphviz), deterministic, and
styled to match the dashboard and benchmark charts so the whole project reads as one designed
system. Two diagrams are produced:

* ``architecture.svg``   — how data flows through the layers (simulator -> engine -> API ->
  dashboard) and how each layer maps onto Roster-5's production stack.
* ``algorithm_flow.svg`` — the optimiser pipeline (seed -> local search -> iterated restarts)
  with the ejection-chain "feedback" loop that names the approach.

Run with: ``python diagrams/generate_diagrams.py``.
"""

from __future__ import annotations

import html
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent

# Shared palette (matches app.css and the benchmark charts).
BG = "#0e1420"
PANEL = "#16202f"
PANEL_2 = "#1b2740"
BORDER = "#33425a"
ACCENT = "#4cc9f0"
ACCENT_2 = "#5b8def"
GOOD = "#46d39a"
TEXT = "#e7eef8"
MUTED = "#93a6c4"
FLASH = "#ff5d6c"
IMMEDIATE = "#ffb454"

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


# ----------------------------------------------------------------------------------------
# Tiny SVG builder
# ----------------------------------------------------------------------------------------


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def rounded_box(
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    fill: str = PANEL,
    stroke: str = BORDER,
    width: float = 1.5,
    rx: int = 10,
    dashed: bool = False,
) -> str:
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{width}"{dash}/>'
    )


def text(
    x: int,
    y: int,
    s: str,
    *,
    size: int = 13,
    color: str = TEXT,
    weight: str = "normal",
    anchor: str = "start",
    mono: bool = False,
) -> str:
    family = "ui-monospace, SFMono-Regular, Menlo, monospace" if mono else FONT
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{_esc(s)}</text>'
    )


def lines(x: int, y: int, items: list[str], *, size: int = 12, color: str = MUTED, dy: int = 18) -> str:
    return "".join(text(x, y + i * dy, item, size=size, color=color) for i, item in enumerate(items))


def arrow(x1: int, y1: int, x2: int, y2: int, *, color: str = ACCENT, label: str | None = None, dashed: bool = False) -> str:
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    line = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2" marker-end="url(#arrow)"{dash}/>'
    lbl = ""
    if label:
        mx, my = (x1 + x2) // 2, (y1 + y2) // 2 - 6
        lbl = text(mx, my, label, size=11, color=MUTED, anchor="middle")
    return line + lbl


def _svg_open(w: int, h: int, title: str, subtitle: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{ACCENT}"/></marker>'
        f'<marker id="arrowmute" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{MUTED}"/></marker></defs>'
        f'<rect width="{w}" height="{h}" fill="{BG}"/>'
        + text(28, 40, title, size=22, color=TEXT, weight="bold")
        + text(28, 62, subtitle, size=13, color=MUTED)
    )


# ----------------------------------------------------------------------------------------
# Diagram 1: system architecture / data flow
# ----------------------------------------------------------------------------------------


def architecture() -> str:
    w, h = 1200, 720
    parts = [_svg_open(w, h, "System Architecture & Data Flow", "Event-driven resource-allocation engine · pure-Python core, FastAPI transport, zero-build dashboard")]

    # Stage columns.
    sim = (40, 150, 220, 200)
    eng = (300, 110, 360, 330)
    api = (700, 150, 210, 200)
    dash = (950, 140, 215, 230)

    # Simulator
    x, y, bw, bh = sim
    parts.append(rounded_box(x, y, bw, bh, stroke=ACCENT_2))
    parts.append(text(x + 16, y + 30, "Event Simulator", size=15, color=TEXT, weight="bold"))
    parts.append(text(x + 16, y + 50, "synthetic, seeded stream", size=11, color=MUTED))
    parts.append(lines(x + 16, y + 82, ["• tasking arrivals", "• unplanned outages", "• re-taskings (priority", "  bumps)", "deterministic per seed"], size=12))

    # Engine (centerpiece)
    x, y, bw, bh = eng
    parts.append(rounded_box(x, y, bw, bh, fill="#101a2b", stroke=ACCENT, width=2))
    parts.append(text(x + 18, y + 28, "Allocation Engine", size=16, color=TEXT, weight="bold"))
    parts.append(text(x + 18, y + 46, "pure-Python core · framework-free · deterministic", size=11, color=MUTED))
    sub = [
        ("Scenario · Feasibility", "eligibility, windows, no double-booking", BORDER),
        ("Objective · KPIs", "Σ weighted on-time value, utilisation", BORDER),
        ("Baselines: FIFO · priority-greedy", "the bar to beat", BORDER),
        ("Optimizer — Iterated Distributed Feedback", "greedy seed → local search → iterated restarts", ACCENT),
        ("Engine: rolling-horizon re-optimisation", "3 modes · human-in-the-loop locks/overrides", ACCENT_2),
    ]
    sy = y + 64
    for title_s, desc, col in sub:
        parts.append(rounded_box(x + 16, sy, bw - 32, 44, fill=PANEL, stroke=col, rx=8))
        parts.append(text(x + 28, sy + 19, title_s, size=12.5, color=TEXT, weight="bold"))
        parts.append(text(x + 28, sy + 35, desc, size=10.5, color=MUTED))
        sy += 52

    # API
    x, y, bw, bh = api
    parts.append(rounded_box(x, y, bw, bh, stroke=ACCENT_2))
    parts.append(text(x + 16, y + 30, "FastAPI Service", size=15, color=TEXT, weight="bold"))
    parts.append(text(x + 16, y + 50, "thin transport layer", size=11, color=MUTED))
    parts.append(lines(x + 16, y + 82, ["• WebSocket: live", "  snapshot stream", "• REST: HITL controls", "• serves static UI", "single asyncio loop"], size=12))

    # Dashboard
    x, y, bw, bh = dash
    parts.append(rounded_box(x, y, bw, bh, stroke=GOOD))
    parts.append(text(x + 16, y + 30, "Operations Dashboard", size=14.5, color=TEXT, weight="bold"))
    parts.append(text(x + 16, y + 50, "vanilla JS · no build · no CDN", size=10.5, color=MUTED))
    parts.append(lines(x + 16, y + 82, ["• allocation Gantt", "• KPIs vs baseline", "• live event stream", "• lock / override /", "  approve controls"], size=12))

    # Flow arrows
    parts.append(arrow(sim[0] + sim[2], 250, eng[0], 250, label="events"))
    parts.append(arrow(eng[0] + eng[2], 240, api[0], 240, label="snapshots"))
    parts.append(arrow(api[0] + api[2], 235, dash[0], 235, label="WebSocket"))
    # HITL feedback loop (dashboard -> api -> engine)
    parts.append(arrow(dash[0] + 60, dash[1] + dash[3], dash[0] + 60, 470, color=MUTED, dashed=True))
    parts.append(f'<line x1="{dash[0] + 60}" y1="470" x2="{api[0] + 100}" y2="470" stroke="{MUTED}" stroke-width="2" stroke-dasharray="6 4"/>')
    parts.append(f'<line x1="{api[0] + 100}" y1="470" x2="{api[0] + 100}" y2="{api[1] + api[3]}" stroke="{MUTED}" stroke-width="2" stroke-dasharray="6 4" marker-end="url(#arrowmute)"/>')
    parts.append(text(api[0] + 115, 466, "human decisions (REST) → re-optimise", size=11, color=MUTED))
    parts.append(f'<line x1="{api[0]}" y1="300" x2="{eng[0] + eng[2]}" y2="300" stroke="{MUTED}" stroke-width="2" stroke-dasharray="6 4" marker-end="url(#arrowmute)"/>')

    # Modes ribbon
    parts.append(text(eng[0] + eng[2] // 2, 466, "modes: Automated · Human-in-the-loop · Hybrid", size=11.5, color=ACCENT, anchor="middle", weight="bold"))

    # Production mapping row
    py = 540
    parts.append(text(28, py - 8, "PRODUCTION MAPPING  (design notes — not runtime dependencies)", size=12, color=IMMEDIATE, weight="bold"))
    maps = [
        (40, "Apache Kafka", "event streaming backbone"),
        (300, "Java / C++ core", "low-latency optimisation"),
        (470, "Oracle", "ACID persistence"),
        (700, "AWS GovCloud", "secure deployment"),
        (950, "Angular / secure web", "operator clients"),
    ]
    for mx, title_s, desc in maps:
        bw2 = 215 if mx in (40, 700, 950) else 150
        parts.append(rounded_box(mx, py + 6, bw2, 64, fill=PANEL_2, stroke=IMMEDIATE, dashed=True, rx=8))
        parts.append(text(mx + 14, py + 32, title_s, size=12.5, color=TEXT, weight="bold"))
        parts.append(text(mx + 14, py + 50, desc, size=10.5, color=MUTED))

    parts.append(text(28, h - 18, "Synthetic, unclassified demonstration · an independent homage to Roster-5 Software's “Iterated Distributed Feedback”.", size=11, color=MUTED))
    parts.append("</svg>")
    return "".join(parts)


# ----------------------------------------------------------------------------------------
# Diagram 2: optimizer algorithm / data-flow
# ----------------------------------------------------------------------------------------


def algorithm_flow() -> str:
    w, h = 1200, 640
    parts = [_svg_open(w, h, "Optimizer — Iterated Distributed Feedback", "Anytime metaheuristic: greedy seed → local search with ejection chains → iterated restarts (deterministic per seed)")]

    # Input
    parts.append(rounded_box(40, 120, 200, 150, stroke=ACCENT_2))
    parts.append(text(56, 148, "Input", size=14, color=TEXT, weight="bold"))
    parts.append(lines(56, 174, ["known taskings", "live assets (now)", "committed work", "human locks", "→ all immovable"], size=12))

    # Stage 1: seeds
    parts.append(rounded_box(290, 96, 250, 200, stroke=BORDER))
    parts.append(text(306, 122, "1 · Seed", size=14, color=TEXT, weight="bold"))
    parts.append(text(306, 140, "three greedy constructions", size=10.5, color=MUTED))
    for i, (lab, col) in enumerate([("value-density first", ACCENT), ("highest-weight first", ACCENT_2), ("FIFO (arrival order)", MUTED)]):
        yy = 154 + i * 34
        parts.append(rounded_box(306, yy, 218, 28, fill=PANEL, stroke=col, rx=6))
        parts.append(text(316, yy + 19, lab, size=11.5, color=TEXT))
    parts.append(text(306, 280, "keep best  ⇒  ≥ FIFO guarantee", size=11, color=GOOD, weight="bold"))

    # Stage 2: local search
    parts.append(rounded_box(590, 96, 300, 200, fill="#101a2b", stroke=ACCENT, width=2))
    parts.append(text(606, 122, "2 · Local search (descent)", size=14, color=TEXT, weight="bold"))
    parts.append(rounded_box(606, 138, 268, 56, fill=PANEL, stroke=ACCENT_2, rx=8))
    parts.append(text(618, 158, "INSERT", size=12.5, color=ACCENT, weight="bold"))
    parts.append(text(618, 176, "place an unscheduled tasking in free space", size=10.5, color=MUTED))
    parts.append(rounded_box(606, 200, 268, 78, fill=PANEL, stroke=FLASH, rx=8))
    parts.append(text(618, 220, "EVICT-AND-INSERT  (ejection chain)", size=12.5, color=IMMEDIATE, weight="bold"))
    parts.append(lines(618, 238, ["drop cheapest colliding set (never a lock),", "insert the valuable tasking, then re-home", "the evicted ones on other assets"], size=10.5, dy=15))

    # Stage 3: iterated restarts
    parts.append(rounded_box(940, 96, 220, 200, stroke=ACCENT_2))
    parts.append(text(956, 122, "3 · Iterate", size=14, color=TEXT, weight="bold"))
    parts.append(text(956, 140, "escape local optima", size=10.5, color=MUTED))
    parts.append(lines(956, 168, ["perturb: seeded", "random “kick”", "(evict a few)", "→ re-descend", "→ keep best seen"], size=12))

    # Output
    parts.append(rounded_box(940, 330, 220, 120, stroke=GOOD))
    parts.append(text(956, 358, "Output", size=14, color=TEXT, weight="bold"))
    parts.append(lines(956, 384, ["best feasible schedule", "objective ≥ baseline", "deterministic per seed"], size=11.5))

    # Arrows along the pipeline
    parts.append(arrow(240, 195, 290, 195))
    parts.append(arrow(540, 195, 590, 195))
    parts.append(arrow(890, 195, 940, 195))
    # feedback loop: objective delta gates moves (the "feedback")
    parts.append(f'<path d="M 740 296 C 740 340, 740 360, 740 360 L 740 388" fill="none" stroke="{GOOD}" stroke-width="2" marker-end="url(#arrow)"/>')
    parts.append(rounded_box(606, 360, 268, 70, fill=PANEL_2, stroke=GOOD, rx=8))
    parts.append(text(618, 384, "Feedback signal", size=12.5, color=GOOD, weight="bold"))
    parts.append(lines(618, 402, ["accept a move iff the objective strictly", "rises — each asset’s local change propagates"], size=10.5, dy=15))
    parts.append(f'<path d="M 940 410 C 910 410, 900 400, 900 250 L 894 250" fill="none" stroke="{MUTED}" stroke-width="2" stroke-dasharray="6 4" marker-end="url(#arrowmute)"/>')
    parts.append(text(700, 470, "iterate restarts → descent → feedback, until no improving move (anytime: best-so-far always valid)", size=11.5, color=MUTED, anchor="middle"))

    # Objective + constraints footer
    parts.append(rounded_box(40, 510, 1120, 90, fill=PANEL, stroke=BORDER))
    parts.append(text(60, 538, "Objective", size=13, color=ACCENT, weight="bold"))
    parts.append(text(60, 562, "maximise  Σ weight(j)  over taskings j served on time", size=12.5, color=TEXT, mono=True))
    parts.append(text(60, 584, "(prize-collecting; NP-hard — hence a fast metaheuristic, not an exact solver)", size=11, color=MUTED))
    parts.append(text(640, 538, "Hard constraints (always satisfied)", size=13, color=IMMEDIATE, weight="bold"))
    parts.append(lines(640, 560, ["• one tasking per asset at a time (no double-booking)   • within release/deadline window", "• asset online & capability/region eligible              • locks & committed work immovable"], size=11, dy=18))

    parts.append("</svg>")
    return "".join(parts)


def main() -> None:
    (OUT_DIR / "architecture.svg").write_text(architecture())
    (OUT_DIR / "algorithm_flow.svg").write_text(algorithm_flow())
    print(f"Wrote architecture.svg and algorithm_flow.svg to {OUT_DIR}")


if __name__ == "__main__":
    main()
