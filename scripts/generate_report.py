"""Generate ``report.html`` — a single, self-contained file that opens with a double-click.

Everything is inlined (CSS, PNG charts/screenshots as base64 data URIs, SVG diagrams as markup)
so the report renders fully offline over ``file://`` with no server and no network. It is the
primary artifact for a non-engineer: the whole story — value, live system, how it works — in one
scrollable page.

Run with: ``python scripts/generate_report.py`` (after the charts/diagrams/screenshots exist).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmarks" / "results"
SHOTS = ROOT / "artifacts" / "screenshots"
DIAGRAMS = ROOT / "diagrams"
OUT = ROOT / "report.html"


def _png_data_uri(path: Path) -> str:
    """Base64-encode a PNG as a data URI (so it travels inside the HTML)."""
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/png;base64,{encoded}"


def _svg_inline(path: Path) -> str:
    """Return raw SVG markup (already self-contained) for inline embedding."""
    return path.read_text() if path.exists() else ""


def _img(path: Path, alt: str) -> str:
    uri = _png_data_uri(path)
    # No lazy-loading: assets are embedded, and eager loading keeps the report print/PDF-safe.
    return f'<img src="{uri}" alt="{alt}"/>' if uri else f"<em>missing: {alt}</em>"


def build() -> str:
    results = json.loads((BENCH / "benchmark_results.json").read_text())
    summary = results["summary"]
    rows = results["scenarios"]

    table_rows = "".join(
        f"<tr><td>{r['name']}</td><td>{r['n_requests']}</td><td>{r['n_resources']}</td>"
        f"<td class='num'>{r['fifo_value']:.0f}</td><td class='num'>{r['optimizer_value']:.0f}</td>"
        f"<td class='num good'>+{r['offline_uplift_pct']:.0f}%</td>"
        f"<td class='num'>+{r['online_uplift_pct']:.0f}%</td>"
        f"<td class='num'>{r['optimizer_elapsed_ms']:.0f} ms</td></tr>"
        for r in rows
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Roster-5 · Allocation Engine — Project Report</title>
<style>
  :root {{ --bg:#0a0e16; --panel:#121a28; --panel2:#16202f; --border:#243246; --text:#e7eef8;
    --muted:#93a6c4; --accent:#4cc9f0; --accent2:#5b8def; --good:#46d39a; --flash:#ff5d6c; --immediate:#ffb454; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    line-height:1.6; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:0 24px 80px; }}
  header.hero {{ background:linear-gradient(180deg,#16263a,#0c1320); border-bottom:1px solid var(--border);
    padding:46px 24px 38px; }}
  .hero-inner {{ max-width:1080px; margin:0 auto; }}
  .eyebrow {{ color:var(--accent); font-weight:700; letter-spacing:1.5px; font-size:12px; }}
  h1 {{ font-size:34px; margin:8px 0 6px; }}
  h2 {{ font-size:23px; margin:46px 0 6px; border-left:3px solid var(--accent); padding-left:12px; }}
  h3 {{ font-size:16px; color:var(--accent); margin:26px 0 6px; }}
  p.lead {{ font-size:17px; color:#cdd9ec; max-width:760px; }}
  .muted {{ color:var(--muted); }} .good {{ color:var(--good); }}
  .num {{ font-variant-numeric:tabular-nums; }}
  .stat-row {{ display:flex; gap:14px; flex-wrap:wrap; margin-top:22px; }}
  .stat {{ background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:14px 18px; min-width:165px; }}
  .stat .big {{ font-size:30px; font-weight:750; }}
  .stat .lbl {{ font-size:11px; letter-spacing:1px; color:var(--muted); text-transform:uppercase; }}
  .stat.hero-stat {{ border-color:#2c4a6b; background:linear-gradient(180deg,#14283d,#101a2a); }}
  .card {{ background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:18px; margin-top:18px; }}
  figure {{ margin:18px 0; }}
  img {{ width:100%; border:1px solid var(--border); border-radius:12px; display:block; }}
  figure.diagram {{ background:#0e1420; border:1px solid var(--border); border-radius:12px; padding:8px; overflow:auto; }}
  figure.diagram svg {{ width:100%; height:auto; display:block; }}
  figcaption {{ color:var(--muted); font-size:13px; margin-top:8px; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
  table {{ width:100%; border-collapse:collapse; margin-top:14px; font-size:14px; }}
  th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--border); }}
  th {{ color:var(--muted); font-size:11px; letter-spacing:0.6px; text-transform:uppercase; }}
  td.num {{ text-align:right; }}
  .callout {{ background:linear-gradient(90deg,rgba(76,201,240,0.10),transparent); border:1px solid #244a63;
    border-radius:12px; padding:16px 18px; margin-top:18px; }}
  .pill {{ display:inline-block; background:#16202f; border:1px solid var(--border); border-radius:20px;
    padding:3px 11px; font-size:12px; color:var(--muted); margin:3px 4px 0 0; }}
  ul {{ max-width:820px; }} li {{ margin:5px 0; }}
  code {{ background:#0e1726; border:1px solid var(--border); border-radius:5px; padding:1px 6px; font-size:13px; }}
  .footer {{ color:var(--muted); font-size:13px; margin-top:50px; border-top:1px solid var(--border); padding-top:18px; }}
  @media (max-width:820px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
</style></head>
<body>
<header class="hero"><div class="hero-inner">
  <div class="eyebrow">ROSTER-5 SOFTWARE · FLAGSHIP DEMONSTRATION</div>
  <h1>Event-Driven Resource-Allocation Engine</h1>
  <p class="lead">A real-time scheduler that allocates a stream of prioritised, deadline-bound
  taskings to a limited pool of assets — automatically, with a human in the loop, or hybrid — and
  an operations dashboard that shows it working. An independent, unclassified homage to Roster-5's
  “Iterated Distributed Feedback”, on entirely synthetic data.</p>
  <div class="stat-row">
    <div class="stat hero-stat"><div class="lbl">Mean value uplift vs FIFO</div><div class="big good">+{summary['mean_offline_uplift_pct']:.0f}%</div><div class="muted">offline · up to +{summary['max_offline_uplift_pct']:.0f}%</div></div>
    <div class="stat"><div class="lbl">Live uplift (rolling-horizon)</div><div class="big">+{summary['mean_online_uplift_pct']:.0f}%</div><div class="muted">up to +{summary['max_online_uplift_pct']:.0f}%</div></div>
    <div class="stat"><div class="lbl">Scenarios benchmarked</div><div class="big">{summary['total_scenarios']}</div><div class="muted">all feasible, all beat FIFO</div></div>
    <div class="stat"><div class="lbl">Solve time</div><div class="big">&lt;1s</div><div class="muted">per re-optimisation</div></div>
  </div>
</div></header>

<div class="wrap">

  <div class="callout">
    <strong>For non-engineers — the 30-second version.</strong> Imagine far more requests for
    satellites, drones and sensors than you have assets to cover them, each with a priority and a
    deadline, and the picture changing every minute. This engine continuously decides <em>which
    asset does which task, when</em>, to deliver the most mission value — and lets an operator
    step in to lock, override, or approve decisions. Across {summary['total_scenarios']} test
    scenarios it delivered <strong class="good">+{summary['mean_offline_uplift_pct']:.0f}% more
    mission value on average</strong> than the naive first-come-first-served approach. The
    pictures below are the system actually running.
  </div>

  <h2>1 · The live operations dashboard</h2>
  <p class="muted">Real screenshots (headless Chromium) of the running system in each of its three
  operating modes. No setup required to view them.</p>

  <figure>{_img(SHOTS / "01_automated_midshift.png", "Automated mode, mid-shift")}
    <figcaption><strong>Automated (closed-loop), mid-shift.</strong> The optimiser re-plans on every
    event. Top KPIs compare mission value delivered against a live FIFO baseline; the timeline shows
    each asset's allocations, the “NOW” line, and the growing queue of unscheduled taskings under
    oversubscription.</figcaption></figure>

  <div class="grid2">
    <figure>{_img(SHOTS / "02_hitl_manual.png", "Human-in-the-loop mode")}
      <figcaption><strong>Human-in-the-loop.</strong> A re-optimised plan waits for approval; an
      operator can lock, override, or hold any individual tasking and the engine re-plans around the
      decision.</figcaption></figure>
    <figure>{_img(SHOTS / "03_hybrid_exceptions.png", "Hybrid mode")}
      <figcaption><strong>Hybrid.</strong> Automated by default, but high-impact exceptions (an
      unserved FLASH/IMMEDIATE tasking) are surfaced for a human to review.</figcaption></figure>
  </div>

  <h2>2 · Proof of value</h2>
  <p>The optimiser is compared against an honest FIFO baseline (and a stronger priority-greedy
  baseline) on the <em>same</em> instances. Offline (full information) is the controlled measure of
  solution quality; online is the realised advantage running live, re-optimising on each event.</p>

  <figure>{_img(BENCH / "value_by_scenario.png", "Value by scenario")}
    <figcaption>Weighted mission value delivered, optimiser vs FIFO, across the scenario suite.</figcaption></figure>

  <div class="grid2">
    <figure>{_img(BENCH / "uplift.png", "Uplift per scenario")}
      <figcaption>Uplift over FIFO — offline vs online.</figcaption></figure>
    <figure>{_img(BENCH / "priority_protection.png", "Priority protection")}
      <figcaption>The optimiser protects high-priority work: it serves a far higher share of FLASH and
      IMMEDIATE taskings than FIFO.</figcaption></figure>
  </div>

  <table>
    <thead><tr><th>Scenario</th><th>Tasks</th><th>Assets</th><th>FIFO</th><th>Optimizer</th>
    <th>Offline</th><th>Online</th><th>Solve</th></tr></thead>
    <tbody>{table_rows}</tbody>
  </table>

  <h2>3 · How the optimiser works</h2>
  <p>The scheduling problem — weighted, prize-collecting interval scheduling on unrelated parallel
  machines with release times, deadlines and eligibility — is NP-hard, so the engine uses a fast
  anytime metaheuristic rather than an exact solver: a strong greedy <strong>seed</strong>, refined
  by local search with <strong>ejection chains</strong>, diversified by <strong>iterated</strong>
  restarts. Seeding from FIFO guarantees the result is never worse than the baseline; a fixed seed
  makes it fully deterministic.</p>

  <figure class="diagram">{_svg_inline(DIAGRAMS / "algorithm_flow.svg")}
    <figcaption>The optimiser pipeline and its ejection-chain “feedback” loop.</figcaption></figure>

  <div class="grid2">
    <figure>{_img(BENCH / "optimizer_convergence.png", "Convergence")}
      <figcaption>The objective improving from greedy seed to converged solution, well above FIFO.</figcaption></figure>
    <figure>{_img(BENCH / "solve_time.png", "Scalability")}
      <figcaption>Solve time stays well under a second across instance sizes.</figcaption></figure>
  </div>

  <h2>4 · Architecture</h2>
  <p>A pure-Python optimisation core (no framework coupling, fully unit- and property-tested) sits
  behind a thin FastAPI transport and a zero-build dashboard. Each layer maps cleanly onto Roster-5's
  production stack.</p>
  <figure class="diagram">{_svg_inline(DIAGRAMS / "architecture.svg")}
    <figcaption>Data flow from the synthetic event stream to the operator's screen, with the
    production mapping (notes only — not runtime dependencies).</figcaption></figure>
  <p>
    <span class="pill">core → Java / C++</span>
    <span class="pill">event stream → Apache Kafka</span>
    <span class="pill">persistence → Oracle</span>
    <span class="pill">deployment → AWS GovCloud</span>
    <span class="pill">deterministic &amp; testable</span>
  </p>

  <div class="callout">
    <strong>Engineering rigour.</strong> Clean layered architecture · full type hints (mypy strict)
    · ruff-clean · meaningful unit tests · <em>property-based</em> tests (Hypothesis) asserting the
    optimiser's invariants on thousands of random instances — no double-booking, all constraints
    respected, objective never below baseline · API integration tests · deterministic under a fixed
    seed. See <code>docs/RESEARCH_BRIEF.md</code> and <code>docs/PRODUCTION_MAPPING.md</code>.
  </div>

  <div class="footer">
    Synthetic, unclassified demonstration. No proprietary or classified data. An independent homage
    to Roster-5 Software, Inc.'s publicly described “Iterated Distributed Feedback”, built in the
    spirit of the open task-allocation literature (Contract-Net / CBBA / iterated local search).
    Generated from committed artifacts by <code>scripts/generate_report.py</code>.
  </div>
</div>
</body></html>
"""


def main() -> None:
    OUT.write_text(build())
    size_kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT} ({size_kb:.0f} KB, self-contained)")


if __name__ == "__main__":
    main()
