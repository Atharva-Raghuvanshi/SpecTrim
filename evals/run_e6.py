"""E6 Pareto (WP6): cost (tokens in avg) vs pass rate from measured E4/E5 files.

matplotlib PNG into evals/results/pareto.png + a text fallback table.
No LLM calls. Points only for cells that exist (partial runs are labelled).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def load_points() -> list[dict]:
    points: list[dict] = []
    for pattern, exp in (("e4_*.json", "E4"), ("e5_*.json", "E5")):
        for f in sorted(RESULTS.glob(pattern)):
            d = json.loads(f.read_text(encoding="utf-8"))
            for cfg, s in (d.get("summary") or {}).items():
                if s.get("n", 0) == 0:
                    continue
                points.append({
                    "experiment": exp,
                    "config": cfg,
                    "model": d.get("builder_model", "?"),
                    "n": s["n"],
                    "pass_rate": s["pass_rate"],
                    "tokens_in_avg": s["tokens_in_avg"],
                })
    return points


def main() -> int:
    points = load_points()
    if not points:
        print("no measured points yet — run E4/E5 first")
        return 0

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; writing text table only")
        plt = None

    table_lines = ["experiment config model n pass_rate tokens_in_avg"]
    for p in sorted(points, key=lambda x: (x["experiment"], x["config"])):
        table_lines.append(
            f"{p['experiment']} {p['config']} {p['model']} n={p['n']} "
            f"{100 * p['pass_rate']:.0f}% {p['tokens_in_avg']:.0f}"
        )

    if plt is not None:
        fig, ax = plt.subplots(figsize=(8, 5))
        markers = {"E4": "o", "E5": "s"}
        for exp in ("E4", "E5"):
            pts = [p for p in points if p["experiment"] == exp]
            if not pts:
                continue
            ax.scatter(
                [p["tokens_in_avg"] for p in pts],
                [100 * p["pass_rate"] for p in pts],
                s=60, marker=markers[exp],
                label=f"{exp} ({pts[0]['model'].split('/')[-1]}, n={sum(p['n'] for p in pts)})",
            )
            for p in pts:
                ax.annotate(p["config"], (p["tokens_in_avg"], 100 * p["pass_rate"]),
                            fontsize=7, xytext=(3, 3), textcoords="offset points")
        ax.set_xlabel("input tokens per task (provider-reported avg)")
        ax.set_ylabel("pass rate %")
        ax.set_title("Cost vs pass rate (measured cells; small n, indicative)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        out = RESULTS / "pareto.png"
        fig.savefig(out, dpi=120)
        print(f"saved {out}")

    (RESULTS / "pareto_table.txt").write_text("\n".join(table_lines), encoding="utf-8")
    print("\n".join(table_lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
