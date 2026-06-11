#!/usr/bin/env python3
"""Run a reproducible small-cap strategy experiment."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backtest.small_cap_engine import run_small_cap_backtest
from src.research.experiment import (
    load_json_config,
    make_experiment_dir,
    small_cap_config_from_dict,
    write_small_cap_experiment,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行小市值策略可复现实验")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "experiments" / "small_cap_h1_pro_baseline.json",
        help="实验配置 JSON",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="输出目录；默认写入 results/experiments/时间戳目录",
    )
    parser.add_argument("--notes", default="", help="写入 report.md 的备注")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_config = load_json_config(args.config)
    experiment_name = raw_config.get("name", args.config.stem)
    cfg = small_cap_config_from_dict(raw_config)

    output_dir = args.output_dir or make_experiment_dir("small-cap", experiment_name)
    print(f"运行实验: {experiment_name}")
    print(f"输出目录: {output_dir}")

    result = run_small_cap_backtest(cfg)
    files = write_small_cap_experiment(
        output_dir=output_dir,
        request_config=raw_config,
        resolved_config=cfg,
        result=result,
        notes=args.notes,
    )

    print("\n实验完成")
    for label, path in files.items():
        print(f"- {label}: {path}")


if __name__ == "__main__":
    main()

