"""AkShare 工具：代码转换、重试请求、交易日历。"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

import pandas as pd

T = TypeVar("T")


def fetch_with_retry(
    func: Callable[..., T],
    *args,
    retries: int = 6,
    sleep: float = 2.0,
    **kwargs,
) -> T:
    """网络不稳定时自动重试（东方财富接口偶发断连）。"""
    last_err: Exception | None = None
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            time.sleep(sleep * (i + 1))
    raise last_err  # type: ignore[misc]


def to_std_code(code6: str) -> str:
    """6 位代码 → 聚宽风格代码（与回测引擎统一）。"""
    c = str(code6).zfill(6)
    if c.startswith(("6", "5", "9")) or c.startswith("688"):
        return f"{c}.XSHG"
    return f"{c}.XSHE"


def to_ak_symbol(code: str) -> str:
    """聚宽/标准代码 → AkShare 6 位代码。"""
    return code.split(".")[0].zfill(6)


def code_filter(code6: str, exclude_stib: bool) -> bool:
    if exclude_stib and code6.startswith("688"):
        return False
    if code6.startswith(("8", "4")):
        return False
    return True


def get_trade_days(start: str, end: str) -> list[pd.Timestamp]:
    import akshare as ak

    cal = fetch_with_retry(ak.tool_trade_date_hist_sina)
    cal["trade_date"] = pd.to_datetime(cal["trade_date"])
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    mask = (cal["trade_date"] >= start_ts) & (cal["trade_date"] <= end_ts)
    return cal.loc[mask, "trade_date"].tolist()


def get_date_range_hint() -> tuple[str, str]:
    """AkShare 无账号限制，返回一个合理的默认回测区间。"""
    end = pd.Timestamp.today().normalize()
    start = end - pd.DateOffset(years=1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
