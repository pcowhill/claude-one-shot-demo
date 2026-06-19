"""Proof-of-value benchmark: optimiser vs naive baselines, across a spread of scenarios.

Runs two honest comparisons and renders committed charts a non-engineer can read at a glance:

* **Offline** (full information): optimiser vs FIFO and priority-greedy on the same instance.
  This is the controlled, apples-to-apples measure of the optimiser's quality — and the
  headline number.
* **Online** (rolling-horizon): the optimiser engine vs a shadow FIFO engine, stepped in
  lockstep over the same event stream — the realised advantage when running live.

Outputs (all written to ``benchmarks/results/``):
    benchmark_results.json        — every number, also served at the API's /api/benchmark
    value_by_scenario.png         — delivered value, optimiser vs FIFO, per scenario
    uplift.png                    — offline vs online uplift, per scenario
    optimizer_convergence.png     — objective improving across the search (one scenario)
    priority_protection.png       — % of taskings served by priority tier (optimiser vs FIFO)
    solve_time.png                — solve time vs instance size (scalability)

Run with: ``python benchmarks/run_benchmarks.py`` (needs the dev requirements).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Make the project importable when run directly (``python benchmarks/run_benchmarks.py``).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib

matplotlib.use("Agg")  # headless: render to files, never to a screen
import matplotlib.pyplot as plt

from app.core.baseline import fifo_schedule, priority_greedy_schedule
from app.core.engine import AllocationEngine, Policy
from app.core.objective import compute_kpis, schedule_value
from app.core.optimizer import optimize
from app.core.scenario import benchmark_suite

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Palette mirrors the dashboard so the whole project reads as one designed system.
C_OPT = "#4cc9f0"
C_BASE = "#7286a3"
C_PRIORITY2 = "#5b8def"
PRIORITY_COLOR = {
    "ROUTINE": "#6b7a99",
    "PRIORITY": "#3fa7ff",
    "IMMEDIATE": "#ffb454",
    "FLASH": "#ff5d6c",
}


def _apply_dark_theme() -> None:
    """A dark matplotlib theme matching the operations dashboard."""
    plt.rcParams.update(
        {
            "figure.facecolor": "#0e1420",
            "savefig.facecolor": "#0e1420",
            "axes.facecolor": "#121a28",
            "axes.edgecolor": "#33425a",
            "axes.labelcolor": "#cfe0f5",
            "axes.titlecolor": "#ffffff",
            "axes.grid": True,
            "grid.color": "#22304a",
            "grid.linewidth": 0.6,
            "text.color": "#e7eef8",
            "xtick.color": "#a9bbd6",
            "ytick.color": "#a9bbd6",
            "font.size": 11,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "figure.dpi": 160,
        }
    )


# ----------------------------------------------------------------------------------------
# Run the comparisons
# ----------------------------------------------------------------------------------------


def _online_delivered(scenario: Any, policy: Policy) -> float:
    """Run an engine to completion under ``policy`` and return delivered value."""
    engine = AllocationEngine(scenario, policy=policy)
    engine.run_to_completion()
    return engine.delivered_value()


def run_suite() -> dict[str, Any]:
    """Execute the full benchmark and return a JSON-serialisable results dictionary."""
    scenarios = benchmark_suite()
    rows: list[dict[str, Any]] = []
    agg_priority = {
        "optimizer": {t: [0, 0] for t in PRIORITY_COLOR},
        "fifo": {t: [0, 0] for t in PRIORITY_COLOR},
    }
    convergence: dict[str, Any] = {}

    for scenario in scenarios:
        fifo = fifo_schedule(scenario)
        prio = priority_greedy_schedule(scenario)
        result = optimize(scenario)

        fifo_value = schedule_value(scenario, fifo)
        opt_value = result.value
        offline_uplift = 100.0 * (opt_value - fifo_value) / fifo_value if fifo_value else 0.0

        online_opt = _online_delivered(scenario, Policy.OPTIMIZE)
        online_fifo = _online_delivered(scenario, Policy.FIFO)
        online_uplift = 100.0 * (online_opt - online_fifo) / online_fifo if online_fifo else 0.0

        fifo_kpis = compute_kpis(scenario, fifo)
        opt_kpis = compute_kpis(scenario, result.schedule)

        for tier in PRIORITY_COLOR:
            for engine_key, kpis in (("optimizer", opt_kpis), ("fifo", fifo_kpis)):
                served, total = kpis.served_by_priority.get(tier, (0, 0))
                agg_priority[engine_key][tier][0] += served
                agg_priority[engine_key][tier][1] += total

        rows.append(
            {
                "name": scenario.name,
                "n_requests": len(scenario.requests),
                "n_resources": len(scenario.resources),
                "horizon": scenario.horizon,
                "possible_value": round(scenario.total_possible_value, 1),
                "fifo_value": round(fifo_value, 1),
                "priority_value": round(schedule_value(scenario, prio), 1),
                "optimizer_value": round(opt_value, 1),
                "offline_uplift_pct": round(offline_uplift, 1),
                "online_optimizer_value": round(online_opt, 1),
                "online_fifo_value": round(online_fifo, 1),
                "online_uplift_pct": round(online_uplift, 1),
                "fifo_capture_pct": round(fifo_kpis.value_capture_pct, 1),
                "optimizer_capture_pct": round(opt_kpis.value_capture_pct, 1),
                "optimizer_elapsed_ms": round(result.elapsed_ms, 1),
                "passes": result.passes,
                "moves": result.moves_applied,
            }
        )

        # Keep the richest convergence trace (most search activity) for the convergence chart.
        if not convergence or len(result.history) > len(convergence.get("history", [])):
            convergence = {
                "scenario": scenario.name,
                "history": [round(v, 1) for v in result.history],
                "baseline": round(result.baseline_value, 1),
                "seed": round(result.seed_value, 1),
                "final": round(result.value, 1),
            }

    offline_uplifts = [r["offline_uplift_pct"] for r in rows]
    online_uplifts = [r["online_uplift_pct"] for r in rows]
    summary = {
        "total_scenarios": len(rows),
        "mean_offline_uplift_pct": round(sum(offline_uplifts) / len(rows), 1),
        "max_offline_uplift_pct": round(max(offline_uplifts), 1),
        "mean_online_uplift_pct": round(sum(online_uplifts) / len(rows), 1),
        "max_online_uplift_pct": round(max(online_uplifts), 1),
    }
    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "summary": summary,
        "scenarios": rows,
        "priority_protection": agg_priority,
        "convergence": convergence,
    }


# ----------------------------------------------------------------------------------------
# Charts
# ----------------------------------------------------------------------------------------


def chart_value_by_scenario(results: dict[str, Any]) -> None:
    """Grouped bars: delivered value, optimiser vs FIFO, per scenario, with uplift labels."""
    rows = results["scenarios"]
    names = [r["name"] for r in rows]
    opt = [r["optimizer_value"] for r in rows]
    fifo = [r["fifo_value"] for r in rows]
    x = range(len(names))
    w = 0.38

    fig, ax = plt.subplots(figsize=(9.5, 5.0))
    ax.bar([i - w / 2 for i in x], fifo, w, label="FIFO baseline", color=C_BASE)
    ax.bar([i + w / 2 for i in x], opt, w, label="Optimizer", color=C_OPT)
    for i, r in enumerate(rows):
        ax.annotate(
            f"+{r['offline_uplift_pct']:.0f}%",
            (i + w / 2, opt[i]),
            textcoords="offset points",
            xytext=(0, 5),
            ha="center",
            fontsize=10,
            fontweight="bold",
            color="#7CF0C8",
        )
    ax.set_title("Mission value delivered — optimizer vs FIFO baseline (offline)")
    ax.set_ylabel("Weighted mission value")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, rotation=15, ha="right")
    ax.legend(facecolor="#16202f", edgecolor="#33425a")
    ax.margins(y=0.16)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "value_by_scenario.png")
    plt.close(fig)


def chart_uplift(results: dict[str, Any]) -> None:
    """Grouped bars: offline vs online uplift percentage per scenario."""
    rows = results["scenarios"]
    names = [r["name"] for r in rows]
    offline = [r["offline_uplift_pct"] for r in rows]
    online = [r["online_uplift_pct"] for r in rows]
    x = range(len(names))
    w = 0.38

    fig, ax = plt.subplots(figsize=(9.5, 5.0))
    ax.bar([i - w / 2 for i in x], offline, w, label="Offline (full information)", color=C_OPT)
    ax.bar([i + w / 2 for i in x], online, w, label="Online (live, rolling-horizon)", color=C_PRIORITY2)
    mean_off = results["summary"]["mean_offline_uplift_pct"]
    ax.axhline(mean_off, color="#7CF0C8", linestyle="--", linewidth=1, label=f"offline mean +{mean_off:.0f}%")
    ax.set_title("Optimizer uplift over FIFO baseline")
    ax.set_ylabel("Improvement in mission value (%)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, rotation=15, ha="right")
    ax.legend(facecolor="#16202f", edgecolor="#33425a")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "uplift.png")
    plt.close(fig)


def chart_convergence(results: dict[str, Any]) -> None:
    """Line: objective value improving across the iterated local search."""
    conv = results["convergence"]
    history = conv["history"]
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    ax.plot(range(len(history)), history, color=C_OPT, linewidth=2, marker="o", markersize=3, label="Optimizer objective")
    ax.axhline(conv["baseline"], color=C_BASE, linestyle="--", linewidth=1.4, label=f"FIFO baseline ({conv['baseline']:.0f})")
    ax.axhline(conv["seed"], color="#ffb454", linestyle=":", linewidth=1.4, label=f"Greedy seed ({conv['seed']:.0f})")
    ax.set_title(f"Iterated local search converging — “{conv['scenario']}” scenario")
    ax.set_xlabel("Search checkpoint (descent passes + iterated restarts)")
    ax.set_ylabel("Objective (mission value)")
    ax.legend(facecolor="#16202f", edgecolor="#33425a", loc="lower right")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "optimizer_convergence.png")
    plt.close(fig)


def chart_priority_protection(results: dict[str, Any]) -> None:
    """Grouped bars: % of taskings served by priority tier, optimiser vs FIFO (aggregated)."""
    pp = results["priority_protection"]
    tiers = ["FLASH", "IMMEDIATE", "PRIORITY", "ROUTINE"]
    opt_pct = [100.0 * pp["optimizer"][t][0] / pp["optimizer"][t][1] if pp["optimizer"][t][1] else 0 for t in tiers]
    fifo_pct = [100.0 * pp["fifo"][t][0] / pp["fifo"][t][1] if pp["fifo"][t][1] else 0 for t in tiers]
    x = range(len(tiers))
    w = 0.38

    fig, ax = plt.subplots(figsize=(8.5, 5.0))
    ax.bar([i - w / 2 for i in x], fifo_pct, w, label="FIFO baseline", color=C_BASE)
    bars = ax.bar([i + w / 2 for i in x], opt_pct, w, label="Optimizer")
    for bar, tier in zip(bars, tiers, strict=True):
        bar.set_color(PRIORITY_COLOR[tier])
    ax.set_title("High-priority protection — taskings served by tier (all scenarios)")
    ax.set_ylabel("Taskings served (%)")
    ax.set_ylim(0, 105)
    ax.set_xticks(list(x))
    ax.set_xticklabels(tiers)
    ax.legend(facecolor="#16202f", edgecolor="#33425a")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "priority_protection.png")
    plt.close(fig)


def chart_solve_time(results: dict[str, Any]) -> None:
    """Scatter: optimiser solve time vs instance size (scalability)."""
    rows = sorted(results["scenarios"], key=lambda r: r["n_requests"])
    sizes = [r["n_requests"] for r in rows]
    times = [r["optimizer_elapsed_ms"] for r in rows]
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    ax.plot(sizes, times, color=C_OPT, linewidth=1.6, marker="o", markersize=6)
    for r in rows:
        ax.annotate(r["name"], (r["n_requests"], r["optimizer_elapsed_ms"]), textcoords="offset points", xytext=(6, 4), fontsize=9, color="#a9bbd6")
    ax.set_title("Optimizer solve time vs instance size")
    ax.set_xlabel("Number of taskings")
    ax.set_ylabel("Solve time (ms)")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "solve_time.png")
    plt.close(fig)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _apply_dark_theme()
    print("Running benchmark suite (offline + online comparisons)…")
    results = run_suite()

    (RESULTS_DIR / "benchmark_results.json").write_text(json.dumps(results, indent=2))
    chart_value_by_scenario(results)
    chart_uplift(results)
    chart_convergence(results)
    chart_priority_protection(results)
    chart_solve_time(results)

    s = results["summary"]
    print(f"  scenarios            : {s['total_scenarios']}")
    print(f"  mean offline uplift  : +{s['mean_offline_uplift_pct']}%  (max +{s['max_offline_uplift_pct']}%)")
    print(f"  mean online uplift   : +{s['mean_online_uplift_pct']}%  (max +{s['max_online_uplift_pct']}%)")
    print(f"  charts + JSON written to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
