"""聚宽市场数据工具：选股池、排名、批量行情。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.data.fetch_jqdata import _clamp_date_range, _ensure_auth


def _code_filter(code: str, exclude_stib: bool) -> bool:
    """保留 A 股主板/创业板/中小板，排除科创板等。"""
    if not (code.endswith(".XSHG") or code.endswith(".XSHE")):
        return False
    num = code.split(".")[0]
    if exclude_stib and num.startswith("688"):
        return False
    if num.startswith("8") or num.startswith("4"):
        return False
    return True


def fetch_universe_snapshot(
    trade_date: date | str,
    *,
    exclude_st: bool = True,
    exclude_stib: bool = True,
    min_turnover_ratio: float = 0.0,
    min_money: float = 0.0,
    profit_positive: bool = False,
) -> pd.DataFrame:
    """
    获取某日全市场估值快照，并完成基础筛选。

    返回列: code, market_cap, circulating_market_cap, turnover_ratio, score
    """
    import jqdatasdk as jq
    from jqdatasdk import indicator, query, valuation

    _ensure_auth()
    day = pd.Timestamp(trade_date).strftime("%Y-%m-%d")

    q = query(
        valuation.code,
        valuation.market_cap,
        valuation.circulating_market_cap,
        valuation.turnover_ratio,
    ).filter(valuation.market_cap > 0)

    df = jq.get_fundamentals(q, date=day)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.dropna(subset=["market_cap", "circulating_market_cap"])
    df = df[df["code"].map(lambda c: _code_filter(c, exclude_stib))]

    if min_turnover_ratio > 0:
        df = df[df["turnover_ratio"].fillna(0) >= min_turnover_ratio]

    if min_money > 0:
        # market_cap 单位：亿元；turnover_ratio 单位：%
        money_est = df["circulating_market_cap"] * 1e8 * df["turnover_ratio"].fillna(0) / 100
        df = df[money_est >= min_money]

    if profit_positive:
        pq = query(indicator.code, indicator.adjusted_profit).filter(
            indicator.adjusted_profit > 0
        )
        profit_df = jq.get_fundamentals(pq, date=day)
        if profit_df is not None and not profit_df.empty:
            df = df[df["code"].isin(profit_df["code"])]

    if exclude_st and not df.empty:
        st = jq.get_extras(
            "is_st",
            df["code"].tolist(),
            start_date=day,
            end_date=day,
            df=True,
        )
        if st is not None and not st.empty:
            st_flags = st.iloc[0]
            df = df[~df["code"].map(lambda c: bool(st_flags.get(c, False)))]

    return df.reset_index(drop=True)


def rank_universe(
    df: pd.DataFrame,
    *,
    circ_weight: float = 2.0,
    total_weight: float = 1.0,
) -> pd.DataFrame:
    """按流通市值×权重 + 总市值×权重 升序排名（1 = 最小）。"""
    if df.empty:
        return df

    out = df.copy()
    out["score"] = (
        out["circulating_market_cap"] * circ_weight
        + out["market_cap"] * total_weight
    )
    out = out.sort_values("score", ascending=True).reset_index(drop=True)
    out["rank"] = range(1, len(out) + 1)
    return out


def fetch_price_panel(
    codes: list[str],
    start: str,
    end: str,
    *,
    fields: list[str] | None = None,
) -> pd.DataFrame:
    """批量拉取行情，返回 MultiIndex (date, code) 的 DataFrame。"""
    import jqdatasdk as jq

    _ensure_auth()
    if not codes:
        return pd.DataFrame()

    use_fields = fields or ["open", "close", "paused", "high_limit", "low_limit"]
    raw = jq.get_price(
        codes,
        start_date=start,
        end_date=end,
        frequency="daily",
        fields=use_fields,
        skip_paused=False,
        fq="pre",
        panel=False,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()

    raw["time"] = pd.to_datetime(raw["time"])
    return raw.set_index(["time", "code"]).sort_index()


def fetch_index_series(
    index_code: str,
    start: str,
    end: str | None = None,
) -> pd.Series:
    """拉取指数收盘价序列。"""
    import jqdatasdk as jq

    _ensure_auth()
    start, end = _clamp_date_range(start, end)
    raw = jq.get_price(
        index_code,
        start_date=start,
        end_date=end,
        frequency="daily",
        fields=["close"],
        skip_paused=True,
    )
    if raw is None or raw.empty:
        return pd.Series(dtype=float)
    return raw["close"].astype(float)


def get_trade_days(start: str, end: str | None = None) -> list[pd.Timestamp]:
    """获取交易日列表。"""
    import jqdatasdk as jq

    _ensure_auth()
    start, end = _clamp_date_range(start, end)
    days = jq.get_trade_days(start_date=start, end_date=end)
    return [pd.Timestamp(d) for d in days]
