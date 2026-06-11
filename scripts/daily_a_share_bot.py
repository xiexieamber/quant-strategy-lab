#!/usr/bin/env python3
"""Run the daily A-share research bot and persist decision memory."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.strategies.serenity_quant import (
    append_decision_memory,
    build_daily_report,
    fetch_snapshot,
    load_strategy_config,
    score_candidate,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A 股每日研究机器人")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "serenity_quant_v1.yaml",
    )
    parser.add_argument("--capital", type=float, help="覆盖配置中的本金")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--output-root", type=Path, default=ROOT / "results" / "daily")
    parser.add_argument("--memory", type=Path, default=ROOT / "results" / "memory" / "decision_memory.jsonl")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_strategy_config(args.config)
    if args.capital:
        cfg["capital"] = args.capital

    rows = []
    errors = []
    for item in cfg["universe"]:
        try:
            snapshot = fetch_snapshot(item)
            rows.append(score_candidate(item, snapshot, cfg))
        except Exception as exc:
            errors.append({"code": item["code"], "name": item["name"], "error": str(exc)})

    rows.sort(key=lambda r: (r["action"] == "buy_candidate", r["score"]), reverse=True)
    run_id = f"{args.date}-{datetime.now().strftime('%H%M%S')}"
    out_dir = args.output_root / args.date
    out_dir.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(rows).to_csv(out_dir / "candidates.csv", index=False)
    (out_dir / "candidates.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if errors:
        (out_dir / "errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")

    report = build_daily_report(rows, cfg, run_date=args.date)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    append_decision_memory(args.memory, rows, run_id)

    print(f"report: {out_dir / 'report.md'}")
    print(f"candidates: {out_dir / 'candidates.csv'}")
    print(f"memory: {args.memory}")
    if errors:
        print(f"errors: {out_dir / 'errors.json'}")


if __name__ == "__main__":
    main()

