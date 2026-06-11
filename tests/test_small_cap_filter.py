"""Small-cap universe filter tests."""

import pandas as pd

import src.data.ak_cache as ak_cache
from src.data.ak_market import code_filter
from src.strategies.small_cap.config import SmallCapConfig
from src.strategies.small_cap.universe_filter import apply_universe_filter


def test_ak_code_filter_excludes_bse_prefixes():
    assert code_filter("920001", exclude_stib=True) is False
    assert code_filter("830001", exclude_stib=True) is False
    assert code_filter("430001", exclude_stib=True) is False
    assert code_filter("600001", exclude_stib=True) is True
    assert code_filter("300001", exclude_stib=True) is True


def test_universe_filter_excludes_bse_when_enabled():
    day = pd.Timestamp("2026-06-08")
    df = pd.DataFrame(
        [
            {
                "code": "920001.XSHG",
                "market_cap": 1,
                "circulating_market_cap": 1,
                "turnover_ratio": 1,
                "money": 100_000_000,
            },
            {
                "code": "600001.XSHG",
                "market_cap": 2,
                "circulating_market_cap": 2,
                "turnover_ratio": 1,
                "money": 100_000_000,
            },
        ]
    )
    result = apply_universe_filter(
        df,
        day,
        SmallCapConfig(exclude_bse=True),
        st_flags=pd.DataFrame(),
    )

    assert result["code"].tolist() == ["600001.XSHG"]


def test_load_stock_data_refreshes_bad_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "000001.pkl"
    cache_path.write_bytes(b"not-a-valid-pickle")

    value = pd.DataFrame(
        {
            "code": ["000001.XSHE"],
            "market_cap": [1.0],
            "circulating_market_cap": [1.0],
            "close": [10.0],
        },
        index=[pd.Timestamp("2026-06-08")],
    )
    price = pd.DataFrame(
        {
            "open": [10.0],
            "close": [10.0],
            "money": [100.0],
            "turnover_ratio": [1.0],
            "paused": [0.0],
            "high_limit": [0.0],
            "low_limit": [0.0],
        },
        index=[pd.Timestamp("2026-06-08")],
    )

    monkeypatch.setattr(ak_cache, "_stock_cache_path", lambda code6: cache_path)
    monkeypatch.setattr(
        ak_cache,
        "_download_value_only",
        lambda code6: ak_cache._StockAkData(value=value, price=price, full=False),
    )

    data = ak_cache.load_stock_data("000001", value_only=True)

    assert len(data.value) == 1
    assert cache_path.read_bytes() != b"not-a-valid-pickle"
