# evals/ — benchmark and evaluation assets

- `prompts/labels.jsonl` — frozen prompt set (E1/E2). User approval pending.
- `tasks_e4b.json` — **primary** benchmark task set (large-tool-output multi-turn;
  WP13 moved it here from `tasks/tasks_e4b.json`). User approval pending.
- `tasks/tasks_e3.json` — dev/regression task set (tiny single-file tasks).
- `runner.py` — E1/E2 spec-quality + question-quality runner (quota-aware, resumable).
- `run_e4.py` — E4 modes benchmark + E5 ablation runner (one model per experiment).
- `run_e3.py` / `run_e3b.py` — downstream-lift builders (dev set / non-trivial set).
- `run_e6.py` — Pareto chart; `make_report.py` — REPORT.md generator;
  `build_static_report.py` — static HTML report (S6 finalization).
- `quota.py` — QuotaTracker (normalized model keys, header-based, hourly probe).
- `orchestr.py` — `make eval-daily` orchestrator (stages S1-S6, STATUS.md blockers).
- `stamp.py` — content-hash result stamps (WP13 semantics).
- `results/` — all measured JSONs; `REPORT.md` — generated report.
