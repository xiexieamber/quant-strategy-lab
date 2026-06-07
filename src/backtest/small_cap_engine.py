"""
小市值轮动回测引擎（H1 / H1-Pro 防雷增强版）。

H1-Pro 对齐《策略说明书》：六类过滤、昨日 MA20 择时、涨跌停撮合、0.3% 单边摩擦。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.backtest.backtest_data import BacktestData
from src.data.fetch_jqdata import _clamp_date_range
from src.data.jq_cache import QuotaExhaustedError, load_jq_backtest_data
from src.data.jq_market import fetch_index_series, get_trade_days, rank_universe
from src.strategies.small_cap.config import SmallCapConfig
from src.strategies.small_cap.universe_filter import (
    apply_universe_filter,
    is_limit_down_open,
    is_limit_up_open,
)


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


def _index_timing_safe(
    index_close: pd.Series,
    trade_days: list[pd.Timestamp],
    day_idx: int,
    cfg: SmallCapConfig,
) -> bool:
    """True=可持股；False=触发强制空仓。Pro 用「昨日」收盘 vs MA20。"""
    if index_close.empty or not cfg.use_index_timing:
        return True

    lag = cfg.index_timing_lag if cfg.h1_pro else 0
    ref_idx = max(0, day_idx - lag)
    ref_day = trade_days[ref_idx]

    hist = index_close.loc[:ref_day].dropna()
    if len(hist) < cfg.index_ma:
        return True
    ma = hist.tail(cfg.index_ma).mean()
    return float(hist.iloc[-1]) >= float(ma)


def _paused_codes(prices: pd.DataFrame, day: pd.Timestamp, codes: list[str]) -> set[str]:
    paused: set[str] = set()
    for code in codes:
        try:
            if bool(prices.loc[(day, code)].get("paused", 0)):
                paused.add(code)
        except KeyError:
            continue
    return paused


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


def _simulate(
    cfg: SmallCapConfig,
    data: BacktestData,
    trade_days: list[pd.Timestamp],
    pf: str,
) -> tuple[list, list, int]:
    cash = cfg.initial_cash
    holdings: dict[str, dict] = {}
    etf_shares = 0.0
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    holdings_rows: list[dict] = []
    trade_count = 0

    for i, day in enumerate(trade_days):
        if cfg.progress_callback:
            cfg.progress_callback(i + 1, len(trade_days), day, "模拟交易")

        seasonal_empty = day.month in cfg.empty_months
        timing_unsafe = not _index_timing_safe(data.index_close, trade_days, i, cfg)
        force_empty = seasonal_empty or timing_unsafe

        raw_uni = data.valuation_store.get(day, pd.DataFrame())
        paused = _paused_codes(
            data.prices, day, raw_uni["code"].tolist() if not raw_uni.empty else []
        )

        universe = apply_universe_filter(
            raw_uni,
            day,
            cfg,
            st_flags=data.st_flags,
            profit_codes=data.profit_store.get(day) if cfg.profit_positive else None,
            list_dates=data.list_dates or None,
            bad_audit_codes=data.bad_audit_codes or None,
            delisting_codes=data.delisting_codes or None,
            avg_money_map=data.avg_money_maps.get(day),
            paused_codes=paused,
        )
        ranked = rank_universe(
            universe,
            circ_weight=cfg.circ_weight,
            total_weight=cfg.total_weight,
        )
        rank_map = dict(zip(ranked["code"], ranked["rank"])) if not ranked.empty else {}

        def _bar(code: str) -> pd.Series | None:
            try:
                return data.prices.loc[(day, code)]
            except KeyError:
                return None

        def _px(code: str) -> float | None:
            row = _bar(code)
            if row is None:
                return None
            if bool(row.get("paused", 0)):
                return None
            val = float(row[pf])
            return val if val > 0 else None

        # --- 卖出股票（强制空仓时也要卖）---
        for code in list(holdings.keys()):
            pos = holdings[code]
            px = _px(code)
            if px is None:
                continue

            rank = rank_map.get(code, 9999)
            loss_pct = (px - pos["cost"]) / pos["cost"] if pos["cost"] > 0 else 0
            should_sell = (
                force_empty
                or rank >= cfg.sell_rank
                or (cfg.stop_loss_pct > 0 and loss_pct <= -cfg.stop_loss_pct)
            )

            if should_sell and cfg.enforce_limit_prices and not force_empty:
                row = _bar(code)
                if row is not None and is_limit_down_open(row, px):
                    should_sell = False

            if should_sell:
                cash += pos["shares"] * px * (1 - cfg.trade_cost)
                trade_count += 1
                del holdings[code]

        # --- 黄金 ETF：空仓期买入 / 正常期卖出 ---
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

        # --- 买入 ---
        if not force_empty and not ranked.empty:
            slots = cfg.max_positions - len(holdings)
            if slots > 0:
                candidates = ranked[ranked["rank"] <= cfg.buy_rank]["code"].tolist()
                port_value = (
                    cash
                    + etf_shares * (_px(cfg.backup_etf) or 0)
                    + _holdings_value(holdings, data.prices, day, pf)
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

                    row = _bar(code)
                    if row is not None and cfg.enforce_limit_prices:
                        if is_limit_up_open(row, px):
                            continue
                        if is_limit_down_open(row, px):
                            continue

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
            + _holdings_value(holdings, data.prices, day, pf)
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
                "force_empty": force_empty,
            }
        )

    return equity_rows, holdings_rows, trade_count


def run_small_cap_backtest(cfg: SmallCapConfig) -> SmallCapBacktestResult:
    """运行小市值策略回测。"""
    use_ak = cfg.data_source.lower() in ("ak", "akshare")
    pf = _price_field(cfg)

    if use_ak:
        from src.data.ak_cache import fetch_index_series as ak_fetch_index
        from src.data.ak_cache import prepare_akshare_data
        from src.data.ak_market import get_trade_days as ak_get_trade_days

        start = pd.Timestamp(cfg.start).strftime("%Y-%m-%d")
        end = (
            pd.Timestamp(cfg.end).strftime("%Y-%m-%d")
            if cfg.end
            else pd.Timestamp.today().strftime("%Y-%m-%d")
        )
        trade_days = ak_get_trade_days(start, end)
        if len(trade_days) < 2:
            raise ValueError(f"交易日不足（{start} ~ {end}）。")

        data = prepare_akshare_data(trade_days, start, end, cfg)
        fetch_bench = ak_fetch_index
    else:
        start, end = _clamp_date_range(cfg.start, cfg.end)
        trade_days = get_trade_days(start, end)
        if len(trade_days) < 2:
            raise ValueError(
                f"交易日不足（{start} ~ {end}），请检查 JQData 账号数据范围。"
            )
        data = load_jq_backtest_data(cfg, trade_days, start, end, pf)
        fetch_bench = fetch_index_series

    equity_rows, holdings_rows, trade_count = _simulate(cfg, data, trade_days, pf)

    equity = pd.Series({d: v for d, v in equity_rows}, name="equity").sort_index()

    bench = fetch_bench("000852.XSHG", start, end)
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
