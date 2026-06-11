#!/usr/bin/env python3
"""本地复现果仁 ETF 20 日动量轮动（AkShare 数据）。"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.ak_cache import _fetch_hist_price  # noqa: E402
from src.data.ak_market import get_trade_days, to_std_code  # noqa: E402
from src.data.fetch_jqdata import _clamp_date_range, _ensure_auth, fetch_ohlcv_jq  # noqa: E402


# 来自果仁回测成交记录的实际轮动池 + 备用黄金
DEFAULT_UNIVERSE = ["512100", "588000", "510300", "510880", "518880"]
BACKUP_ETF = "518880"
BENCHMARK = "510300"  # 用沪深300 ETF 近似 000300 指数


@dataclass
class EtfMomentumConfig:
    start: str = "2025-03-07"
    end: str = "2026-06-09"
    period: int = 10
    ideal_count: int = 2
    max_count: int = 3
    ideal_position: float = 0.5
    position_limit: float = 0.5
    buy_rank: int = 2
    sell_rank: int = 3
    take_profit: float = 0.06
    stop_loss: float = 0.10
    mom_window: int = 20
    trade_cost: float = 0.001
    initial_cash: float = 100_000.0
    universe: tuple[str, ...] = tuple(DEFAULT_UNIVERSE)


def load_close_panel_jq(codes: list[str], start: str, end: str) -> pd.DataFrame:
    _ensure_auth()
    start, end = _clamp_date_range(start, end)
    series: dict[str, pd.Series] = {}
    for code in codes:
        code6 = code.zfill(6)
        symbol = to_std_code(code6)
        df = fetch_ohlcv_jq(symbol, start=start, end=end)
        close = df["Close"].copy()
        close.index = pd.to_datetime(close.index).normalize()
        series[code6] = close
        print(f"  ✓ {code6}  {len(close)} 条 K 线  [{start} ~ {end}]")
    panel = pd.DataFrame(series).sort_index()
    return panel.dropna(how="all")


def load_close_panel(codes: list[str], start: str, end: str, source: str = "auto") -> pd.DataFrame:
    if source in ("auto", "jq"):
        try:
            print("数据源: JQData")
            return load_close_panel_jq(codes, start, end)
        except Exception as exc:
            if source == "jq":
                raise
            print(f"JQData 不可用 ({exc})，回退 AkShare …")
    print("数据源: AkShare")
    hist_start = (pd.Timestamp(start) - pd.DateOffset(days=60)).strftime("%Y%m%d")
    series: dict[str, pd.Series] = {}
    for code in codes:
        code6 = code.zfill(6)
        code_std = to_std_code(code6)
        px = _fetch_hist_price(code6, code_std, hist_start)
        close = px["close"].copy()
        close.index = pd.to_datetime(close.index).normalize()
        series[code6] = close
        print(f"  ✓ {code6}  {len(close)} 条 K 线")
    panel = pd.DataFrame(series).sort_index()
    panel = panel.loc[(panel.index >= pd.Timestamp(start)) & (panel.index <= pd.Timestamp(end))]
    return panel.dropna(how="all")


def load_benchmark_jq(start: str, end: str) -> pd.Series:
    """沪深300 指数（与果仁 reference=000300 对齐）。"""
    _ensure_auth()
    start, end = _clamp_date_range(start, end)
    import jqdatasdk as jq

    raw = jq.get_price(
        "000300.XSHG",
        start_date=start,
        end_date=end,
        frequency="daily",
        fields=["close"],
        skip_paused=False,
        fq="pre",
    )
    close = raw["close"].copy()
    close.index = pd.to_datetime(close.index).normalize()
    print(f"  ✓ 000300 指数  {len(close)} 条 K 线  [{start} ~ {end}]")
    return close


def calc_metrics(equity: pd.Series, benchmark: pd.Series) -> dict[str, float | int]:
    ret = equity.pct_change().fillna(0)
    bench_ret = benchmark.pct_change().fillna(0)
    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    bench_total = benchmark.iloc[-1] / benchmark.iloc[0] - 1
    years = len(equity) / 252
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0
    bench_annual = (1 + bench_total) ** (1 / years) - 1 if years > 0 else 0.0
    rolling_max = equity.cummax()
    max_drawdown = ((equity - rolling_max) / rolling_max).min()
    vol = ret.std() * np.sqrt(252)
    sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0.0
    excess = ret - bench_ret
    excess_std = excess.std() * np.sqrt(252)
    info_ratio = excess.mean() / excess.std() * np.sqrt(252) if excess.std() > 0 else 0.0
    return {
        "days": len(equity),
        "total_return": total_return,
        "annual_return": annual_return,
        "benchmark_total": bench_total,
        "benchmark_annual": bench_annual,
        "excess_total": total_return - bench_total,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "volatility": vol,
        "info_ratio": info_ratio,
        "excess_std": excess_std,
    }


def is_limit_down(day_ret: float) -> bool:
    return day_ret <= -0.095


def run_simulation(cfg: EtfMomentumConfig, closes: pd.DataFrame) -> tuple[pd.Series, list[dict], list[dict]]:
    trade_days = closes.index.tolist()
    rotation_pool = [c for c in cfg.universe if c != BACKUP_ETF]
    if BACKUP_ETF not in closes.columns:
        raise ValueError(f"缺少备用 ETF {BACKUP_ETF}")

    mom = closes.pct_change(cfg.mom_window)
    daily_ret = closes.pct_change()

    cash = cfg.initial_cash
    holdings: dict[str, dict] = {}  # code -> {shares, cost}
    backup_shares = 0.0
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    trades: list[dict] = []
    period_rows: list[dict] = []
    period_idx = 0
    last_rebalance_i = -cfg.period

    def mark_to_market(day: pd.Timestamp) -> float:
        total = cash
        for code, pos in holdings.items():
            px = closes.at[day, code]
            if pd.notna(px):
                total += pos["shares"] * px
        bpx = closes.at[day, BACKUP_ETF]
        if pd.notna(bpx):
            total += backup_shares * bpx
        return total

    def sell(code: str, day: pd.Timestamp, reason: str) -> None:
        nonlocal cash
        pos = holdings.pop(code)
        px = closes.at[day, code]
        proceeds = pos["shares"] * px * (1 - cfg.trade_cost)
        ret = px / pos["cost"] - 1
        cash += proceeds
        trades.append(
            {
                "action": "sell",
                "code": code,
                "day": day,
                "price": px,
                "return": ret,
                "reason": reason,
            }
        )

    def buy(code: str, day: pd.Timestamp, target_value: float) -> None:
        nonlocal cash, backup_shares
        if target_value <= 0:
            return
        # 先释放黄金仓位，否则 cash 常为 0 导致永远无法买入
        if backup_shares > 0:
            bpx = closes.at[day, BACKUP_ETF]
            if pd.notna(bpx) and bpx > 0:
                cash += backup_shares * bpx * (1 - cfg.trade_cost)
                backup_shares = 0.0
        if cash <= 0:
            return
        px = closes.at[day, code]
        if pd.isna(px) or px <= 0:
            return
        spend = min(target_value, cash / (1 + cfg.trade_cost))
        shares = spend * (1 - cfg.trade_cost) / px
        cash -= spend
        if code in holdings:
            old = holdings[code]
            total_shares = old["shares"] + shares
            avg_cost = (old["cost"] * old["shares"] + px * shares) / total_shares
            holdings[code] = {"shares": total_shares, "cost": avg_cost}
        else:
            holdings[code] = {"shares": shares, "cost": px}
        trades.append({"action": "buy", "code": code, "day": day, "price": px, "reason": "rebalance"})

    def deploy_backup(day: pd.Timestamp) -> None:
        nonlocal cash, backup_shares
        if cash <= 0:
            return
        bpx = closes.at[day, BACKUP_ETF]
        if pd.isna(bpx) or bpx <= 0:
            return
        backup_shares += cash * (1 - cfg.trade_cost) / bpx
        cash = 0.0

    for i, day in enumerate(trade_days):
        ranked = []
        for code in rotation_pool:
            m = mom.at[day, code]
            if pd.notna(m) and m > 0:
                ranked.append((code, m))
        ranked.sort(key=lambda x: x[1], reverse=True)
        rank_map = {code: idx + 1 for idx, (code, _) in enumerate(ranked)}

        # --- 每日检查卖出 ---
        for code in list(holdings.keys()):
            px = closes.at[day, code]
            if pd.isna(px):
                continue
            rank = rank_map.get(code, 9999)
            gain = px / holdings[code]["cost"] - 1
            if rank >= cfg.sell_rank:
                sell(code, day, f"rank>={cfg.sell_rank}")
            elif gain >= cfg.take_profit:
                sell(code, day, f"take_profit>={cfg.take_profit:.0%}")
            elif gain <= -cfg.stop_loss:
                sell(code, day, f"stop_loss<={-cfg.stop_loss:.0%}")

        is_rebalance = (i - last_rebalance_i) >= cfg.period or i == 0
        if is_rebalance:
            last_rebalance_i = i
            period_idx += 1
            period_start_equity = mark_to_market(day)

            # --- 调仓日买入 ---
            buy_candidates = []
            for code, _ in ranked:
                if rank_map[code] > cfg.buy_rank:
                    continue
                dr = daily_ret.at[day, code]
                if pd.notna(dr) and is_limit_down(dr):
                    continue
                buy_candidates.append(code)
                if len(buy_candidates) >= cfg.max_count:
                    break

            # 果仁：ideal_position=0.5 + ideal_count=2 → 总目标约 50%，每只约 25%
            per_weight = min(cfg.ideal_position / max(cfg.ideal_count, 1), cfg.position_limit)
            equity_now = mark_to_market(day)
            target_codes = set(buy_candidates[: cfg.ideal_count])

            # 卖掉不在目标池的
            for code in list(holdings.keys()):
                if code not in target_codes:
                    sell(code, day, "rebalance_out")

            for code in target_codes:
                if code not in holdings:
                    buy(code, day, equity_now * per_weight)

            # 闲钱进黄金
            deploy_backup(day)

            period_end_equity = mark_to_market(day)
            stock_value = sum(
                holdings[c]["shares"] * closes.at[day, c]
                for c in holdings
                if pd.notna(closes.at[day, c])
            )
            period_rows.append(
                {
                    "period": period_idx,
                    "start": day,
                    "end": day,
                    "holding_position": stock_value / period_end_equity if period_end_equity else 0,
                    "stock_count": len(holdings),
                    "period_return": period_end_equity / period_start_equity - 1 if period_start_equity else 0,
                    "equity": period_end_equity,
                }
            )

        # 日末：剩余现金进黄金
        if cash > 0:
            deploy_backup(day)

        equity_rows.append((day, mark_to_market(day)))

    equity = pd.Series({d: v for d, v in equity_rows}).sort_index()
    return equity, trades, period_rows


def yearly_table(equity: pd.Series, benchmark: pd.Series) -> pd.DataFrame:
    rows = []
    for year, grp in equity.groupby(equity.index.year):
        bgrp = benchmark.loc[benchmark.index.year == year]
        if len(grp) < 2 or len(bgrp) < 2:
            continue
        rows.append(
            {
                "year": year,
                "strategy": grp.iloc[-1] / grp.iloc[0] - 1,
                "benchmark": bgrp.iloc[-1] / bgrp.iloc[0] - 1,
            }
        )
    return pd.DataFrame(rows)


def print_report(cfg: EtfMomentumConfig, metrics: dict, trades: list[dict], yearly: pd.DataFrame) -> None:
    sells = [t for t in trades if t["action"] == "sell"]
    wins = [t for t in sells if t.get("return", 0) > 0]

    print("\n" + "=" * 72)
    print("本地 ETF 动量轮动回测（JQData / AkShare 前复权收盘价）")
    print("=" * 72)
    print(f"区间: {cfg.start} ~ {cfg.end}")
    print(f"轮动池: {', '.join(cfg.universe)}")
    print(f"调仓周期: {cfg.period} 日 | 买≤{cfg.buy_rank} 卖≥{cfg.sell_rank} | 止盈{cfg.take_profit:.0%} 止损{cfg.stop_loss:.0%}")
    print("-" * 72)
    print(f"{'指标':<16} {'本策略':>12} {'基准(000300)':>14}")
    print(f"{'总收益':<16} {metrics['total_return']:>11.2%} {metrics['benchmark_total']:>14.2%}")
    print(f"{'年化收益':<16} {metrics['annual_return']:>11.2%} {metrics['benchmark_annual']:>14.2%}")
    print(f"{'超额总收益':<16} {metrics['excess_total']:>11.2%}")
    print(f"{'夏普比率':<16} {metrics['sharpe_ratio']:>12.2f}")
    print(f"{'最大回撤':<16} {metrics['max_drawdown']:>11.2%}")
    print(f"{'波动率':<16} {metrics['volatility']:>11.2%}")
    print(f"{'信息比率':<16} {metrics['info_ratio']:>12.2f}")
    print("-" * 72)
    print(f"交易次数(卖): {len(sells)} | 胜率: {len(wins)/len(sells):.1%}" if sells else "无卖出交易")
    print("\n年度收益:")
    for _, row in yearly.iterrows():
        print(
            f"  {int(row['year'])}: 策略 {row['strategy']:+.2%} | "
            f"基准 {row['benchmark']:+.2%} | 超额 {row['strategy']-row['benchmark']:+.2%}"
        )
    print("=" * 72)
    print("\n果仁回测对照（你提供的 JSON）:")
    print("  总收益 55.23% | 年化 21.75% | 夏普 1.63 | 最大回撤 14.16%")
    print("  基准总收益 41.89% | 基准年化 16.95%")
    print("  注：本地与果仁可能有池子、调仓细节、价格口径差异。")


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 ETF 动量轮动回测")
    parser.add_argument("--start", default="2025-03-07")
    parser.add_argument("--end", default="2026-06-09")
    parser.add_argument("--period", type=int, default=10)
    parser.add_argument("--source", choices=["auto", "jq", "ak"], default="auto")
    args = parser.parse_args()

    cfg = EtfMomentumConfig(start=args.start, end=args.end, period=args.period)
    all_codes = sorted(set(cfg.universe))

    print("正在下载 ETF 数据 …")
    closes = load_close_panel(all_codes, cfg.start, cfg.end, source=args.source)
    closes = closes.ffill().dropna(how="any")
    if closes.empty:
        raise SystemExit("收盘价面板为空，请检查网络或日期区间。")

    print("\n正在下载基准指数 …")
    try:
        bench_close = load_benchmark_jq(cfg.start, cfg.end)
    except Exception:
        bench_close = closes[BENCHMARK]
        print(f"  回退使用 {BENCHMARK} ETF 作为基准")
    bench_close = bench_close.reindex(closes.index).ffill()

    print(f"\n有效交易日: {len(closes)} 天 ({closes.index[0].date()} ~ {closes.index[-1].date()})")
    equity, trades, _ = run_simulation(cfg, closes)
    bench_equity = cfg.initial_cash * (bench_close / bench_close.iloc[0])
    metrics = calc_metrics(equity, bench_equity)
    yearly = yearly_table(equity, bench_equity)
    print_report(cfg, metrics, trades, yearly)


if __name__ == "__main__":
    main()
