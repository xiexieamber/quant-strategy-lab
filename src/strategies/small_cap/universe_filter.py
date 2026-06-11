"""H1-Pro 标的池过滤（说明书第二节）。"""

from __future__ import annotations

import pandas as pd

from src.data.jq_market import _code_filter
from src.strategies.small_cap.config import SmallCapConfig

# 聚宽 audit opinion_type_id：2=保留 3=无法表示 4=否定
BAD_AUDIT_TYPE_IDS = {2, 3, 4}


def apply_universe_filter(
    df: pd.DataFrame,
    day: pd.Timestamp,
    cfg: SmallCapConfig,
    *,
    st_flags: pd.DataFrame,
    profit_codes: set[str] | None = None,
    list_dates: dict[str, pd.Timestamp] | None = None,
    bad_audit_codes: set[str] | None = None,
    delisting_codes: set[str] | None = None,
    avg_money_map: dict[str, float] | None = None,
    paused_codes: set[str] | None = None,
) -> pd.DataFrame:
    """对单日估值快照应用全部过滤规则，返回合法 Universe。"""
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.dropna(subset=["market_cap", "circulating_market_cap"]).copy()

    def _keep_code(code: str) -> bool:
        if not _code_filter(code, cfg.exclude_stib):
            return False
        num = code.split(".")[0]
        if cfg.exclude_bse and num.startswith(("8", "4", "9")):
            return False
        return True

    out = out[out["code"].map(_keep_code)]

    if paused_codes:
        out = out[~out["code"].isin(paused_codes)]

    if cfg.exclude_delisting and delisting_codes:
        out = out[~out["code"].isin(delisting_codes)]

    if cfg.min_listed_days > 0 and list_dates:
        keep = []
        for code in out["code"]:
            ld = list_dates.get(code)
            if ld is None:
                keep.append(code)
                continue
            if (day - pd.Timestamp(ld)).days >= cfg.min_listed_days:
                keep.append(code)
        out = out[out["code"].isin(keep)]

    if cfg.min_turnover_ratio > 0:
        out = out[out["turnover_ratio"].fillna(0) >= cfg.min_turnover_ratio]

    if cfg.min_money > 0:
        if "money" in out.columns and out["money"].fillna(0).gt(0).any():
            out = out[out["money"].fillna(0) >= cfg.min_money]
        else:
            est = out["circulating_market_cap"] * 1e8 * out["turnover_ratio"].fillna(0) / 100
            out = out[est >= cfg.min_money]

    if cfg.min_avg_money > 0 and cfg.min_avg_money_days > 0 and avg_money_map:
        out = out[
            out["code"].map(lambda c: avg_money_map.get(c, 0.0) >= cfg.min_avg_money)
        ]

    if cfg.profit_positive and profit_codes is not None:
        out = out[out["code"].isin(profit_codes)]

    if cfg.exclude_bad_audit and bad_audit_codes:
        out = out[~out["code"].isin(bad_audit_codes)]

    if cfg.exclude_st and not out.empty and not st_flags.empty:
        day_key = day if day in st_flags.index else st_flags.index[st_flags.index <= day].max()
        if pd.notna(day_key):
            row = st_flags.loc[day_key]
            out = out[~out["code"].map(lambda c: bool(row.get(c, False)))]

    return out.reset_index(drop=True)


def is_limit_down_open(row: pd.Series, px: float) -> bool:
    """开盘价一字跌停 → 无法卖出。"""
    low = float(row.get("low_limit", 0) or 0)
    if low <= 0:
        return False
    return px <= low * 1.001


def is_limit_up_open(row: pd.Series, px: float) -> bool:
    """开盘价一字涨停 → 无法买入。"""
    high = float(row.get("high_limit", 0) or 0)
    if high <= 0:
        return False
    return px >= high * 0.999
