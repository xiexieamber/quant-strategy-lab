"""AkShare 数据缓存：按股票下载市值/行情，组装成与 JQ 回测兼容的面板。"""

from __future__ import annotations

import os
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
# 并行下载线程数（东方财富接口限流，默认 6；可在环境变量 AK_MAX_WORKERS 调整）
AK_MAX_WORKERS = max(1, int(os.environ.get("AK_MAX_WORKERS", "6")))


@dataclass
class _StockAkData:
    value: pd.DataFrame
    price: pd.DataFrame
    list_date: pd.Timestamp | None = None
    profit_positive: bool = False  # 仅 meta 确认后才为 True
    is_delisting: bool = False
    stock_name: str = ""
    full: bool = False  # True = 已下载完整行情 + 基本面 meta


def _stock_cache_path(code6: str) -> Path:
    return CACHE_DIR / "stock" / f"{code6}.pkl"


def _is_etf_code(code6: str) -> bool:
    """ETF/基金无 stock_value_em 市值接口，需走 K 线通道。"""
    c = str(code6).zfill(6)
    return c.startswith(("51", "56", "15", "16", "588"))


def _normalize_value(raw: pd.DataFrame, code_std: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
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
    if raw is None or raw.empty:
        return pd.DataFrame()
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
        info = fetch_with_retry(
            ak.stock_individual_info_em,
            symbol=code6,
            retries=2,
            sleep=0.5,
        )
        if info is None or info.empty:
            return list_date, profit_ok, is_delist, name
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
        fin = fetch_with_retry(
            ak.stock_financial_analysis_indicator_em,
            symbol=code6,
            indicator="扣非净利润",
            retries=2,
            sleep=0.5,
        )
        if fin is not None and not fin.empty:
            latest = fin.iloc[-1, 1]
            profit_ok = float(str(latest).replace(",", "")) > 0
    except Exception:
        pass

    return list_date, profit_ok, is_delist, name


def _fetch_hist_price(code6: str, code_std: str, hist_start: str) -> pd.DataFrame:
    import akshare as ak

    end_s = pd.Timestamp.today().strftime("%Y%m%d")
    fetchers: list = []
    if _is_etf_code(code6):
        fetchers.append(
            lambda: fetch_with_retry(
                ak.fund_etf_hist_em,
                symbol=code6,
                period="daily",
                start_date=hist_start,
                end_date=end_s,
                adjust="qfq",
                retries=2,
                sleep=0.5,
            )
        )
    fetchers.append(
        lambda: fetch_with_retry(
            ak.stock_zh_a_hist,
            symbol=code6,
            period="daily",
            start_date=hist_start,
            end_date=end_s,
            adjust="qfq",
            retries=2,
            sleep=0.5,
        )
    )

    last_err: Exception | None = None
    for fetch in fetchers:
        try:
            px_raw = fetch()
            price = _normalize_price(px_raw, code_std)
            if not price.empty:
                return price
        except Exception as e:
            last_err = e
    raise ValueError(f"无法获取 {code6} 的 K 线数据") from last_err


def _value_from_price(price: pd.DataFrame, code_std: str) -> pd.DataFrame:
    """ETF 等无市值接口的标的：用收盘价构造占位估值表。"""
    df = price.reset_index()
    day_col = "day" if "day" in df.columns else df.columns[0]
    out = pd.DataFrame(
        {
            "code": code_std,
            "market_cap": 1.0,
            "circulating_market_cap": 1.0,
            "close": df["close"],
        },
        index=pd.to_datetime(df[day_col]),
    )
    out.index.name = "day"
    return out


def _download_etf_full(code6: str, *, hist_start: str) -> _StockAkData:
    """ETF 完整数据：仅 K 线（空仓期持有黄金 ETF 等）。"""
    code_std = to_std_code(code6)
    price = _fetch_hist_price(code6, code_std, hist_start)
    value = _value_from_price(price, code_std)
    return _StockAkData(
        value=value,
        price=price,
        profit_positive=True,
        stock_name="ETF",
        full=True,
    )


def _download_value_only(code6: str) -> _StockAkData:
    """仅拉市值序列（1 次 API），用于全市场排名。"""
    import akshare as ak

    if _is_etf_code(code6):
        return _download_etf_full(code6, hist_start="20180101")

    code_std = to_std_code(code6)
    try:
        val_raw = fetch_with_retry(
            ak.stock_value_em,
            symbol=code6,
            retries=2,
            sleep=0.5,
        )
    except Exception:
        val_raw = None
    value = _normalize_value(val_raw, code_std)
    if value.empty:
        raise ValueError(f"无法获取 {code6} 的市值数据")
    return _StockAkData(
        value=value,
        price=_price_from_value(value),
        full=False,
    )


def _download_price_and_meta(
    code6: str,
    value: pd.DataFrame,
    *,
    hist_start: str,
) -> tuple[pd.DataFrame, pd.Timestamp | None, bool, bool, str]:
    """拉完整 K 线 + 上市/利润 meta（候选股才需要）。"""
    import akshare as ak

    if _is_etf_code(code6):
        code_std = to_std_code(code6)
        try:
            price = _fetch_hist_price(code6, code_std, hist_start)
        except Exception:
            price = _price_from_value(value) if value is not None and not value.empty else pd.DataFrame()
        return price, None, True, False, "ETF"

    code_std = to_std_code(code6)
    try:
        price = _fetch_hist_price(code6, code_std, hist_start)
    except Exception:
        price = _price_from_value(value) if value is not None and not value.empty else pd.DataFrame()

    list_date, profit_ok, is_delist, name = _fetch_stock_meta(code6)
    return price, list_date, profit_ok, is_delist, name


def _download_stock_full(code6: str, *, hist_start: str) -> _StockAkData:
    """完整下载（市值 + K 线 + meta）。"""
    if _is_etf_code(code6):
        return _download_etf_full(code6, hist_start=hist_start)
    base = _download_value_only(code6)
    price, list_date, profit_ok, is_delist, name = _download_price_and_meta(
        code6, base.value, hist_start=hist_start
    )
    return _StockAkData(
        value=base.value,
        price=price,
        list_date=list_date,
        profit_positive=profit_ok,
        is_delisting=is_delist,
        stock_name=name,
        full=True,
    )


def _enhance_stock(
    code6: str,
    existing: _StockAkData | None,
    *,
    hist_start: str,
    force_refresh: bool,
) -> _StockAkData:
    if existing is not None and getattr(existing, "full", False) and not force_refresh:
        return existing
    if existing is None or force_refresh:
        return _download_stock_full(code6, hist_start=hist_start)
    price, list_date, profit_ok, is_delist, name = _download_price_and_meta(
        code6, existing.value, hist_start=hist_start
    )
    return _StockAkData(
        value=existing.value,
        price=price,
        list_date=list_date,
        profit_positive=profit_ok,
        is_delisting=is_delist,
        stock_name=name,
        full=True,
    )


def load_stock_data(
    code6: str,
    *,
    force_refresh: bool = False,
    value_only: bool = False,
    hist_start: str = "20180101",
) -> _StockAkData:
    path = _stock_cache_path(code6)
    existing: _StockAkData | None = None
    if path.exists() and not force_refresh:
        try:
            with path.open("rb") as f:
                existing = pickle.load(f)
            is_full = getattr(existing, "full", True)  # 旧缓存视为完整
            if value_only or (is_full and not force_refresh):
                return existing
        except (ModuleNotFoundError, pickle.UnpicklingError, EOFError, AttributeError):
            # 旧缓存可能包含本地未安装的 pandas/pyarrow dtype，或写入中断。
            # 这种情况下不要让并行下载层静默吞掉异常，直接重新拉取并覆盖缓存。
            existing = None

    if _is_etf_code(code6):
        data = _download_etf_full(code6, hist_start=hist_start)
    elif value_only:
        data = _download_value_only(code6)
    elif existing is not None and not force_refresh:
        data = _enhance_stock(code6, existing, hist_start=hist_start, force_refresh=False)
    else:
        data = _download_stock_full(code6, hist_start=hist_start)

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


def _parallel_download(
    codes: list[str],
    *,
    force_refresh: bool,
    value_only: bool,
    hist_start: str,
    progress_callback=None,
    progress_kind: str = "download_stock",
) -> dict[str, _StockAkData]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    stock_cache: dict[str, _StockAkData] = {}
    total = len(codes)
    done = 0

    def _one(code6: str) -> tuple[str, _StockAkData | None]:
        try:
            return code6, load_stock_data(
                code6,
                force_refresh=force_refresh,
                value_only=value_only,
                hist_start=hist_start,
            )
        except Exception:
            return code6, None

    workers = AK_MAX_WORKERS
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, c) for c in codes]
        for fut in as_completed(futures):
            code6, data = fut.result()
            if data is not None:
                stock_cache[code6] = data
            done += 1
            if progress_callback:
                progress_callback(progress_kind, done, total, code6)
    return stock_cache


def ensure_universe_downloaded(
    universe: list[str],
    *,
    force_refresh: bool = False,
    progress_callback=None,
    value_only: bool = True,
    hist_start: str = "20180101",
) -> dict[str, _StockAkData]:
    """下载股票池。默认 value_only=True，只拉市值（快）。"""
    return _parallel_download(
        universe,
        force_refresh=force_refresh,
        value_only=value_only,
        hist_start=hist_start,
        progress_callback=progress_callback,
        progress_kind="download_value",
    )


def ensure_candidates_enhanced(
    candidates: list[str],
    stock_cache: dict[str, _StockAkData],
    *,
    force_refresh: bool = False,
    hist_start: str = "20180101",
    progress_callback=None,
) -> None:
    """为候选股补全 K 线 + meta（通常仅几百只）。"""
    need: list[str] = []
    for c in candidates:
        code6 = to_ak_symbol(c)
        data = stock_cache.get(code6)
        if data is not None and getattr(data, "full", True):
            continue
        need.append(code6)
    if not need:
        return

    extra = _parallel_download(
        need,
        force_refresh=force_refresh,
        value_only=False,
        hist_start=hist_start,
        progress_callback=progress_callback,
        progress_kind="download_detail",
    )
    stock_cache.update(extra)


def build_valuation_store(
    trade_days: list[pd.Timestamp],
    universe: list[str],
    stock_cache: dict[str, _StockAkData],
    progress_callback=None,
) -> dict[pd.Timestamp, pd.DataFrame]:
    day_map: dict[pd.Timestamp, pd.DataFrame] = {}
    total = len(trade_days)

    for i, day in enumerate(trade_days):
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

        if progress_callback:
            progress_callback(i + 1, total, day, "组装估值")

    return day_map


def _patch_valuation_liquidity(
    valuation_store: dict[pd.Timestamp, pd.DataFrame],
    stock_cache: dict[str, _StockAkData],
) -> None:
    """把已下载详情的股票成交额/换手率写回估值表（供 H1-Pro 流动性过滤）。"""
    for day, df in valuation_store.items():
        if df is None or df.empty:
            continue
        for idx, row in df.iterrows():
            code6 = to_ak_symbol(str(row["code"]))
            data = stock_cache.get(code6)
            if data is None or not getattr(data, "full", False):
                continue
            try:
                p = data.price.loc[day]
            except KeyError:
                continue
            df.at[idx, "money"] = float(p.get("money", 0) or 0)
            df.at[idx, "turnover_ratio"] = float(p.get("turnover_ratio", 0) or 0)


def collect_candidate_codes(
    valuation_store: dict[pd.Timestamp, pd.DataFrame],
    trade_days: list[pd.Timestamp],
    *,
    exclude_stib: bool,
    sell_rank: int,
    max_positions: int,
    backup_etf: str | None,
    h1_pro: bool = False,
) -> list[str]:
    from src.data.jq_market import _code_filter

    codes: set[str] = set()
    # H1-Pro 需对更多小市值股拉利润/成交额 meta，否则 AkShare 过滤会把池子缩到几十只
    if h1_pro:
        top_n = max(sell_rank + 100, max_positions * 10, 150)
    else:
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
        try:
            raw = fetch_with_retry(
                ak.stock_zh_a_hist,
                symbol="512100",
                period="daily",
                start_date=start_s,
                end_date=end_s,
                adjust="qfq",
            )
        except Exception:
            raw = None
        if raw is None or raw.empty:
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
    money_series: dict[str, pd.Series] = {}
    min_periods = max(1, window // 2)

    for code6, data in stock_cache.items():
        if not getattr(data, "full", False):
            continue
        if data.price.empty or "money" not in data.price.columns:
            continue
        code_std = to_std_code(code6)
        money_series[code_std] = (
            pd.to_numeric(data.price["money"], errors="coerce")
            .reindex(trade_days)
            .fillna(0.0)
        )

    if not money_series:
        return {day: {} for day in trade_days}

    rolling = (
        pd.DataFrame(money_series, index=trade_days)
        .rolling(window=window, min_periods=min_periods)
        .mean()
    )

    result: dict[pd.Timestamp, dict[str, float]] = {}
    for day, row in rolling.iterrows():
        clean = row.dropna()
        result[day] = {code: float(value) for code, value in clean.items()}
    return result


def _hist_start_date(start: str, cfg) -> str:
    """K 线起始日：回测起点再往前留缓冲（均线 / 5 日均成交额）。"""
    buf_days = max(cfg.index_ma if cfg.use_index_timing else 0, cfg.min_avg_money_days, 30)
    dt = pd.Timestamp(start) - pd.Timedelta(days=int(buf_days * 1.6))
    return dt.strftime("%Y%m%d")


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
    hist_start = _hist_start_date(start, cfg)

    def _dl_cb(kind, cur, tot, code6):
        if not cfg.progress_callback:
            return
        day = trade_days[min(cur - 1, len(trade_days) - 1)]
        labels = {
            "download_value": f"市值 {code6}",
            "download_detail": f"详情 {code6}",
        }
        cfg.progress_callback(cur, tot, day, labels.get(kind, code6))

    # 阶段 1：全市场只拉市值（约 4000×1 次请求，并行）
    stock_cache = ensure_universe_downloaded(
        universe,
        progress_callback=_dl_cb,
        value_only=True,
        hist_start=hist_start,
    )

    if cfg.progress_callback:
        cfg.progress_callback(0, len(trade_days), trade_days[0], "组装估值")

    def _assemble_cb(cur, tot, day, phase):
        if cfg.progress_callback:
            cfg.progress_callback(cur, tot, day, phase)

    valuation_store = build_valuation_store(
        trade_days, universe, stock_cache, progress_callback=_assemble_cb
    )

    pf = "open" if cfg.rebalance_price == "open" else "close"
    candidate_codes = collect_candidate_codes(
        valuation_store,
        trade_days,
        exclude_stib=cfg.exclude_stib,
        sell_rank=cfg.sell_rank,
        max_positions=cfg.max_positions,
        backup_etf=cfg.backup_etf,
        h1_pro=cfg.h1_pro,
    )

    # 阶段 2：候选股补 K 线 + 利润/上市 meta（H1-Pro 约几百～一千只）
    if cfg.progress_callback:
        cfg.progress_callback(0, len(candidate_codes), trade_days[0], "候选股详情")
    ensure_candidates_enhanced(
        candidate_codes,
        stock_cache,
        hist_start=hist_start,
        progress_callback=_dl_cb,
    )

    # 空仓期黄金 ETF（518880 等）无市值接口，单独保证拉取成功
    if cfg.backup_etf:
        etf6 = to_ak_symbol(cfg.backup_etf)
        cached = stock_cache.get(etf6)
        if cached is None or not getattr(cached, "full", False):
            try:
                stock_cache[etf6] = load_stock_data(
                    etf6, value_only=False, hist_start=hist_start
                )
            except Exception:
                pass

    if cfg.h1_pro:
        _patch_valuation_liquidity(valuation_store, stock_cache)

    prices = build_price_panel(candidate_codes, trade_days, stock_cache, price_field=pf)

    # AkShare path already applies the current ST exclusion in get_universe_codes().
    # Calling stock_info_a_code_name again here is both redundant and occasionally
    # very slow, so keep the per-day ST matrix empty for this data source.
    st_flags = pd.DataFrame()

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
