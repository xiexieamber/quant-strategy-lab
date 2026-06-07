"""从果仁 runtest 响应 JSON 中提取回测指标并排名。"""

from __future__ import annotations

from typing import Any


def _sheet_value(sheet: dict[str, Any], row: int, col: int = 0) -> float | None:
    """读取 sheet_data.meas_data[row][col]，跳过 '-' 等非数字。"""
    try:
        value = sheet["meas_data"][row][col]
    except (KeyError, IndexError, TypeError):
        return None
    if value in ("-", "", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _year_return(yearly_sheet: dict[str, Any], year: int) -> float | None:
    """从 yearly_statistics 读取某年策略收益（小数，如 0.26 = 26%）。"""
    try:
        years = yearly_sheet["row"][0]["data"][0]
        returns = yearly_sheet["meas_data"][0]
        idx = years.index(year)
        return float(returns[idx])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def extract_metrics(response: dict[str, Any], year_focus: int = 2024) -> dict[str, Any]:
    """
    解析 runtest 响应，提取本策略（非基准）的核心指标。

    果仁返回的小数含义：0.412349 = 41.23% 收益，0.519937 = 51.99% 回撤。
    """
    if response.get("status") != "ok":
        return {
            "ok": False,
            "error": str(response.get("data", "unknown error")),
        }

    data = response.get("data") or {}
    summary_sheet = (data.get("summary") or {}).get("sheet_data")
    yearly_sheet = (data.get("yearly_statistics") or {}).get("sheet_data")

    if not summary_sheet:
        return {"ok": False, "error": "响应缺少 data.summary.sheet_data"}

    total_return = _sheet_value(summary_sheet, 0, 0)
    annual_return = _sheet_value(summary_sheet, 1, 0)
    sharpe_ratio = _sheet_value(summary_sheet, 2, 0)
    max_drawdown = _sheet_value(summary_sheet, 3, 0)
    volatility = _sheet_value(summary_sheet, 4, 0)

    year_return = _year_return(yearly_sheet, year_focus) if yearly_sheet else None

    return {
        "ok": True,
        "total_return": total_return,
        "annual_return": annual_return,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "volatility": volatility,
        f"year_{year_focus}_return": year_return,
        "total_return_pct": _pct(total_return),
        "annual_return_pct": _pct(annual_return),
        "max_drawdown_pct": _pct(max_drawdown),
        f"year_{year_focus}_return_pct": _pct(year_return),
    }


def _pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100, 2)


def _rank_score(values: list[float | None], higher_is_better: bool) -> list[float]:
    """把一组数值转成 0~100 的排名分。"""
    valid = [(i, v) for i, v in enumerate(values) if v is not None]
    if not valid:
        return [0.0] * len(values)

    n = len(valid)
    sorted_items = sorted(
        valid,
        key=lambda x: x[1],
        reverse=higher_is_better,
    )
    scores = [0.0] * len(values)
    for rank, (idx, _) in enumerate(sorted_items, start=1):
        scores[idx] = (n - rank + 1) / n * 100
    return scores


def rank_results(
    results: list[dict[str, Any]],
    *,
    year_key: str = "year_2024_return",
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """
    按 docs/03-果仁策略B调参指南.md 的综合分公式排名。

    默认权重：年化 30%、夏普 25%、回撤 30%、2024 15%。
    """
    if weights is None:
        weights = {
            "annual_return": 0.30,
            "sharpe_ratio": 0.25,
            "max_drawdown": 0.30,
            year_key: 0.15,
        }

    ok_results = [r for r in results if r.get("ok")]
    if not ok_results:
        return results

    annual_scores = _rank_score(
        [r.get("annual_return") for r in ok_results],
        higher_is_better=True,
    )
    sharpe_scores = _rank_score(
        [r.get("sharpe_ratio") for r in ok_results],
        higher_is_better=True,
    )
    # 回撤越小越好
    drawdown_scores = _rank_score(
        [r.get("max_drawdown") for r in ok_results],
        higher_is_better=False,
    )
    year_scores = _rank_score(
        [r.get(year_key) for r in ok_results],
        higher_is_better=True,
    )

    ranked: list[dict[str, Any]] = []
    for i, item in enumerate(ok_results):
        composite = (
            annual_scores[i] * weights["annual_return"]
            + sharpe_scores[i] * weights["sharpe_ratio"]
            + drawdown_scores[i] * weights["max_drawdown"]
            + year_scores[i] * weights[year_key]
        )
        ranked.append(
            {
                **item,
                "composite_score": round(composite, 2),
                "rank_annual": round(annual_scores[i], 1),
                "rank_sharpe": round(sharpe_scores[i], 1),
                "rank_drawdown": round(drawdown_scores[i], 1),
                "rank_year": round(year_scores[i], 1),
            }
        )

    ranked.sort(
        key=lambda x: (x.get("composite_score", 0), x.get("sharpe_ratio") or 0),
        reverse=True,
    )

    failed = [r for r in results if not r.get("ok")]
    return ranked + failed
