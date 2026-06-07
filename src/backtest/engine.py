"""
简单的向量化回测引擎（适合初学者理解原理）。

假设:
- 每次全仓买入/卖出（不做分批、不做杠杆）
- 不考虑手续费和滑点（后续可扩展）
- 信号在当天收盘后产生，下一根 K 线开盘执行
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    """回测结果"""

    total_return: float       # 总收益率（如 0.15 表示 15%）
    annual_return: float      # 年化收益率
    max_drawdown: float       # 最大回撤（负数，如 -0.12 表示最大跌 12%）
    sharpe_ratio: float       # 夏普比率（风险调整后收益，>1 通常较好）
    equity_curve: pd.Series   # 资金曲线
    trades: int               # 交易次数（买卖各算一次切换）


def run_backtest(
    prices: pd.Series,
    signals: pd.Series,
    initial_cash: float = 100_000.0,
) -> BacktestResult:
    """
    运行回测。

    参数:
        prices: 收盘价序列
        signals: 持仓信号，1=持有，0=空仓
        initial_cash: 初始资金

    返回:
        BacktestResult 对象
    """
    df = pd.DataFrame({"price": prices, "signal": signals}).dropna()
    if df.empty:
        raise ValueError("价格或信号数据为空")

    # 用前一天的信号决定今天的持仓（避免「未来函数」）
    df["position"] = df["signal"].shift(1).fillna(0)

    # 每日收益率 = 持仓 × 价格涨跌幅
    df["market_return"] = df["price"].pct_change().fillna(0)
    df["strategy_return"] = df["position"] * df["market_return"]

    equity = initial_cash * (1 + df["strategy_return"]).cumprod()
    total_return = equity.iloc[-1] / initial_cash - 1

    # 年化（按 252 个交易日估算）
    n_days = len(df)
    years = n_days / 252
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0

    # 最大回撤
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = drawdown.min()

    # 夏普比率（无风险利率简化为 0）
    daily_std = df["strategy_return"].std()
    sharpe_ratio = (
        df["strategy_return"].mean() / daily_std * np.sqrt(252)
        if daily_std > 0
        else 0.0
    )

    # 统计交易次数（持仓从 0→1 或 1→0 算一次切换）
    position_change = df["position"].diff().fillna(0)
    trades = int((position_change != 0).sum())

    return BacktestResult(
        total_return=total_return,
        annual_return=annual_return,
        max_drawdown=max_drawdown,
        sharpe_ratio=sharpe_ratio,
        equity_curve=equity,
        trades=trades,
    )
