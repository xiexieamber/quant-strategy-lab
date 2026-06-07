"""回测所需数据包（估值、行情、过滤元数据）。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class BacktestData:
    valuation_store: dict[pd.Timestamp, pd.DataFrame]
    st_flags: pd.DataFrame
    prices: pd.DataFrame
    index_close: pd.Series
    profit_store: dict[pd.Timestamp, set[str]] = field(default_factory=dict)
    list_dates: dict[str, pd.Timestamp] = field(default_factory=dict)
    bad_audit_codes: set[str] = field(default_factory=set)
    delisting_codes: set[str] = field(default_factory=set)
    avg_money_maps: dict[pd.Timestamp, dict[str, float]] = field(default_factory=dict)
    profit_ok_codes: set[str] = field(default_factory=set)  # AkShare 静态扣非>0
