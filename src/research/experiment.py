"""Experiment helpers for append-only strategy research outputs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.small_cap_engine import SmallCapBacktestResult
from src.strategies.small_cap.config import SmallCapConfig


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = ROOT / "results" / "experiments"


def load_json_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def small_cap_config_from_dict(raw: dict[str, Any]) -> SmallCapConfig:
    """Build a SmallCapConfig from a JSON-friendly experiment config."""
    preset = raw.get("preset", "h1_pro")
    overrides = dict(raw.get("overrides") or {})

    if "empty_months" in overrides and isinstance(overrides["empty_months"], list):
        overrides["empty_months"] = tuple(overrides["empty_months"])

    if preset == "h1_pro":
        return SmallCapConfig.for_h1_pro(**overrides)
    if preset == "guorn_h1":
        return SmallCapConfig.for_guorn_h1(**overrides)
    if preset == "custom":
        return SmallCapConfig(**overrides)
    raise ValueError(f"Unknown small-cap preset: {preset}")


def config_to_jsonable(cfg: SmallCapConfig) -> dict[str, Any]:
    data = asdict(cfg)
    data.pop("progress_callback", None)
    if isinstance(data.get("empty_months"), tuple):
        data["empty_months"] = list(data["empty_months"])
    return data


def git_revision(root: Path = ROOT) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


def make_experiment_dir(
    strategy_name: str,
    experiment_name: str,
    root: Path = EXPERIMENT_ROOT,
) -> Path:
    safe_name = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-"
        for ch in experiment_name.strip().lower()
    ).strip("-")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = root / f"{timestamp}-{strategy_name}-{safe_name or 'experiment'}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def result_metrics(result: SmallCapBacktestResult) -> dict[str, Any]:
    return {
        "start": result.start,
        "end": result.end,
        "total_return": result.total_return,
        "annual_return": result.annual_return,
        "max_drawdown": result.max_drawdown,
        "sharpe_ratio": result.sharpe_ratio,
        "trade_count": result.trade_count,
        "final_equity": float(result.equity_curve.iloc[-1])
        if not result.equity_curve.empty
        else None,
    }


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_small_cap_experiment(
    *,
    output_dir: Path,
    request_config: dict[str, Any],
    resolved_config: SmallCapConfig,
    result: SmallCapBacktestResult,
    notes: str = "",
) -> dict[str, Path]:
    """Persist config, metrics, logs, and a Markdown report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = result_metrics(result)
    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "strategy_name": "small_cap",
        "git_revision": git_revision(),
        "notes": notes,
    }

    config_payload = {
        "request": request_config,
        "resolved": config_to_jsonable(resolved_config),
        "metadata": metadata,
    }

    files = {
        "config": output_dir / "config.json",
        "metrics": output_dir / "metrics.json",
        "yearly_returns": output_dir / "yearly_returns.csv",
        "holdings_log": output_dir / "holdings_log.csv",
        "report": output_dir / "report.md",
    }

    write_json(files["config"], config_payload)
    write_json(files["metrics"], metrics)
    result.yearly_returns.to_csv(files["yearly_returns"], index=False)
    result.holdings_log.to_csv(files["holdings_log"], index=False)
    files["report"].write_text(
        render_small_cap_report(
            metadata=metadata,
            config=resolved_config,
            metrics=metrics,
            yearly_returns=result.yearly_returns,
        ),
        encoding="utf-8",
    )
    return files


def render_small_cap_report(
    *,
    metadata: dict[str, Any],
    config: SmallCapConfig,
    metrics: dict[str, Any],
    yearly_returns: pd.DataFrame,
) -> str:
    years = _yearly_returns_markdown(yearly_returns)
    cfg = config_to_jsonable(config)
    return f"""# Small Cap Experiment Report

Created at: {metadata.get("created_at")}

Git revision: {metadata.get("git_revision") or "unknown"}

Notes: {metadata.get("notes") or "-"}

## Metrics

| Metric | Value |
| --- | ---: |
| Start | {metrics["start"]} |
| End | {metrics["end"]} |
| Total return | {metrics["total_return"]:.4f} |
| Annual return | {metrics["annual_return"]:.4f} |
| Max drawdown | {metrics["max_drawdown"]:.4f} |
| Sharpe ratio | {metrics["sharpe_ratio"]:.4f} |
| Trade count | {metrics["trade_count"]} |
| Final equity | {metrics["final_equity"]:.2f} |

## Yearly Returns

{years}

## Key Config

```json
{json.dumps(cfg, ensure_ascii=False, indent=2)}
```

## Audit Notes

- This report is a reproducible experiment record, not a trading recommendation.
- Compare this result with validation/test splits before changing real capital allocation.
- Do not select parameters only by this single report.
"""


def _yearly_returns_markdown(yearly_returns: pd.DataFrame) -> str:
    if yearly_returns.empty:
        return "No yearly data"

    rows = ["| Year | Return |", "| --- | ---: |"]
    for _, row in yearly_returns.iterrows():
        rows.append(f"| {int(row['year'])} | {float(row['return']):.4f} |")
    return "\n".join(rows)


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    if not is_dataclass(value):
        raise TypeError("Expected a dataclass instance")
    return asdict(value)
