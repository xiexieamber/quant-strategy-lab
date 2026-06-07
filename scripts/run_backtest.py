#!/usr/bin/env python3
"""
运行双均线策略回测的示例脚本。

在项目根目录执行:
    python scripts/run_backtest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# 把项目根目录加入 Python 路径，方便 import src
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backtest.engine import run_backtest
from src.data.fetch_data import fetch_ohlcv
from src.strategies.dual_moving_average import dual_moving_average_signals


def load_config() -> dict:
    config_path = ROOT / "strategies" / "dual_ma" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    config = load_config()
    symbol = config["symbol"]
    start = config["start"]
    end = config.get("end")
    short_window = config["short_window"]
    long_window = config["long_window"]
    initial_cash = config.get("initial_cash", 100_000)

    print("=" * 50)
    print("  Quant Strategy Lab - 双均线回测")
    print("=" * 50)
    print(f"\n📥 正在下载 {symbol} 数据 ({start} ~ {end or '今天'})...")

    df = fetch_ohlcv(symbol, start=start, end=end)
    print(f"   共 {len(df)} 个交易日")

    print(f"\n📊 计算信号 (短期={short_window}, 长期={long_window})...")
    signals_df = dual_moving_average_signals(
        df["Close"],
        short_window=short_window,
        long_window=long_window,
    )

    print("\n🔄 运行回测...")
    result = run_backtest(
        prices=signals_df["close"],
        signals=signals_df["signal"],
        initial_cash=initial_cash,
    )

    print("\n" + "=" * 50)
    print("  回测结果")
    print("=" * 50)
    print(f"  初始资金:   {initial_cash:,.0f}")
    print(f"  最终资金:   {result.equity_curve.iloc[-1]:,.0f}")
    print(f"  总收益率:   {result.total_return * 100:+.2f}%")
    print(f"  年化收益:   {result.annual_return * 100:+.2f}%")
    print(f"  最大回撤:   {result.max_drawdown * 100:.2f}%")
    print(f"  夏普比率:   {result.sharpe_ratio:.2f}")
    print(f"  交易次数:   {result.trades}")
    print("=" * 50)
    print("\n💡 提示: 修改 strategies/dual_ma/config.yaml 可调整参数后重新运行")


if __name__ == "__main__":
    main()
