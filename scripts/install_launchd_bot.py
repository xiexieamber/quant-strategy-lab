#!/usr/bin/env python3
"""Install or remove macOS launchd schedules for the A-share bot."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安装 A 股策略机器人的 macOS 定时任务")
    parser.add_argument("--capital", type=float, default=50000, help="日报使用的本金")
    parser.add_argument(
        "--python",
        type=Path,
        default=ROOT / ".venv" / "bin" / "python",
        help="Python 解释器路径",
    )
    parser.add_argument("--install", action="store_true", help="安装并加载定时任务")
    parser.add_argument("--uninstall", action="store_true", help="卸载定时任务")
    parser.add_argument("--dry-run", action="store_true", help="只生成预览，不写入系统目录")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    jobs = _jobs(args.python, args.capital)

    if args.uninstall:
        _uninstall(jobs)
        return

    if args.dry_run:
        for label, plist in jobs.items():
            print(f"\n[{label}]")
            print(plistlib.dumps(plist, sort_keys=False).decode("utf-8"))
        return

    if not args.install:
        raise SystemExit("请指定 --install、--uninstall 或 --dry-run")

    if not args.python.exists():
        raise FileNotFoundError(args.python)

    (ROOT / "results" / "logs").mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    for label, plist in jobs.items():
        plist_path = LAUNCH_AGENTS / f"{label}.plist"
        plist_path.write_bytes(plistlib.dumps(plist, sort_keys=False))
        _launchctl("bootout", plist_path, allow_failure=True)
        _launchctl("bootstrap", plist_path)
        _launchctl("enable", plist_path, by_label=label)
        print(f"已安装：{plist_path}")


def _jobs(python: Path, capital: float) -> dict[str, dict]:
    logs = ROOT / "results" / "logs"
    daily_label = "com.codex.ashare.daily"
    weekly_label = "com.codex.ashare.weekly"
    common = {
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": False,
        "StandardOutPath": str(logs / "launchd.out.log"),
        "StandardErrorPath": str(logs / "launchd.err.log"),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "MPLCONFIGDIR": str(ROOT / "results" / "mplconfig"),
        },
    }
    return {
        daily_label: {
            "Label": daily_label,
            "ProgramArguments": [
                str(python),
                str(ROOT / "scripts" / "daily_a_share_bot.py"),
                "--capital",
                str(capital),
            ],
            "StartCalendarInterval": [
                {"Weekday": weekday, "Hour": 15, "Minute": 20}
                for weekday in range(1, 6)
            ],
            **common,
        },
        weekly_label: {
            "Label": weekly_label,
            "ProgramArguments": [
                str(python),
                str(ROOT / "scripts" / "weekly_review.py"),
            ],
            "StartCalendarInterval": {"Weekday": 5, "Hour": 16, "Minute": 30},
            **common,
        },
    }


def _uninstall(jobs: dict[str, dict]) -> None:
    for label in jobs:
        plist_path = LAUNCH_AGENTS / f"{label}.plist"
        _launchctl("bootout", plist_path, allow_failure=True)
        if plist_path.exists():
            plist_path.unlink()
        print(f"已卸载：{plist_path}")


def _launchctl(action: str, plist_path: Path, *, allow_failure: bool = False, by_label: str | None = None) -> None:
    uid = os.getuid()
    target = f"gui/{uid}"
    if action == "enable" and by_label:
        cmd = ["launchctl", "enable", f"{target}/{by_label}"]
    else:
        cmd = ["launchctl", action, target, str(plist_path)]
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if result.returncode != 0 and not allow_failure:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


if __name__ == "__main__":
    main()
