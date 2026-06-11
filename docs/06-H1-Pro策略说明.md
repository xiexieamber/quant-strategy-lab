# H1-Pro 防雷增强版（策略说明书实现）

对应《小市值防雷增强版 H1-Pro》完整逻辑，代码入口：

- 配置：`src/strategies/small_cap/config.py` → `SmallCapConfig.for_h1_pro()`
- 过滤：`src/strategies/small_cap/universe_filter.py`
- 回测：`src/backtest/small_cap_engine.py`
- 界面：预设 **「H1-Pro 防雷增强（说明书）」**

## 说明书参数对照

| 说明书 | 代码字段 | 默认值 |
|--------|----------|--------|
| 初始资金 10 万 | `initial_cash` | 100_000 |
| 最多 10 只 | `max_positions` | 10 |
| 单边摩擦 0.3% | `trade_cost` | 0.003 |
| 买 ≤8 / 卖 ≥15 | `buy_rank` / `sell_rank` | 8 / 15 |
| 1/4 月空仓 | `empty_months` | (1, 4) |
| 昨日中证1000 < MA20 | `index_timing_lag=1` | ✅ |
| 止损 12% | `stop_loss_pct` | 0.12 |
| 上市 ≥180 天 | `min_listed_days` | 180 |
| 5 日均成交额 ≥1000 万 | `min_avg_money` / `min_avg_money_days` | 1e7 / 5 |
| 扣非净利润 > 0 | `profit_positive` | True |
| 差审计意见剔除 | `exclude_bad_audit` | True |
| 退市整理剔除 | `exclude_delisting` | True |
| 涨跌停撮合 | `enforce_limit_prices` | True |

## 命令行回测

```python
from src.strategies.small_cap.config import SmallCapConfig
from src.backtest.small_cap_engine import run_small_cap_backtest

cfg = SmallCapConfig.for_h1_pro(
    start="2025-01-01",
    end="2025-06-01",
    data_source="akshare",  # 或 "jq"
)
result = run_small_cap_backtest(cfg)
print(result.annual_return, result.max_drawdown)
```

## 数据源差异

| 过滤项 | JQData | AkShare |
|--------|--------|---------|
| ST/688/停牌 | ✅ | ✅ |
| 上市 180 天 | ✅ `get_all_securities` | ✅ `stock_individual_info_em` |
| 5 日均成交额 | ✅ `get_price.money` | ✅ 历史 `成交额` |
| 扣非净利润 | ✅ `indicator.adjusted_profit` | ⚠️ 财务指标近似 |
| 审计意见 | ✅ `STK_AUDIT_OPINION` | ❌（仅名称含「退」） |
| 涨跌停价 | ✅ `high_limit/low_limit` | ⚠️ 无字段时不生效 |

> AkShare 首次全市场下载较慢，缓存后复用。历史回测仅供学习研究。
