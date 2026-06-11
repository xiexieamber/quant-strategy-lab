#!/usr/bin/env python3
"""Diagnose the small-cap strategy universe and execution funnel."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.ak_cache import prepare_akshare_data
from src.data.ak_market import get_trade_days
from src.data.jq_market import rank_universe
from src.research.experiment import load_json_config, small_cap_config_from_dict
from src.strategies.small_cap.universe_filter import apply_universe_filter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="诊断小市值策略过滤漏斗")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "experiments" / "small_cap_h1_pro_baseline.json",
    )
    parser.add_argument("--days", type=int, default=20, help="展示最近 N 个交易日")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw = load_json_config(args.config)
    cfg = small_cap_config_from_dict(raw)
    cfg.progress_callback = _progress
    start = pd.Timestamp(cfg.start).strftime("%Y-%m-%d")
    end = (
        pd.Timestamp(cfg.end).strftime("%Y-%m-%d")
        if cfg.end
        else pd.Timestamp.today().strftime("%Y-%m-%d")
    )

    trade_days = get_trade_days(start, end)
    data = prepare_akshare_data(trade_days, start, end, cfg)

    rows: list[dict] = []
    for i, day in enumerate(trade_days[-args.days :]):
        day_idx = trade_days.index(day)
        raw_uni = data.valuation_store.get(day, pd.DataFrame())
        paused = set()
        if not raw_uni.empty:
            for code in raw_uni["code"].tolist():
                try:
                    row = data.prices.loc[(day, code)]
                    if bool(row.get("paused", 0)):
                        paused.add(code)
                except KeyError:
                    continue

        filtered = apply_universe_filter(
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
            filtered,
            circ_weight=cfg.circ_weight,
            total_weight=cfg.total_weight,
        )
        buy_candidates = (
            ranked[ranked["rank"] <= cfg.buy_rank]["code"].tolist()
            if not ranked.empty
            else []
        )
        priced_buy = 0
        for code in buy_candidates:
            try:
                px = float(data.prices.loc[(day, code)][cfg.rebalance_price])
                if px > 0:
                    priced_buy += 1
            except (KeyError, TypeError, ValueError):
                continue

        etf_price = None
        if cfg.backup_etf:
            try:
                etf_price = float(data.prices.loc[(day, cfg.backup_etf)][cfg.rebalance_price])
            except (KeyError, TypeError, ValueError):
                etf_price = None

        index_safe = True
        if cfg.use_index_timing and not data.index_close.empty:
            ref_idx = max(0, day_idx - cfg.index_timing_lag)
            ref_day = trade_days[ref_idx]
            hist = data.index_close.loc[:ref_day].dropna()
            if len(hist) >= cfg.index_ma:
                index_safe = float(hist.iloc[-1]) >= float(hist.tail(cfg.index_ma).mean())

        rows.append(
            {
                "date": day.strftime("%Y-%m-%d"),
                "raw": len(raw_uni),
                "filtered": len(filtered),
                "ranked": len(ranked),
                "buy_candidates": len(buy_candidates),
                "priced_buy": priced_buy,
                "seasonal_empty": day.month in cfg.empty_months,
                "index_safe": index_safe,
                "force_empty": day.month in cfg.empty_months or not index_safe,
                "etf_price": etf_price,
            }
        )

    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    print("\nSummary:")
    print(f"valuation days: {len(data.valuation_store)}/{len(trade_days)}")
    print(f"price rows: {len(data.prices)}")
    print(f"profit ok codes: {len(data.profit_ok_codes)}")
    print(f"list dates: {len(data.list_dates)}")
    print(f"avg money days: {len(data.avg_money_maps)}")
    print(f"index close rows: {len(data.index_close)}")


def _progress(cur: int, total: int, day: pd.Timestamp, phase: str) -> None:
    if total <= 0:
        return
    step = 50 if total <= 1000 else 250
    if cur in (0, 1, total) or cur % step == 0:
        pct = cur / total * 100
        day_s = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)
        print(f"[{phase}] {cur}/{total} ({pct:.1f}%) {day_s}", flush=True)


if __name__ == "__main__":
    main()
