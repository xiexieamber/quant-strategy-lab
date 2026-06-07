"""
小市值轮动回测引擎（多标的、日频调仓）。

对应果仁 H1：流通市值权重 2 + 总市值权重 1，模型 II 买卖阈值，开盘价调仓。
本地额外支持：1/4 月空仓、中证1000 均线择时、止损、成交额/净利润筛选（无需 VIP）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.data.fetch_jqdata import _clamp_date_range
from src.data.jq_market import (
    fetch_index_series,
    fetch_price_panel,
    fetch_universe_snapshot,
    get_trade_days,
    rank_universe,
)
from src.strategies.small_cap.config import SmallCapConfig


@dataclass
class SmallCapBacktestResult:
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe_ratio: float
    equity_curve: pd.Series
    benchmark_curve: pd.Series
    yearly_returns: pd.DataFrame
    holdings_log: pd.DataFrame
    trade_count: int
    start: str
    end: str


def _price_field(cfg: SmallCapConfig) -> str:
    return "open" if cfg.rebalance_price == "open" else "close"


def _compute_metrics(equity: pd.Series) -> dict[str, float]:
    if equity.empty or len(equity) < 2:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
        }

    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    n_days = len(equity)
    years = n_days / 252
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0

    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = float(drawdown.min())

    daily_ret = equity.pct_change().fillna(0)
    daily_std = daily_ret.std()
    sharpe = (
        float(daily_ret.mean() / daily_std * np.sqrt(252))
        if daily_std > 0
        else 0.0
    )
    return {
        "total_return": float(total_return),
        "annual_return": float(annual_return),
        "max_drawdown": float(max_drawdown),
        "sharpe_ratio": sharpe,
    }


def _yearly_table(equity: pd.Series) -> pd.DataFrame:
    if equity.empty:
        return pd.DataFrame(columns=["year", "return"])

    df = equity.to_frame("equity")
    df["year"] = df.index.year
    rows = []
    for year, grp in df.groupby("year"):
        ret = grp["equity"].iloc[-1] / grp["equity"].iloc[0] - 1
        rows.append({"year": int(year), "return": float(ret)})
    return pd.DataFrame(rows)


def _index_timing_ok(
    index_close: pd.Series,
    day: pd.Timestamp,
    ma_window: int,
) -> bool:
    hist = index_close.loc[:day].dropna()
    if len(hist) < ma_window:
        return True
    ma = hist.tail(ma_window).mean()
    return float(hist.iloc[-1]) >= float(ma)


def _holdings_value(
    holdings: dict[str, dict],
    prices: pd.DataFrame,
    day: pd.Timestamp,
    pf: str,
) -> float:
    total = 0.0
    for code, pos in holdings.items():
        try:
            px = float(prices.loc[(day, code)][pf])
            total += pos["shares"] * px
        except (KeyError, TypeError):
            continue
    return total


def run_small_cap_backtest(cfg: SmallCapConfig) -> SmallCapBacktestResult:
    """运行小市值策略回测。"""
    start, end = _clamp_date_range(cfg.start, cfg.end)
    trade_days = get_trade_days(start, end)
    if len(trade_days) < 2:
        raise ValueError(f"交易日不足（{start} ~ {end}），请检查 JQData 账号数据范围。")

    pf = _price_field(cfg)

    index_close = pd.Series(dtype=float)
    if cfg.use_index_timing:
        index_close = fetch_index_series(cfg.index_code, start, end)

    cash = cfg.initial_cash
    holdings: dict[str, dict] = {}
    etf_shares = 0.0
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    holdings_rows: list[dict] = []
    trade_count = 0

    total_days = len(trade_days)
    for i, day in enumerate(trade_days):
        if cfg.progress_callback:
            cfg.progress_callback(i + 1, total_days, day)

        day_str = day.strftime("%Y-%m-%d")
        month = day.month
        seasonal_empty = month in cfg.empty_months
        timing_empty = (
            cfg.use_index_timing
            and not index_close.empty
            and not _index_timing_ok(index_close, day, cfg.index_ma)
        )
        force_empty = seasonal_empty or timing_empty

        universe = fetch_universe_snapshot(
            day_str,
            exclude_st=cfg.exclude_st,
            exclude_stib=cfg.exclude_stib,
            min_turnover_ratio=cfg.min_turnover_ratio,
            min_money=cfg.min_money,
            profit_positive=cfg.profit_positive,
        )
        ranked = rank_universe(
            universe,
            circ_weight=cfg.circ_weight,
            total_weight=cfg.total_weight,
        )
        rank_map = dict(zip(ranked["code"], ranked["rank"])) if not ranked.empty else {}

        day_codes = set(holdings.keys())
        if not ranked.empty:
            day_codes.update(ranked.head(max(cfg.sell_rank + 5, cfg.max_positions * 2))["code"])
        if cfg.backup_etf:
            day_codes.add(cfg.backup_etf)

        prices = fetch_price_panel(
            list(day_codes),
            day_str,
            day_str,
            fields=[pf, "paused", "high_limit", "low_limit"],
        )
        if prices.empty:
            equity_rows.append((day, cash))
            continue

        def _px(code: str) -> float | None:
            try:
                row = prices.loc[(day, code)]
                if bool(row.get("paused", 0)):
                    return None
                val = float(row[pf])
                return val if val > 0 else None
            except KeyError:
                return None

        for code in list(holdings.keys()):
            rank = rank_map.get(code, 9999)
            pos = holdings[code]
            px = _px(code)
            if px is None:
                continue

            loss_pct = (px - pos["cost"]) / pos["cost"] if pos["cost"] > 0 else 0
            should_sell = (
                force_empty
                or rank >= cfg.sell_rank
                or (cfg.stop_loss_pct > 0 and loss_pct <= -cfg.stop_loss_pct)
            )
            if should_sell:
                cash += pos["shares"] * px * (1 - cfg.trade_cost)
                trade_count += 1
                del holdings[code]

        if force_empty and cfg.backup_etf:
            etf_px = _px(cfg.backup_etf)
            if etf_px and cash > 0:
                etf_shares += cash * (1 - cfg.trade_cost) / etf_px
                cash = 0.0
                trade_count += 1
        elif etf_shares > 0 and not force_empty:
            etf_px = _px(cfg.backup_etf)
            if etf_px:
                cash += etf_shares * etf_px * (1 - cfg.trade_cost)
                etf_shares = 0.0
                trade_count += 1

        if not force_empty and not ranked.empty:
            slots = cfg.max_positions - len(holdings)
            if slots > 0:
                candidates = ranked[ranked["rank"] <= cfg.buy_rank]["code"].tolist()
                port_value = cash + etf_shares * (_px(cfg.backup_etf) or 0) + _holdings_value(
                    holdings, prices, day, pf
                )
                target_value = port_value / cfg.max_positions

                for code in candidates:
                    if slots <= 0:
                        break
                    if code in holdings:
                        continue
                    px = _px(code)
                    if px is None:
                        continue
                    try:
                        row = prices.loc[(day, code)]
                        low_limit = float(row.get("low_limit", 0) or 0)
                        if low_limit > 0 and px <= low_limit * 1.001:
                            continue
                    except KeyError:
                        pass

                    buy_cash = min(cash, target_value)
                    if buy_cash < target_value * 0.5:
                        continue
                    shares = buy_cash * (1 - cfg.trade_cost) / px
                    if shares <= 0:
                        continue
                    holdings[code] = {"shares": shares, "cost": px}
                    cash -= buy_cash
                    slots -= 1
                    trade_count += 1

        total_equity = (
            cash
            + etf_shares * (_px(cfg.backup_etf) or 0)
            + _holdings_value(holdings, prices, day, pf)
        )
        equity_rows.append((day, total_equity))
        holdings_rows.append(
            {
                "date": day,
                "stocks": ",".join(sorted(holdings.keys())),
                "n_stocks": len(holdings),
                "etf_shares": etf_shares,
                "cash": cash,
                "equity": total_equity,
            }
        )

    equity = pd.Series({d: v for d, v in equity_rows}, name="equity").sort_index()

    bench = fetch_index_series("000852.XSHG", start, end)
    if not bench.empty:
        bench = bench.reindex(equity.index).ffill()
        bench = cfg.initial_cash * (bench / bench.iloc[0])
    else:
        bench = pd.Series(cfg.initial_cash, index=equity.index, name="benchmark")

    metrics = _compute_metrics(equity)
    return SmallCapBacktestResult(
        total_return=metrics["total_return"],
        annual_return=metrics["annual_return"],
        max_drawdown=metrics["max_drawdown"],
        sharpe_ratio=metrics["sharpe_ratio"],
        equity_curve=equity,
        benchmark_curve=bench,
        yearly_returns=_yearly_table(equity),
        holdings_log=pd.DataFrame(holdings_rows),
        trade_count=trade_count,
        start=start,
        end=end,
    )
