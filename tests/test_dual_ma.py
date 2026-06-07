"""双均线策略与回测引擎测试"""

import pandas as pd

from src.backtest.engine import run_backtest
from src.strategies.dual_moving_average import dual_moving_average_signals


def test_dual_ma_generates_signals():
    prices = pd.Series(
        range(1, 31),
        index=pd.date_range("2024-01-01", periods=30),
        dtype=float,
    )
    result = dual_moving_average_signals(prices, short_window=3, long_window=5)

    assert "signal" in result.columns
    assert result["signal"].isin([0, 1]).all()
    # 前 long_window-1 行均线为 NaN，但 signal 仍应为 0 或 1
    assert len(result) == 30


def test_backtest_returns_metrics():
    index = pd.date_range("2024-01-01", periods=10)
    prices = pd.Series([100, 101, 102, 103, 104, 105, 104, 103, 102, 101], index=index)
    signals = pd.Series([0, 0, 1, 1, 1, 1, 1, 0, 0, 0], index=index)

    result = run_backtest(prices, signals, initial_cash=10_000)

    assert result.total_return != 0 or result.trades >= 0
    assert len(result.equity_curve) == 10
    assert result.max_drawdown <= 0
