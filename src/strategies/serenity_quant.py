"""Serenity-style A-share candidate scoring and decision memory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.data.ak_cache import load_stock_data


ROOT = Path(__file__).resolve().parents[2]

ACTION_LABELS = {
    "buy_candidate": "可买候选",
    "watch": "观察",
    "watch_hot": "过热观察",
    "too_expensive": "一手过大",
    "avoid": "回避",
}

RISK_NOTE_LABELS = {
    "none": "无",
    "star-market-permission": "科创板权限",
    "chinext-permission": "创业板权限",
    "20d-hot": "20日涨幅过热",
    "near-ytd-high": "接近年内高点",
    "one-lot-too-large": "一手金额过大",
    "low-liquidity": "流动性偏低",
}


@dataclass
class MarketSnapshot:
    code: str
    name: str
    date: str
    close: float
    ret5: float | None
    ret20: float | None
    ret60: float | None
    dd_high: float | None
    pos_ytd: float | None
    ma20: float | None
    ma60: float | None
    above_ma20: bool
    above_ma60: bool
    turnover: float | None
    avg_amount20: float | None


def load_strategy_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def lot_amount(price: float, lot_size: int = 100) -> float:
    return float(price) * lot_size


def is_star_market(code: str) -> bool:
    return code.startswith("688")


def is_chinext(code: str) -> bool:
    return code.startswith("300")


def fetch_snapshot(item: dict[str, Any], *, start: str = "20260101") -> MarketSnapshot:
    data = load_stock_data(item["code"], value_only=False, hist_start=start)
    price = data.price.sort_index()
    price = price[price.index >= pd.Timestamp("2026-01-01")]
    if price.empty or pd.to_numeric(price.get("close"), errors="coerce").dropna().empty:
        data = load_stock_data(
            item["code"],
            value_only=False,
            hist_start=start,
            force_refresh=True,
        )
        price = data.price.sort_index()
        price = price[price.index >= pd.Timestamp("2026-01-01")]
    if price.empty:
        raise ValueError(f"{item['code']} has no 2026 price data")

    close = pd.to_numeric(price["close"], errors="coerce").dropna()
    if close.empty:
        raise ValueError(f"{item['code']} has no valid close data")

    latest_date = close.index[-1]
    latest_close = float(close.iloc[-1])
    ma20 = float(close.tail(20).mean()) if len(close) >= 20 else None
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else None
    ytd_high = float(close.max())
    ytd_low = float(close.min())
    dd_high = latest_close / ytd_high - 1 if ytd_high > 0 else None
    pos_ytd = (
        (latest_close - ytd_low) / (ytd_high - ytd_low)
        if ytd_high > ytd_low
        else None
    )

    return MarketSnapshot(
        code=item["code"],
        name=item["name"],
        date=latest_date.strftime("%Y-%m-%d"),
        close=latest_close,
        ret5=_window_return(close, 5),
        ret20=_window_return(close, 20),
        ret60=_window_return(close, 60),
        dd_high=dd_high,
        pos_ytd=pos_ytd,
        ma20=ma20,
        ma60=ma60,
        above_ma20=bool(ma20 is not None and latest_close > ma20),
        above_ma60=bool(ma60 is not None and latest_close > ma60),
        turnover=_latest_value(price, "turnover_ratio"),
        avg_amount20=_avg_value(price, "money", 20),
    )


def score_candidate(
    item: dict[str, Any],
    snapshot: MarketSnapshot,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    capital = float(cfg.get("capital", 50000))
    weights = cfg["weights"]
    risk_rules = cfg["risk_rules"]
    lot_size = int(cfg.get("lot_size", 100))

    serenity = _bounded(item.get("serenity_score", 0), 0, 100) / 100 * weights["serenity"]
    trend = _trend_score(snapshot, weights["trend"])
    liquidity = _liquidity_score(snapshot, weights["liquidity"])
    quality = _bounded(item.get("quality_score", 0), 0, 100) / 100 * weights["quality"]
    catalyst = _bounded(item.get("catalyst_score", 0), 0, 100) / 100 * weights["catalyst"]
    risk_penalty, risk_notes = _risk_penalty(item, snapshot, cfg)

    total = serenity + trend + liquidity + quality + catalyst - risk_penalty
    total = round(max(0.0, min(100.0, total)), 2)

    one_lot = lot_amount(snapshot.close, lot_size)
    max_single_cash = capital * float(cfg.get("max_single_position_pct", 0.3))
    suggested_lots = 0
    if one_lot <= max_single_cash and total >= cfg["score_thresholds"]["watch"]:
        suggested_lots = max(1, int(max_single_cash // one_lot))

    action = "avoid"
    if total >= cfg["score_thresholds"]["buy"] and suggested_lots > 0:
        action = "buy_candidate"
    elif total >= cfg["score_thresholds"]["watch"]:
        action = "watch"

    if snapshot.ret20 is not None and snapshot.ret20 > risk_rules["hot_20d_return_pct"]:
        action = "watch_hot"
    if one_lot > max_single_cash:
        action = "too_expensive"

    return {
        "date": snapshot.date,
        "code": snapshot.code,
        "name": snapshot.name,
        "theme": item.get("theme", ""),
        "layer": item.get("layer", ""),
        "close": snapshot.close,
        "one_lot_amount": round(one_lot, 2),
        "ret5": snapshot.ret5,
        "ret20": snapshot.ret20,
        "ret60": snapshot.ret60,
        "dd_high": snapshot.dd_high,
        "pos_ytd": snapshot.pos_ytd,
        "above_ma20": snapshot.above_ma20,
        "above_ma60": snapshot.above_ma60,
        "avg_amount20": snapshot.avg_amount20,
        "score": total,
        "action": action,
        "suggested_lots": suggested_lots,
        "suggested_shares": suggested_lots * lot_size,
        "suggested_amount": round(suggested_lots * one_lot, 2),
        "risk_notes": "; ".join(risk_notes) if risk_notes else "none",
        "score_breakdown": {
            "serenity": round(serenity, 2),
            "trend": round(trend, 2),
            "liquidity": round(liquidity, 2),
            "quality": round(quality, 2),
            "catalyst": round(catalyst, 2),
            "risk_penalty": round(risk_penalty, 2),
        },
    }


def build_daily_report(
    rows: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    run_date: str,
) -> str:
    ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
    buy = [r for r in ranked if r["action"] == "buy_candidate"]
    watch = [r for r in ranked if r["action"] != "buy_candidate"]

    lines = [
        f"# A 股每日策略机器人报告 - {run_date}",
        "",
        "## 今日摘要",
        "",
        f"- 本金：{cfg.get('capital', 50000)}",
        f"- 策略：{cfg.get('name', 'serenity-quant')}",
        "- 模式：研究辅助与调仓建议，最终买卖由 A 总确认。",
        "",
        "## 今日可买候选",
        "",
    ]
    if buy:
        lines.extend(_markdown_table(buy))
    else:
        lines.append("今日没有达到买入阈值的候选。")

    lines.extend(["", "## 观察 / 回避名单", ""])
    lines.extend(_markdown_table(watch[:10]) if watch else ["无。"])

    lines.extend(
        [
            "",
            "## 硬性风控规则",
            "",
            "- 首批总仓位不得超过配置上限。",
            "- 单票亏损达到 -8%：减半或停止加仓。",
            "- 单票亏损达到 -12%：清仓或重新评估。",
            "- 被标记为过热或一手金额过大的股票不追。",
        ]
    )
    return "\n".join(lines) + "\n"


def append_decision_memory(path: Path, rows: list[dict[str, Any]], run_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            payload = {
                "run_id": run_id,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "decision": row,
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _window_return(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    return float(close.iloc[-1] / close.iloc[-days - 1] - 1)


def _latest_value(df: pd.DataFrame, column: str) -> float | None:
    if column not in df.columns or df.empty:
        return None
    value = pd.to_numeric(df[column], errors="coerce").dropna()
    return float(value.iloc[-1]) if not value.empty else None


def _avg_value(df: pd.DataFrame, column: str, days: int) -> float | None:
    if column not in df.columns or df.empty:
        return None
    value = pd.to_numeric(df[column], errors="coerce").tail(days).dropna()
    return float(value.mean()) if not value.empty else None


def _trend_score(snapshot: MarketSnapshot, max_score: float) -> float:
    score = 0.0
    if snapshot.above_ma20:
        score += max_score * 0.35
    if snapshot.above_ma60:
        score += max_score * 0.25
    if snapshot.ret20 is not None:
        if 0 <= snapshot.ret20 <= 0.25:
            score += max_score * 0.25
        elif 0.25 < snapshot.ret20 <= 0.35:
            score += max_score * 0.12
    if snapshot.dd_high is not None and -0.25 <= snapshot.dd_high <= -0.05:
        score += max_score * 0.15
    return min(max_score, score)


def _liquidity_score(snapshot: MarketSnapshot, max_score: float) -> float:
    amount = snapshot.avg_amount20 or 0
    if amount >= 5_000_000_000:
        return max_score
    if amount >= 1_500_000_000:
        return max_score * 0.75
    if amount >= 500_000_000:
        return max_score * 0.5
    return max_score * 0.15


def _risk_penalty(
    item: dict[str, Any],
    snapshot: MarketSnapshot,
    cfg: dict[str, Any],
) -> tuple[float, list[str]]:
    max_penalty = float(cfg["weights"]["risk"])
    rules = cfg["risk_rules"]
    capital = float(cfg.get("capital", 50000))
    lot_size = int(cfg.get("lot_size", 100))
    notes: list[str] = []
    penalty = 0.0

    if is_star_market(item["code"]) and not cfg.get("allow_star_market", False):
        penalty += max_penalty * 0.7
        notes.append("star-market-permission")
    if is_chinext(item["code"]) and not cfg.get("allow_chinext", True):
        penalty += max_penalty * 0.5
        notes.append("chinext-permission")
    if snapshot.ret20 is not None and snapshot.ret20 > rules["hot_20d_return_pct"]:
        penalty += max_penalty * 0.6
        notes.append("20d-hot")
    if snapshot.pos_ytd is not None and snapshot.pos_ytd > rules["crowded_ytd_position"]:
        penalty += max_penalty * 0.35
        notes.append("near-ytd-high")
    if lot_amount(snapshot.close, lot_size) > capital * cfg.get("max_single_position_pct", 0.3):
        penalty += max_penalty * 0.6
        notes.append("one-lot-too-large")
    if snapshot.avg_amount20 is not None and snapshot.avg_amount20 < 500_000_000:
        penalty += max_penalty * 0.4
        notes.append("low-liquidity")
    return min(max_penalty, penalty), notes


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _markdown_table(rows: list[dict[str, Any]]) -> list[str]:
    headers = [
        "排名",
        "代码",
        "名称",
        "分数",
        "动作",
        "收盘价",
        "20日涨幅",
        "年内高点回撤",
        "建议股数",
        "风险标记",
    ]
    lines = ["| " + " | ".join(headers) + " |", "|---:|---|---|---:|---|---:|---:|---:|---:|---|"]
    for i, row in enumerate(rows, start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    row["code"],
                    row["name"],
                    f"{row['score']:.1f}",
                    localize_action(row["action"]),
                    f"{row['close']:.2f}",
                    _pct(row.get("ret20")),
                    _pct(row.get("dd_high")),
                    str(row.get("suggested_shares", 0)),
                    localize_risk_notes(row.get("risk_notes", "")),
                ]
            )
            + " |"
        )
    return lines


def localize_action(action: str) -> str:
    return ACTION_LABELS.get(action, action)


def localize_risk_notes(notes: str) -> str:
    if not notes or notes == "none":
        return RISK_NOTE_LABELS["none"]
    parts = [part.strip() for part in notes.split(";") if part.strip()]
    return "；".join(RISK_NOTE_LABELS.get(part, part) for part in parts)


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"
