"""
双均线策略（Dual Moving Average）

当短期均线上穿长期均线时买入，下穿时卖出。
"""

from __future__ import annotations

import pandas as pd


def dual_moving_average_signals(
    prices: pd.Series,
    short_window: int = 5,
    long_window: int = 20,
) -> pd.DataFrame:
    """
    根据收盘价计算双均线买卖信号。

    参数:
        prices: 收盘价序列（index 为日期）
        short_window: 短期均线窗口（默认 5 天）
        long_window: 长期均线窗口（默认 20 天）

    返回:
        DataFrame，包含列:
        - close: 收盘价
        - ma_short: 短期均线
        - ma_long: 长期均线
        - signal: 1=持有/买入, 0=空仓/卖出
    """
    if short_window >= long_window:
        raise ValueError("短期窗口必须小于长期窗口")

    df = pd.DataFrame({"close": prices})
    df["ma_short"] = df["close"].rolling(short_window).mean()
    df["ma_long"] = df["close"].rolling(long_window).mean()

    # 短期 > 长期 → 信号为 1（做多），否则为 0
    df["signal"] = (df["ma_short"] > df["ma_long"]).astype(int)

    return df
