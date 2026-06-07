"""
从 Yahoo Finance 下载历史行情数据。

用法示例:
    from src.data.fetch_data import fetch_ohlcv
    df = fetch_ohlcv("AAPL", start="2020-01-01", end="2024-12-31")
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf


def fetch_ohlcv(
    symbol: str,
    start: str = "2020-01-01",
    end: str | None = None,
    save_path: Path | None = None,
) -> pd.DataFrame:
    """
    下载指定标的的 OHLCV（开高低收量）数据。

    参数:
        symbol: 股票代码，如 "AAPL"（苹果）、"000001.SS"（上证指数）
        start: 开始日期，格式 "YYYY-MM-DD"
        end: 结束日期，默认到今天
        save_path: 可选，保存为 CSV 的路径

    返回:
        包含 Open, High, Low, Close, Volume 列的 DataFrame
    """
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, end=end)

    if df.empty:
        raise ValueError(f"未能获取 {symbol} 的数据，请检查代码和网络连接")

    df.index = pd.to_datetime(df.index)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(save_path)

    return df
