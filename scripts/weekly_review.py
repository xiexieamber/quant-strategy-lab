#!/usr/bin/env python3
"""Review previous bot decisions against current prices."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.strategies.serenity_quant import fetch_snapshot, localize_action


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A 股策略记忆周复盘")
    parser.add_argument("--memory", type=Path, default=ROOT / "results" / "memory" / "decision_memory.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "memory" / "weekly_review.md")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.memory.exists():
        raise FileNotFoundError(args.memory)

    latest_by_code = {}
    with args.memory.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            decision = item["decision"]
            latest_by_code[decision["code"]] = decision

    rows = []
    grouped = defaultdict(list)
    for decision in latest_by_code.values():
        snapshot = fetch_snapshot({"code": decision["code"], "name": decision["name"]})
        entry = float(decision["close"])
        current = float(snapshot.close)
        ret = current / entry - 1 if entry > 0 else 0.0
        row = {
            "code": decision["code"],
            "name": decision["name"],
            "action": decision["action"],
            "entry": entry,
            "current": current,
            "return": ret,
            "score": decision["score"],
        }
        rows.append(row)
        grouped[decision["action"]].append(ret)

    lines = [
        f"# A 股策略周复盘 - {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## 决策表现回看",
        "",
        "| 代码 | 名称 | 当时动作 | 记录价 | 当前价 | 收益 | 分数 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in sorted(rows, key=lambda x: x["return"], reverse=True):
        lines.append(
            f"| {row['code']} | {row['name']} | {localize_action(row['action'])} | {row['entry']:.2f} | "
            f"{row['current']:.2f} | {row['return'] * 100:.2f}% | {row['score']:.1f} |"
        )

    lines.extend(["", "## 动作分类表现", ""])
    for action, values in grouped.items():
        avg = sum(values) / len(values) if values else 0.0
        lines.append(f"- {localize_action(action)}: 数量={len(values)}, 平均收益={avg * 100:.2f}%")

    lines.extend(
        [
            "",
            "## 下次权重调整建议",
            "",
            "- 如果过热/观察名单连续 2 次跑赢买入候选，可以小幅降低过热惩罚。",
            "- 如果买入候选跑输观察名单，提高趋势和流动性要求。",
            "- 如果一手金额过大的股票持续跑赢，仍保持观察，除非本金提高。",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"review: {args.output}")


if __name__ == "__main__":
    main()
