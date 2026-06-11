"""Experiment helper tests."""

import pandas as pd

from src.research.experiment import (
    config_to_jsonable,
    render_small_cap_report,
    small_cap_config_from_dict,
)
from src.strategies.small_cap.config import SmallCapConfig


def test_small_cap_config_from_h1_pro_json():
    cfg = small_cap_config_from_dict(
        {
            "preset": "h1_pro",
            "overrides": {
                "initial_cash": 100_000,
                "empty_months": [1, 4],
            },
        }
    )

    assert cfg.initial_cash == 100_000
    assert cfg.h1_pro is True
    assert cfg.empty_months == (1, 4)


def test_config_to_jsonable_removes_callback():
    cfg = SmallCapConfig.for_h1_pro(progress_callback=lambda *_: None)
    data = config_to_jsonable(cfg)

    assert "progress_callback" not in data
    assert isinstance(data["empty_months"], list)


def test_render_small_cap_report_is_self_contained_markdown():
    cfg = SmallCapConfig.for_h1_pro()
    report = render_small_cap_report(
        metadata={"created_at": "2026-06-08T00:00:00", "git_revision": "abc123"},
        config=cfg,
        metrics={
            "start": "2021-01-01",
            "end": "2025-12-31",
            "total_return": 0.5,
            "annual_return": 0.1,
            "max_drawdown": -0.12,
            "sharpe_ratio": 1.2,
            "trade_count": 20,
            "final_equity": 150_000,
        },
        yearly_returns=pd.DataFrame(
            [{"year": 2024, "return": 0.08}, {"year": 2025, "return": 0.12}]
        ),
    )

    assert "Small Cap Experiment Report" in report
    assert "| 2024 | 0.0800 |" in report
    assert "trading recommendation" in report
