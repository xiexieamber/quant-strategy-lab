"""Tests for Serenity-style candidate scoring."""

from src.strategies.serenity_quant import MarketSnapshot, lot_amount, score_candidate


def test_lot_amount():
    assert lot_amount(12.34) == 1234.0


def test_score_candidate_marks_expensive_stock():
    cfg = {
        "capital": 50000,
        "max_single_position_pct": 0.3,
        "lot_size": 100,
        "allow_star_market": False,
        "allow_chinext": True,
        "score_thresholds": {"buy": 75, "watch": 60},
        "risk_rules": {"hot_20d_return_pct": 0.35, "crowded_ytd_position": 0.9},
        "weights": {
            "serenity": 30,
            "trend": 20,
            "liquidity": 15,
            "quality": 15,
            "catalyst": 10,
            "risk": 20,
        },
    }
    item = {
        "code": "300502",
        "name": "新易盛",
        "theme": "光模块",
        "layer": "CPO",
        "serenity_score": 90,
        "quality_score": 90,
        "catalyst_score": 90,
    }
    snapshot = MarketSnapshot(
        code="300502",
        name="新易盛",
        date="2026-06-09",
        close=765.0,
        ret5=0.02,
        ret20=0.29,
        ret60=0.9,
        dd_high=-0.02,
        pos_ytd=0.96,
        ma20=650,
        ma60=550,
        above_ma20=True,
        above_ma60=True,
        turnover=2.0,
        avg_amount20=20_000_000_000,
    )

    row = score_candidate(item, snapshot, cfg)

    assert row["action"] == "too_expensive"
    assert row["suggested_shares"] == 0


def test_score_candidate_can_be_buy_candidate():
    cfg = {
        "capital": 50000,
        "max_single_position_pct": 0.3,
        "lot_size": 100,
        "allow_star_market": False,
        "allow_chinext": True,
        "score_thresholds": {"buy": 75, "watch": 60},
        "risk_rules": {"hot_20d_return_pct": 0.35, "crowded_ytd_position": 0.9},
        "weights": {
            "serenity": 30,
            "trend": 20,
            "liquidity": 15,
            "quality": 15,
            "catalyst": 10,
            "risk": 20,
        },
    }
    item = {
        "code": "002156",
        "name": "通富微电",
        "theme": "先进封装",
        "layer": "封测",
        "serenity_score": 88,
        "quality_score": 80,
        "catalyst_score": 80,
    }
    snapshot = MarketSnapshot(
        code="002156",
        name="通富微电",
        date="2026-06-09",
        close=63.0,
        ret5=0.01,
        ret20=0.08,
        ret60=0.25,
        dd_high=-0.12,
        pos_ytd=0.65,
        ma20=60,
        ma60=52,
        above_ma20=True,
        above_ma60=True,
        turnover=4.0,
        avg_amount20=10_000_000_000,
    )

    row = score_candidate(item, snapshot, cfg)

    assert row["action"] == "buy_candidate"
    assert row["suggested_shares"] >= 100
