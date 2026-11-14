"""results/*.json -> results/plots/*.png. Plots only what the scripts measured; nothing is typed in.

Prefers the LLM-planner runs (benchmark.json, ablations.json); falls back to the solver-planner
"context sufficiency" runs and says so in the subtitle.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RES, OUT = ROOT / "results", ROOT / "results" / "plots"

# reference palette (light): blue = ToolGraph, orange = vector_k15; everything else recedes to gray
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#fcfcfb"
GRAPH, VEC = "#2a78d6", "#eb6834"
plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF, "font.size": 10.5,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.axisbelow": True, "text.color": INK, "axes.titleweight": "bold",
    "axes.titlesize": 13, "axes.titlelocation": "left",
})


def load(primary: str, fallback: str) -> tuple[dict | None, str]:
    for name, label in ((primary, None), (fallback, "context sufficiency (solver planner)")):
        f = RES / f"{name}.json"
        if f.exists():
            d = json.loads(f.read_text())
            return d, label or f"planner: {d['config'].get('planner', '?')}"
    return None, ""


def subtitle(ax, text: str) -> None:
    ax.text(0, 1.02, text, transform=ax.transAxes, color=INK2, fontsize=9.5, va="bottom")


def save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT / name, dpi=160)
    plt.close(fig)
    print("wrote", OUT / name)


def learning_curve(d: dict, label: str) -> None:
    agg = d["aggregate"]["graph_learning"]["success_rate"]
    its = list(range(len(agg)))
    mean = [a["mean"] for a in agg]
    std = [a["std"] for a in agg]
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    ax.fill_between(its, [m - s for m, s in zip(mean, std)], [m + s for m, s in zip(mean, std)], color=GRAPH, alpha=0.15, lw=0)
    ax.plot(its, mean, color=GRAPH, lw=2, marker="o", ms=6, mec=SURF, mew=2)
    ax.annotate(f"ToolGraph {mean[-1]:.0%}", (its[-1], mean[-1]), xytext=(6, 0), textcoords="offset points", va="center", color=INK, fontweight="bold")
    base = d["aggregate"]["baselines"]
    for name, color in (("all_tools", MUTED), ("vector_k15", VEC), ("vector_k8", MUTED)):
        if name in base:
            v = base[name]["success_rate"]["mean"]
            ax.axhline(v, color=color, lw=1.5, ls=(0, (4, 3)))
            ax.annotate(f"{name} {v:.0%}", (its[-1], v), xytext=(6, 4), textcoords="offset points", va="bottom", color=INK2)
    ax.set_xticks(its, ["static"] + [f"after {i}" for i in its[1:]])
    ax.set_xlabel("training batches seen (held-out test set, no updates during eval)")
    ax.set_ylabel("task success")
    ax.set_ylim(0, 1.05)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_xlim(-0.2, its[-1] + 1.6)
    ax.set_title("ToolGraph learns from outcomes", pad=22)
    subtitle(ax, f"mean ± std over {d['config']['seeds']} seeds · {label}")
    save(fig, "learning_curve.png")


def tokens(d: dict, label: str) -> None:
    base = d["aggregate"]["baselines"]
    agg = d["aggregate"]["graph_learning"]
    pts = [(n, base[n]["context_tokens"]["mean"], base[n]["success_rate"]["mean"], VEC if n == "vector_k15" else MUTED) for n in base]
    pts += [("ToolGraph static", agg["context_tokens"][0]["mean"], agg["success_rate"][0]["mean"], GRAPH),
            ("ToolGraph learned", agg["context_tokens"][-1]["mean"], agg["success_rate"][-1]["mean"], GRAPH)]
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    for n, x, y, c in pts:
        ax.scatter(x, y, s=90, color=c, edgecolor=SURF, linewidth=2, zorder=3)
        dx, dy, va = {"vector_k15": (8, 6, "bottom"), "ToolGraph learned": (8, 6, "bottom")}.get(n, (8, -4, "top"))
        ax.annotate(f"{n}\n{x:,.0f} tok · {y:.0%}", (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=9, color=INK2, va=va)
    ax.set_xscale("log")
    ax.set_xlabel("tool-definition tokens in the prompt per task (log scale)")
    ax.set_ylabel("task success")
    ax.set_ylim(0, 1.12)
    ax.set_xlim(300, 30000)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_title("Fewer tokens, more tasks completed", pad=22)
    subtitle(ax, f"up and to the left is better · {label}")
    save(fig, "tokens_vs_success.png")


def ablations(d: dict, label: str) -> None:
    agg = d["aggregate"]
    order = sorted(agg, key=lambda n: agg[n]["success_rate"]["mean"])
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    ys = range(len(order))
    vals = [agg[n]["success_rate"]["mean"] for n in order]
    errs = [agg[n]["success_rate"]["std"] for n in order]
    colors = [GRAPH if n == "graph_full" else VEC if n.startswith("vector") else MUTED for n in order]
    ax.barh(ys, vals, xerr=errs, color=colors, height=0.6, error_kw={"ecolor": INK2, "lw": 1, "capsize": 3})
    for y, v, e, n in zip(ys, vals, errs, order):
        ax.text(v + e + 0.03, y, f"{v:.0%}  · {agg[n]['context_tokens']['mean']:,.0f} tok", va="center", fontsize=9, color=INK2)
    ax.set_yticks(list(ys), order)
    ax.set_xlim(0, 1.25)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.grid(axis="y", visible=False)
    ax.set_title("Ablations: every graph component pulls weight", pad=22)
    subtitle(ax, f"task success on held-out tasks after training · {label}")
    save(fig, "ablations.png")


def scale(d: dict) -> None:
    rows = d["sizes"]
    n = [r["n_tools"] for r in rows]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4.2))
    for key, color, name in (("graph", GRAPH, "ToolGraph (static)"), ("vector_k15", VEC, "vector_k15")):
        ys = [r[key]["tool_recall"] for r in rows]
        a1.plot(n, ys, color=color, lw=2, marker="o", ms=6, mec=SURF, mew=2, label=name)
        a1.annotate(f"{ys[-1]:.0%}", (n[-1], ys[-1]), xytext=(6, 0), textcoords="offset points", va="center", color=INK2)
    a1.set_xscale("log")
    a1.set_ylim(0, 1)
    a1.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    a1.set_xlabel("tools in catalog (log)")
    a1.set_title("Recall of needed tools")
    a1.legend(frameon=False, loc="lower left")
    p50 = [r["graph"]["latency_p50_ms"] for r in rows]
    p95 = [r["graph"]["latency_p95_ms"] for r in rows]
    a2.plot(n, p50, color=GRAPH, lw=2, marker="o", ms=6, mec=SURF, mew=2, label="p50")
    a2.plot(n, p95, color=GRAPH, lw=2, ls=(0, (4, 3)), marker="o", ms=6, mec=SURF, mew=2, label="p95")
    for x, y in zip(n, p50):
        a2.annotate(f"{y:.0f} ms", (x, y), xytext=(10, -12), textcoords="offset points", ha="left", fontsize=9, color=INK2)
    a2.set_xscale("log")
    a2.set_xlabel("tools in catalog (log)")
    a2.set_ylabel("ms")
    a2.set_title("ToolGraph retrieval latency")
    a2.legend(frameon=False, loc="upper left")
    save(fig, "scale.png")


def failures(d: dict, label: str) -> None:
    """Failure reasons summed over seeds, per strategy (static vs learned graph)."""
    strat = {n: runs for n, runs in d["baselines"].items()}
    strat["graph static"] = [c[0] for c in d["graph_learning"]]
    strat["graph learned"] = [c[-1] for c in d["graph_learning"]]
    buckets = sorted({b for runs in strat.values() for r in runs for b in r["failures"]})
    colors = dict(zip(buckets, ["#1baf7a", "#6250d6", "#eda100", "#e87ba4", MUTED]))
    names = list(strat)
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    left = [0.0] * len(names)
    for b in buckets:
        vals = [sum(r["failures"].get(b, 0) for r in strat[n]) / sum(r["n"] for r in strat[n]) for n in names]
        ax.barh(names, vals, left=left, color=colors[b], height=0.6, label=b, edgecolor=SURF, linewidth=2)
        left = [l + v for l, v in zip(left, vals)]
    for y, tot in enumerate(left):
        ax.text(tot + 0.01, y, f"{tot:.0%}", va="center", fontsize=9, color=INK2)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.grid(axis="y", visible=False)
    ax.legend(frameon=False, loc="lower right")
    ax.set_title("Why tasks fail", pad=22)
    subtitle(ax, f"share of held-out tasks, by failure reason · {label}")
    save(fig, "failures.png")


def main() -> None:
    b, bl = load("benchmark", "sufficiency")
    if b:
        learning_curve(b, bl)
        tokens(b, bl)
        failures(b, bl)
    a, al = load("ablations", "ablations_sufficiency")
    if a:
        ablations(a, al)
    if (RES / "scale.json").exists():
        scale(json.loads((RES / "scale.json").read_text()))


if __name__ == "__main__":
    main()
