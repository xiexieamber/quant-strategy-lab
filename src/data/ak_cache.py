"""AkShare 数据缓存：按股票下载市值/行情，组装成与 JQ 回测兼容的面板。"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.data.ak_market import (
    code_filter,
    fetch_with_retry,
    to_ak_symbol,
    to_std_code,
)
from src.data.fetch_jqdata import PROJECT_ROOT
from src.data.jq_market import rank_universe

CACHE_DIR = PROJECT_ROOT / "data" / "processed" / "ak_cache"


@dataclass
class _StockAkData:
    value: pd.DataFrame
    price: pd.DataFrame
    list_date: pd.Timestamp | None = None
    profit_positive: bool = True
    is_delisting: bool = False
    stock_name: str = ""


def _stock_cache_path(code6: str) -> Path:
    return CACHE_DIR / "stock" / f"{code6}.pkl"


def _normalize_value(raw: pd.DataFrame, code_std: str) -> pd.DataFrame:
    df = raw.copy()
    df["day"] = pd.to_datetime(df["数据日期"])
    df["code"] = code_std
    df["market_cap"] = pd.to_numeric(df["总市值"], errors="coerce") / 1e8
    df["circulating_market_cap"] = pd.to_numeric(df["流通市值"], errors="coerce") / 1e8
    df["close"] = pd.to_numeric(df["当日收盘价"], errors="coerce")
    out = df[
        ["day", "code", "market_cap", "circulating_market_cap", "close"]
    ].dropna(subset=["market_cap", "circulating_market_cap"])
    return out.set_index("day")


def _price_from_value(value: pd.DataFrame) -> pd.DataFrame:
    """行情接口失败时，用市值数据里的收盘价近似（开盘价=收盘价）。"""
    df = value.reset_index()
    out = pd.DataFrame(
        {
            "open": df["close"],
            "close": df["close"],
            "money": 0.0,
            "turnover_ratio": 0.0,
            "paused": 0.0,
            "high_limit": 0.0,
            "low_limit": 0.0,
        },
        index=pd.to_datetime(df["day"]),
    )
    out.index.name = "day"
    return out


def _normalize_price(raw: pd.DataFrame, code_std: str) -> pd.DataFrame:
    df = raw.copy()
    df["day"] = pd.to_datetime(df["日期"])
    df["code"] = code_std
    df = df.rename(
        columns={
            "开盘": "open",
            "收盘": "close",
            "成交额": "money",
            "换手率": "turnover_ratio",
        }
    )
    df["paused"] = 0.0
    df["high_limit"] = 0.0
    df["low_limit"] = 0.0
    cols = [
        "open",
        "close",
        "money",
        "turnover_ratio",
        "paused",
        "high_limit",
        "low_limit",
    ]
    out = df[cols].copy()
    for c in cols:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out.index = pd.to_datetime(df["day"])
    out.index.name = "day"
    return out.dropna(subset=["open", "close"])


def _fetch_stock_meta(code6: str) -> tuple[pd.Timestamp | None, bool, bool, str]:
    """上市日期、扣非净利润是否>0、是否退市整理、名称。"""
    import akshare as ak

    list_date: pd.Timestamp | None = None
    profit_ok = True
    is_delist = False
    name = ""
    try:
        info = fetch_with_retry(ak.stock_individual_info_em, symbol=code6)
        for _, row in info.iterrows():
            item = str(row.iloc[0])
            val = str(row.iloc[1])
            if "上市" in item:
                list_date = pd.Timestamp(val)
            if item in ("股票简称", "简称"):
                name = val
                if "退" in val:
                    is_delist = True
    except Exception:
        pass

    try:
        fin = fetch_with_retry(ak.stock_financial_analysis_indicator_em, symbol=code6, indicator="扣非净利润")
        if fin is not None and not fin.empty:
            latest = fin.iloc[-1, 1]
            profit_ok = float(str(latest).replace(",", "")) > 0
    except Exception:
        pass

    return list_date, profit_ok, is_delist, name


def _download_stock(code6: str) -> _StockAkData:
    import akshare as ak

    code_std = to_std_code(code6)
    val_raw = fetch_with_retry(ak.stock_value_em, symbol=code6)
    value = _normalize_value(val_raw, code_std)
    try:
        px_raw = fetch_with_retry(
            ak.stock_zh_a_hist,
            symbol=code6,
            period="daily",
            start_date="20180101",
            end_date=pd.Timestamp.today().strftime("%Y%m%d"),
            adjust="qfq",
            retries=6,
            sleep=2.5,
        )
        price = _normalize_price(px_raw, code_std)
    except Exception:
        price = _price_from_value(value)

    list_date, profit_ok, is_delist, name = _fetch_stock_meta(code6)
    return _StockAkData(
        value=value,
        price=price,
        list_date=list_date,
        profit_positive=profit_ok,
        is_delisting=is_delist,
        stock_name=name,
    )


def load_stock_data(code6: str, *, force_refresh: bool = False) -> _StockAkData:
    path = _stock_cache_path(code6)
    if path.exists() and not force_refresh:
        with path.open("rb") as f:
            return pickle.load(f)
    data = _download_stock(code6)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
    return data


def get_universe_codes(
    *,
    exclude_st: bool = True,
    exclude_stib: bool = True,
    force_refresh: bool = False,
) -> list[str]:
    import akshare as ak

    tag = f"u_{int(exclude_st)}_{int(exclude_stib)}.pkl"
    path = CACHE_DIR / tag
    if path.exists() and not force_refresh:
        with path.open("rb") as f:
            return pickle.load(f)

    raw = fetch_with_retry(ak.stock_info_a_code_name)
    codes: list[str] = []
    for _, row in raw.iterrows():
        code6 = str(row["code"]).zfill(6)
        name = str(row.get("name", ""))
        if not code_filter(code6, exclude_stib):
            continue
        if exclude_st and "ST" in name.upper():
            continue
        codes.append(code6)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(codes, f)
    return codes


def build_st_flags(trade_days: list[pd.Timestamp]) -> pd.DataFrame:
    import akshare as ak

    raw = fetch_with_retry(ak.stock_info_a_code_name)
    flags: dict[str, bool] = {}
    for _, row in raw.iterrows():
        c = to_std_code(str(row["code"]))
        flags[c] = "ST" in str(row["name"]).upper()
    return pd.DataFrame([flags] * len(trade_days), index=trade_days)


def ensure_universe_downloaded(
    universe: list[str],
    *,
    force_refresh: bool = False,
    progress_callback=None,
) -> dict[str, _StockAkData]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    stock_cache: dict[str, _StockAkData] = {}
    total = len(universe)
    done = 0

    def _one(code6: str) -> tuple[str, _StockAkData]:
        return code6, load_stock_data(code6, force_refresh=force_refresh)

    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = [pool.submit(_one, c) for c in universe]
        for fut in as_completed(futures):
            code6, data = fut.result()
            stock_cache[code6] = data
            done += 1
            if progress_callback:
                progress_callback("download_stock", done, total, code6)
    return stock_cache


def build_valuation_store(
    trade_days: list[pd.Timestamp],
    universe: list[str],
    stock_cache: dict[str, _StockAkData],
) -> dict[pd.Timestamp, pd.DataFrame]:
    day_map: dict[pd.Timestamp, pd.DataFrame] = {}

    for day in trade_days:
        rows: list[dict] = []
        for code6 in universe:
            data = stock_cache.get(code6)
            if data is None:
                continue
            try:
                v = data.value.loc[day]
            except KeyError:
                continue
            item = {
                "code": v["code"] if isinstance(v["code"], str) else v["code"].iloc[0],
                "market_cap": float(v["market_cap"]),
                "circulating_market_cap": float(v["circulating_market_cap"]),
                "turnover_ratio": 0.0,
                "money": 0.0,
            }
            try:
                p = data.price.loc[day]
                item["turnover_ratio"] = float(p["turnover_ratio"])
                item["money"] = float(p["money"])
            except (KeyError, TypeError, ValueError):
                pass
            rows.append(item)

        if rows:
            day_map[day] = pd.DataFrame(rows)

    return day_map


def collect_candidate_codes(
    valuation_store: dict[pd.Timestamp, pd.DataFrame],
    trade_days: list[pd.Timestamp],
    *,
    exclude_stib: bool,
    sell_rank: int,
    max_positions: int,
    backup_etf: str | None,
) -> list[str]:
    from src.data.jq_market import _code_filter

    codes: set[str] = set()
    top_n = max(sell_rank + 10, max_positions * 3)
    for day in trade_days:
        df = valuation_store.get(day, pd.DataFrame())
        if df.empty:
            continue
        df = df[df["code"].map(lambda c: _code_filter(c, exclude_stib))]
        ranked = rank_universe(df)
        codes.update(ranked.head(top_n)["code"].tolist())
    if backup_etf:
        codes.add(backup_etf)
    return sorted(codes)


def build_price_panel(
    codes: list[str],
    trade_days: list[pd.Timestamp],
    stock_cache: dict[str, _StockAkData],
    *,
    price_field: str = "open",
) -> pd.DataFrame:
    records: list[dict] = []
    extra = ["paused", "high_limit", "low_limit"]

    for code in codes:
        code6 = to_ak_symbol(code)
        data = stock_cache.get(code6)
        if data is None:
            continue
        for day in trade_days:
            try:
                row = data.price.loc[day]
            except KeyError:
                continue
            if pd.isna(row.get("open")):
                continue
            rec = {
                "time": day,
                "code": code,
                price_field: float(row[price_field if price_field in row.index else "open"]),
                "money": float(row.get("money", 0) or 0),
            }
            for c in extra:
                rec[c] = float(row.get(c, 0) or 0)
            records.append(rec)

    if not records:
        return pd.DataFrame()

    raw = pd.DataFrame(records)
    raw["time"] = pd.to_datetime(raw["time"])
    return raw.set_index(["time", "code"]).sort_index()


def fetch_index_series(index_code: str, start: str, end: str) -> pd.Series:
    import akshare as ak

    code6 = to_ak_symbol(index_code)
    symbol = f"sh{code6}"
    start_s = pd.Timestamp(start).strftime("%Y%m%d")
    end_s = pd.Timestamp(end).strftime("%Y%m%d")

    try:
        raw = fetch_with_retry(
            ak.stock_zh_index_daily_em,
            symbol=symbol,
            start_date=start_s,
            end_date=end_s,
        )
    except Exception:
        raw = pd.DataFrame()

    if raw is None or raw.empty:
        raw = fetch_with_retry(
            ak.stock_zh_a_hist,
            symbol="512100",
            period="daily",
            start_date=start_s,
            end_date=end_s,
            adjust="qfq",
        )
        if raw.empty:
            return pd.Series(dtype=float)
        return raw.set_index(pd.to_datetime(raw["日期"]))["收盘"].astype(float).sort_index()

    date_col = "date" if "date" in raw.columns else raw.columns[0]
    close_col = "close" if "close" in raw.columns else "收盘"
    raw[date_col] = pd.to_datetime(raw[date_col])
    return raw.set_index(date_col)[close_col].astype(float).sort_index()


def build_ak_meta(stock_cache: dict[str, _StockAkData]) -> tuple[
    dict[str, pd.Timestamp],
    set[str],
    set[str],
    set[str],
]:
    """从 AkShare 股票缓存提取 list_dates / delisting / profit_ok / bad_audit(空)。"""
    list_dates: dict[str, pd.Timestamp] = {}
    delisting: set[str] = set()
    profit_ok: set[str] = set()
    for code6, data in stock_cache.items():
        code_std = to_std_code(code6)
        if data.list_date is not None:
            list_dates[code_std] = data.list_date
        if data.is_delisting or "退" in data.stock_name:
            delisting.add(code_std)
        if data.profit_positive:
            profit_ok.add(code_std)
    return list_dates, delisting, profit_ok, set()


def build_ak_avg_money_maps(
    stock_cache: dict[str, _StockAkData],
    trade_days: list[pd.Timestamp],
    window: int = 5,
) -> dict[pd.Timestamp, dict[str, float]]:
    result: dict[pd.Timestamp, dict[str, float]] = {}
    for day in trade_days:
        m: dict[str, float] = {}
        for code6, data in stock_cache.items():
            code_std = to_std_code(code6)
            try:
                hist = data.price.loc[:day].tail(window)
            except Exception:
                continue
            if len(hist) >= max(1, window // 2):
                m[code_std] = float(hist["money"].fillna(0).mean())
        result[day] = m
    return result


def prepare_akshare_data(
    trade_days: list[pd.Timestamp],
    start: str,
    end: str,
    cfg,
):
    from src.backtest.backtest_data import BacktestData

    universe = get_universe_codes(
        exclude_st=cfg.exclude_st,
        exclude_stib=cfg.exclude_stib,
    )

    def _dl_cb(kind, cur, tot, code6):
        if cfg.progress_callback and kind == "download_stock":
            day = trade_days[min(cur - 1, len(trade_days) - 1)]
            cfg.progress_callback(cur, tot, day, f"下载 {code6}")

    stock_cache = ensure_universe_downloaded(universe, progress_callback=_dl_cb)

    if cfg.progress_callback:
        cfg.progress_callback(0, len(trade_days), trade_days[0], "组装估值")

    valuation_store = build_valuation_store(trade_days, universe, stock_cache)

    pf = "open" if cfg.rebalance_price == "open" else "close"
    candidate_codes = collect_candidate_codes(
        valuation_store,
        trade_days,
        exclude_stib=cfg.exclude_stib,
        sell_rank=cfg.sell_rank,
        max_positions=cfg.max_positions,
        backup_etf=cfg.backup_etf,
    )
    prices = build_price_panel(candidate_codes, trade_days, stock_cache, price_field=pf)

    st_flags = build_st_flags(trade_days) if cfg.exclude_st else pd.DataFrame()

    index_close = pd.Series(dtype=float)
    if cfg.use_index_timing:
        index_close = fetch_index_series(cfg.index_code, start, end)

    list_dates, delisting, profit_ok, _ = build_ak_meta(stock_cache)
    avg_maps = {}
    if cfg.min_avg_money > 0 and cfg.min_avg_money_days > 0:
        avg_maps = build_ak_avg_money_maps(
            stock_cache, trade_days, window=cfg.min_avg_money_days
        )

    profit_store: dict[pd.Timestamp, set[str]] = {}
    if cfg.profit_positive:
        profit_store = {d: profit_ok for d in trade_days}

    return BacktestData(
        valuation_store=valuation_store,
        st_flags=st_flags,
        prices=prices,
        index_close=index_close,
        profit_store=profit_store,
        list_dates=list_dates,
        bad_audit_codes=set(),
        delisting_codes=delisting,
        avg_money_maps=avg_maps,
        profit_ok_codes=profit_ok,
    )
