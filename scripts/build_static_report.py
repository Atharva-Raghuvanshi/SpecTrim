"""S6: static HTML report from evals/results/*.json ONLY (no LLM calls).

Paired, same-stamp, complete cells only; everything else NOT MEASURED.
Output: docs/report.html (self-contained, inline CSS).
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
OUT = ROOT / "docs" / "report.html"

CSS = """body{font-family:system-ui,sans-serif;margin:24px;background:#0e1116;color:#e8edf2}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;margin:8px 0}
th,td{padding:6px 10px;border:1px solid #2a323d;font-size:14px;text-align:left}
h1,h2{color:#4f9cf9} .muted{color:#9aa7b4} .warn{color:#d9a53f}
code{background:#151a21;padding:2px 6px;border-radius:4px}"""


def esc(s: object) -> str:
    return html.escape(str(s))


def latest(pattern: str) -> dict | None:
    files = sorted(
        (f for f in RESULTS.glob(pattern) if "diagnosis" not in f.name),
        key=lambda p: p.stat().st_mtime,
    )
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def table(headers: list[str], rows: list[list[str]]) -> str:
    return ("<table><thead><tr>" + "".join(f"<th>{esc(h)}</th>" for h in headers) +
            "</tr></thead><tbody>" +
            "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows) +
            "</tbody></table>")


def e12_section() -> str:
    d = latest("e1_e2_v2_full.json")
    if not d or not d.get("ran"):
        return "<h2>E1/E2</h2><p class='muted'>NOT MEASURED</p>"
    per = d.get("per_prompt", [])
    completed = [p for p in per if p.get("valid") is not None and not p.get("infra_aborted")]
    valid = sum(1 for p in completed if p.get("valid"))
    rows = [[p["id"], str(p.get("valid")), str(p.get("error", "")[:80])]
            for p in per]
    p_ = d.get("e2_micro_precision")
    r_ = d.get("e2_micro_recall")
    summary = (f"ran {d.get('ran')}/30 · valid {valid}/{len(completed)} completed · "
               f"P {p_} · R {r_}" if p_ is not None else
               f"ran {d.get('ran')}/30 · valid {valid}/{len(completed)} completed · "
               f"P/R NOT MEASURED (quota-aborted prompts pending)")
    return (f"<h2>E1/E2 (v2 prompts, gpt-oss-120b pipeline)</h2><p>{esc(summary)}</p>" +
            table(["prompt", "valid", "error"], rows))


def e4_section() -> str:
    files = sorted(
        (f for f in RESULTS.glob("e4_*.json") if "diagnosis" not in f.name),
        key=lambda p: p.stat().st_mtime,
    )
    if not files:
        return "<h2>E4 paired</h2><p class='muted'>NOT MEASURED</p>"
    d = json.loads(files[-1].read_text(encoding="utf-8"))
    paired = d.get("paired") or {}
    if not paired.get("paired_n"):
        return ("<h2>E4 paired</h2><p class='muted'>NOT MEASURED — insufficient "
                "complete triples (re-run queued)</p>")
    pc = paired["per_config"]
    rows = []
    for cfg, s in pc.items():
        delta = f" ({s['tokens_in_delta_pct']:+.1f}%)" if "tokens_in_delta_pct" in s else ""
        lo, hi = s.get("pass_rate_wilson95", ("?", "?"))
        rows.append([cfg, str(s["n"]), f"{100 * s['pass_rate']:.0f}%",
                     f"{lo}–{hi}%", f"{s['tokens_in_avg']}", delta,
                     f"{s.get('tokens_in_avg_passed')}",
                     f"{s.get('tokens_in_avg_failed')}"])
    cmp_ = paired.get("comparison", {})
    warn = ""
    if cmp_.get("tokens_in_delta_pct", 0) is not None and cmp_.get("tokens_in_delta_pct", 0) > 0:
        warn = "<p class='warn'>NO token saving in balanced mode on this set — stated plainly.</p>"
    return (f"<h2>E4 paired ({esc(d.get('builder_model', ''))}, n={paired['paired_n']})</h2>"
            + warn +
            table(["config", "n", "pass", "Wilson95", "tok in avg", "Δ vs off",
                   "tok in (passed)", "tok in (failed)"], rows) +
            f"<p class='muted'>comparison {esc(str(cmp_.get('from')))}→{esc(str(cmp_.get('to')))}: "
            f"last won {cmp_.get('last_won', 0)}, first won {cmp_.get('first_won', 0)}, "
            f"tie {cmp_.get('tie', 0)}; sign-test p={esc(str(cmp_.get('sign_test_p')))}, "
            f"Fisher p={esc(str(cmp_.get('fisher_p')))}</p>")


def main() -> int:
    body = ["<h1>Query Optimizer — measured results</h1>"
            "<p class='muted'>generated from evals/results/*.json · paired, same-stamp, "
            "complete cells only; everything else NOT MEASURED</p>",
            e12_section(), e4_section()]
    OUT.write_text(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>QO measured results</title><style>{CSS}</style></head>"
        f"<body>{''.join(body)}</body></html>", encoding="utf-8")
    print(f"static report: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
