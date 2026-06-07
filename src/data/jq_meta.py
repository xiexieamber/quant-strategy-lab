"""聚宽静态元数据缓存：上市日期、审计意见、退市标记。"""

from __future__ import annotations

import pickle
from pathlib import Path

import pandas as pd

from src.data.fetch_jqdata import PROJECT_ROOT, _ensure_auth
from src.strategies.small_cap.universe_filter import BAD_AUDIT_TYPE_IDS

META_DIR = PROJECT_ROOT / "data" / "processed" / "jq_cache" / "meta"


def load_list_dates(force_refresh: bool = False) -> dict[str, pd.Timestamp]:
    """股票上市日期 {code: Timestamp}。"""
    path = META_DIR / "list_dates.pkl"
    if path.exists() and not force_refresh:
        with path.open("rb") as f:
            return pickle.load(f)

    import jqdatasdk as jq

    _ensure_auth()
    secs = jq.get_all_securities(types=["stock"])
    out = {code: pd.Timestamp(row["start_date"]) for code, row in secs.iterrows()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    return out


def load_delisting_codes(force_refresh: bool = False) -> set[str]:
    """名称含「退」的标的（退市整理近似）。"""
    path = META_DIR / "delisting_codes.pkl"
    if path.exists() and not force_refresh:
        with path.open("rb") as f:
            return pickle.load(f)

    import jqdatasdk as jq

    _ensure_auth()
    secs = jq.get_all_securities(types=["stock"])
    codes = {code for code, row in secs.iterrows() if "退" in str(row.get("display_name", ""))}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(codes, f, protocol=pickle.HIGHEST_PROTOCOL)
    return codes


def load_bad_audit_codes(force_refresh: bool = False) -> set[str]:
    """最新年报审计意见为保留/无法表示/否定的股票代码。"""
    path = META_DIR / "bad_audit_codes.pkl"
    if path.exists() and not force_refresh:
        with path.open("rb") as f:
            return pickle.load(f)

    import jqdatasdk as jq
    from jqdatasdk import finance, query

    _ensure_auth()
    q = query(
        finance.STK_AUDIT_OPINION.code,
        finance.STK_AUDIT_OPINION.opinion_type_id,
        finance.STK_AUDIT_OPINION.pub_date,
        finance.STK_AUDIT_OPINION.report_type,
    ).filter(finance.STK_AUDIT_OPINION.report_type == 0)
    df = finance.run_query(q)
    if df is None or df.empty:
        return set()

    df = df[df["opinion_type_id"].isin(BAD_AUDIT_TYPE_IDS)]
    df = df.sort_values("pub_date").drop_duplicates("code", keep="last")
    codes = set(df["code"].tolist())

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(codes, f, protocol=pickle.HIGHEST_PROTOCOL)
    return codes


def build_avg_money_maps(
    prices: pd.DataFrame,
    trade_days: list[pd.Timestamp],
    window: int = 5,
) -> dict[pd.Timestamp, dict[str, float]]:
    """
    从含 money 列的 (date, code) 面板计算每个交易日、每只股票过去 window 日平均成交额。
    """
    if prices.empty or "money" not in prices.columns:
        return {}

    raw = prices.reset_index()
    raw["time"] = pd.to_datetime(raw["time"])
    result: dict[pd.Timestamp, dict[str, float]] = {}

    for day in trade_days:
        code_avgs: dict[str, float] = {}
        hist_start = day - pd.Timedelta(days=window * 3)
        sub = raw[(raw["time"] <= day) & (raw["time"] >= hist_start)]
        for code, grp in sub.groupby("code"):
            tail = grp.sort_values("time").tail(window)
            if len(tail) >= max(1, window // 2):
                code_avgs[code] = float(tail["money"].mean())
        result[day] = code_avgs
    return result


def load_paused_codes_for_day(codes: list[str], day: str) -> set[str]:
    """当日停牌代码集合。"""
    import jqdatasdk as jq

    _ensure_auth()
    if not codes:
        return set()
    raw = jq.get_price(
        codes,
        start_date=day,
        end_date=day,
        frequency="daily",
        fields=["paused"],
        skip_paused=False,
    )
    if raw is None or raw.empty:
        return set()
    if "code" in raw.columns:
        return set(raw.loc[raw["paused"] == 1, "code"])
    return set()
