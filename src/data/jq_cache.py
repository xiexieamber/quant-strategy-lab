"""聚宽数据本地缓存 + 批量拉取，避免逐日 API 耗尽每日 100 万条额度。"""

from __future__ import annotations

import pickle
from pathlib import Path

import pandas as pd

from src.data.fetch_jqdata import PROJECT_ROOT, _ensure_auth

CACHE_DIR = PROJECT_ROOT / "data" / "processed" / "jq_cache"
# 试用账号每日约 100 万条；全市场约 4500 只 × N 天
EST_STOCKS_PER_DAY = 4500
SAFE_ROW_BUDGET = 850_000


class QuotaExhaustedError(RuntimeError):
    """今日 JQData 查询额度不足，但已部分写入本地缓存。"""

    def __init__(self, message: str, cached_days: int, total_days: int):
        super().__init__(message)
        self.cached_days = cached_days
        self.total_days = total_days


def get_jq_spare_quota() -> int:
    import jqdatasdk as jq

    _ensure_auth()
    info = jq.get_query_count()
    return int(info.get("spare", 0))


def _valuation_day_path(day: str) -> Path:
    return CACHE_DIR / "valuation" / f"{day.replace('-', '')}.pkl"


def _profit_day_path(day: str) -> Path:
    return CACHE_DIR / "profit" / f"{day.replace('-', '')}.pkl"


def _load_cached_day(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    with path.open("rb") as f:
        return pickle.load(f)


def _save_cached_day(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(df, f, protocol=pickle.HIGHEST_PROTOCOL)


def _fetch_valuation_batch(end_date: str, count: int) -> pd.DataFrame:
    import jqdatasdk as jq
    from jqdatasdk import query, valuation

    _ensure_auth()
    q = query(
        valuation.code,
        valuation.market_cap,
        valuation.circulating_market_cap,
        valuation.turnover_ratio,
    ).filter(valuation.market_cap > 0)
    raw = jq.get_fundamentals_continuously(q, end_date=end_date, count=count, panel=False)
    if raw is None or raw.empty:
        return pd.DataFrame()

    out = raw.copy()
    if "day" in out.columns:
        out["day"] = pd.to_datetime(out["day"])
    return out


def _fetch_profit_batch(end_date: str, count: int) -> pd.DataFrame:
    import jqdatasdk as jq
    from jqdatasdk import indicator, query

    _ensure_auth()
    q = query(indicator.code, indicator.adjusted_profit).filter(
        indicator.adjusted_profit > 0
    )
    raw = jq.get_fundamentals_continuously(q, end_date=end_date, count=count, panel=False)
    if raw is None or raw.empty:
        return pd.DataFrame()
    out = raw.copy()
    if "day" in out.columns:
        out["day"] = pd.to_datetime(out["day"])
    return out


def load_valuation_store(
    trade_days: list[pd.Timestamp],
    *,
    force_refresh: bool = False,
    progress_callback=None,
) -> dict[pd.Timestamp, pd.DataFrame]:
    """
    按交易日加载估值面板。优先读本地缓存；缺失日期批量向聚宽请求并写入缓存。

    若今日额度不够拉完所有缺失日，抛出 QuotaExhaustedError，已下载部分会保留。
    """
    day_map: dict[pd.Timestamp, pd.DataFrame] = {}
    missing: list[pd.Timestamp] = []

    for i, day in enumerate(trade_days):
        day_str = day.strftime("%Y-%m-%d")
        if not force_refresh:
            cached = _load_cached_day(_valuation_day_path(day_str))
            if cached is not None:
                day_map[day] = cached
                continue
        missing.append(day)
        if progress_callback:
            progress_callback("cache", i + 1, len(trade_days), day, len(day_map))

    if not missing:
        return day_map

    idx = 0
    while idx < len(missing):
        spare = get_jq_spare_quota()
        remaining = len(missing) - idx
        batch_size = min(remaining, max(1, spare // EST_STOCKS_PER_DAY - 50))
        if batch_size < 1 or spare < EST_STOCKS_PER_DAY:
            raise QuotaExhaustedError(
                f"今日聚宽 JQData 额度不足（剩余约 {spare:,} 条）。"
                f"已缓存 {len(day_map)}/{len(trade_days)} 个交易日。"
                f"请明天再点「开始回测」，会自动续拉缺失日期（不会重复下载）。",
                cached_days=len(day_map),
                total_days=len(trade_days),
            )

        chunk = missing[idx : idx + batch_size]
        end_str = chunk[-1].strftime("%Y-%m-%d")
        batch_df = _fetch_valuation_batch(end_str, len(chunk))

        chunk_days = {d.strftime("%Y-%m-%d"): d for d in chunk}
        if batch_df.empty:
            idx += batch_size
            continue

        batch_df["day"] = pd.to_datetime(batch_df["day"]).dt.normalize()

        for day_str, day_ts in chunk_days.items():
            day_key = pd.Timestamp(day_str).normalize()
            day_rows = batch_df[batch_df["day"] == day_key]
            if day_rows.empty:
                continue
            df = day_rows.drop(columns=["day"], errors="ignore").reset_index(drop=True)
            _save_cached_day(_valuation_day_path(day_str), df)
            day_map[day_ts] = df

        idx += batch_size
        if progress_callback:
            progress_callback(
                "download",
                len(day_map),
                len(trade_days),
                chunk[-1],
                len(day_map),
            )

    return day_map


def load_profit_store(
    trade_days: list[pd.Timestamp],
    *,
    force_refresh: bool = False,
    progress_callback=None,
) -> dict[pd.Timestamp, set[str]]:
    """加载每日扣非净利润>0 的股票代码集合。"""
    day_map: dict[pd.Timestamp, set[str]] = {}
    missing: list[pd.Timestamp] = []

    for day in trade_days:
        day_str = day.strftime("%Y-%m-%d")
        if not force_refresh:
            cached = _load_cached_day(_profit_day_path(day_str))
            if cached is not None:
                day_map[day] = set(cached["code"].tolist())
                continue
        missing.append(day)

    if not missing:
        return day_map

    idx = 0
    while idx < len(missing):
        spare = get_jq_spare_quota()
        remaining = len(missing) - idx
        batch_size = min(remaining, max(1, spare // EST_STOCKS_PER_DAY - 50))
        if batch_size < 1 or spare < EST_STOCKS_PER_DAY:
            raise QuotaExhaustedError(
                f"今日聚宽 JQData 额度不足（剩余约 {spare:,} 条）。"
                f"净利润数据已缓存 {len(day_map)}/{len(trade_days)} 天。"
                f"请明天再试。",
                cached_days=len(day_map),
                total_days=len(trade_days),
            )

        chunk = missing[idx : idx + batch_size]
        end_str = chunk[-1].strftime("%Y-%m-%d")
        batch_df = _fetch_profit_batch(end_str, len(chunk))
        if not batch_df.empty:
            batch_df["day"] = pd.to_datetime(batch_df["day"]).dt.normalize()

        for day in chunk:
            day_str = day.strftime("%Y-%m-%d")
            day_key = pd.Timestamp(day_str).normalize()
            if batch_df.empty:
                codes: set[str] = set()
            else:
                day_rows = batch_df[batch_df["day"] == day_key]
                codes = set(day_rows["code"].tolist()) if not day_rows.empty else set()
            _save_cached_day(_profit_day_path(day_str), pd.DataFrame({"code": list(codes)}))
            day_map[day] = codes

        idx += batch_size
        if progress_callback:
            progress_callback("profit", len(day_map), len(trade_days), chunk[-1], len(day_map))

    return day_map


def load_st_flags(
    codes: list[str],
    trade_days: list[pd.Timestamp],
    *,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    加载 ST 标记（日期 × 股票）。按整段区间一次 get_extras，结果缓存到本地。
    """
    if not codes or not trade_days:
        return pd.DataFrame()

    start = trade_days[0].strftime("%Y-%m-%d")
    end = trade_days[-1].strftime("%Y-%m-%d")
    cache_path = CACHE_DIR / "st" / f"st_{start.replace('-', '')}_{end.replace('-', '')}.pkl"

    if cache_path.exists() and not force_refresh:
        with cache_path.open("rb") as f:
            return pickle.load(f)

    spare = get_jq_spare_quota()
    est = len(codes) * len(trade_days)
    if est > spare:
        # 额度紧张时退化为：用股票名称判断 ST（不额外消耗额度）
        return _st_from_names(codes, trade_days)

    import jqdatasdk as jq

    _ensure_auth()
    raw = jq.get_extras("is_st", codes, start_date=start, end_date=end, df=True)
    if raw is None or raw.empty:
        return _st_from_names(codes, trade_days)

    raw.index = pd.to_datetime(raw.index)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as f:
        pickle.dump(raw, f, protocol=pickle.HIGHEST_PROTOCOL)
    return raw


def _st_from_names(codes: list[str], trade_days: list[pd.Timestamp]) -> pd.DataFrame:
    """无额度时的降级方案：根据股票名称识别 ST。"""
    import jqdatasdk as jq

    _ensure_auth()
    secs = jq.get_all_securities(types=["stock"])
    st_set = set(
        secs[secs["display_name"].str.contains("ST", na=False)].index.tolist()
    )
    flags = {c: (c in st_set) for c in codes}
    return pd.DataFrame([flags], index=[trade_days[-1]])


def load_price_store(
    codes: list[str],
    trade_days: list[pd.Timestamp],
    *,
    price_field: str = "open",
    include_money: bool = False,
    force_refresh: bool = False,
    progress_callback=None,
) -> pd.DataFrame:
    """批量拉取行情，返回 MultiIndex (date, code)。"""
    if not codes:
        return pd.DataFrame()

    start = trade_days[0].strftime("%Y-%m-%d")
    end = trade_days[-1].strftime("%Y-%m-%d")
    tag = f"{price_field}_m{int(include_money)}"
    cache_path = CACHE_DIR / "prices" / f"{tag}_{start.replace('-', '')}_{end.replace('-', '')}.pkl"
    code_key = "_".join(sorted(codes))
    meta_path = cache_path.with_suffix(".codes.pkl")

    if cache_path.exists() and meta_path.exists() and not force_refresh:
        with meta_path.open("rb") as f:
            saved_codes = pickle.load(f)
        if saved_codes == code_key:
            with cache_path.open("rb") as f:
                return pickle.load(f)

    from src.data.jq_market import fetch_price_panel

    fields = [price_field, "paused", "high_limit", "low_limit"]
    if include_money:
        fields.append("money")
    # 分批拉取，避免单次请求过大
    chunk_size = 80
    frames = []
    for i in range(0, len(codes), chunk_size):
        part = codes[i : i + chunk_size]
        if progress_callback:
            progress_callback("prices", i + len(part), len(codes), None, 0)
        frames.append(fetch_price_panel(part, start, end, fields=fields))

    panel = pd.concat(frames).sort_index() if frames else pd.DataFrame()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as f:
        pickle.dump(panel, f, protocol=pickle.HIGHEST_PROTOCOL)
    with meta_path.open("wb") as f:
        pickle.dump(code_key, f)
    return panel


def collect_candidate_codes(
    valuation_store: dict[pd.Timestamp, pd.DataFrame],
    trade_days: list[pd.Timestamp],
    *,
    exclude_stib: bool,
    sell_rank: int,
    max_positions: int,
    backup_etf: str | None,
) -> list[str]:
    """根据估值排名，预估回测期间可能用到的股票代码。"""
    from src.data.jq_market import _code_filter, rank_universe

    codes: set[str] = set()
    top_n = max(sell_rank + 10, max_positions * 3)
    for day in trade_days:
        df = valuation_store.get(day)
        if df is None or df.empty:
            continue
        df = df[df["code"].map(lambda c: _code_filter(c, exclude_stib))]
        ranked = rank_universe(df)
        codes.update(ranked.head(top_n)["code"].tolist())
    if backup_etf:
        codes.add(backup_etf)
    return sorted(codes)


def load_jq_backtest_data(
    cfg,
    trade_days: list[pd.Timestamp],
    start: str,
    end: str,
    pf: str,
):
    """加载 JQData 回测数据包（含 H1-Pro 元数据）。"""
    from src.backtest.backtest_data import BacktestData
    from src.data.jq_meta import (
        build_avg_money_maps,
        load_bad_audit_codes,
        load_delisting_codes,
        load_list_dates,
    )

    progress = None
    if cfg.progress_callback:

        def progress(kind, cur, total, day, _extra=0):
            if kind == "download" and day is not None:
                cfg.progress_callback(cur, total, day, "拉取估值")

    valuation_store = load_valuation_store(trade_days, progress_callback=progress)
    if len(valuation_store) < len(trade_days):
        raise QuotaExhaustedError(
            f"估值数据不完整（{len(valuation_store)}/{len(trade_days)} 天）。"
            f"请明天再试，已下载部分已缓存。",
            cached_days=len(valuation_store),
            total_days=len(trade_days),
        )

    need_profit = cfg.profit_positive or cfg.h1_pro
    profit_store: dict[pd.Timestamp, set[str]] = {}
    if need_profit:
        profit_store = load_profit_store(trade_days, progress_callback=progress)

    candidate_codes = collect_candidate_codes(
        valuation_store,
        trade_days,
        exclude_stib=cfg.exclude_stib,
        sell_rank=cfg.sell_rank,
        max_positions=cfg.max_positions,
        backup_etf=cfg.backup_etf,
    )

    st_flags = pd.DataFrame()
    if cfg.exclude_st:
        st_flags = load_st_flags(candidate_codes, trade_days)

    need_money = cfg.h1_pro or cfg.min_avg_money > 0
    prices = load_price_store(
        candidate_codes,
        trade_days,
        price_field=pf,
        include_money=need_money,
        progress_callback=progress,
    )

    index_close = pd.Series(dtype=float)
    if cfg.use_index_timing:
        from src.data.jq_market import fetch_index_series

        index_close = fetch_index_series(cfg.index_code, start, end)

    list_dates: dict[str, pd.Timestamp] = {}
    bad_audit: set[str] = set()
    delisting: set[str] = set()
    avg_maps: dict = {}

    if cfg.h1_pro or cfg.min_listed_days > 0:
        try:
            list_dates = load_list_dates()
        except Exception:
            list_dates = {}
    if cfg.exclude_delisting or cfg.h1_pro:
        try:
            delisting = load_delisting_codes()
        except Exception:
            delisting = set()
    if cfg.exclude_bad_audit or cfg.h1_pro:
        try:
            bad_audit = load_bad_audit_codes()
        except Exception:
            bad_audit = set()
    if cfg.min_avg_money > 0 and cfg.min_avg_money_days > 0:
        avg_maps = build_avg_money_maps(
            prices, trade_days, window=cfg.min_avg_money_days
        )

    return BacktestData(
        valuation_store=valuation_store,
        st_flags=st_flags,
        prices=prices,
        index_close=index_close,
        profit_store=profit_store,
        list_dates=list_dates,
        bad_audit_codes=bad_audit,
        delisting_codes=delisting,
        avg_money_maps=avg_maps,
    )


def estimate_rows_needed(n_days: int, n_codes: int, *, cached_days: int = 0) -> int:
    missing = max(0, n_days - cached_days)
    return missing * EST_STOCKS_PER_DAY + n_codes * n_days
