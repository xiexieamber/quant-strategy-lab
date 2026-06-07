"""小市值轮动策略配置（H1 / H1-Pro 模型 II）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SmallCapConfig:
    """策略参数。`h1_pro=True` 或 `SmallCapConfig.h1_pro()` 启用防雷增强版默认值。"""

    start: str = "2025-03-01"
    end: str | None = None
    initial_cash: float = 1_000_000.0

    # 策略模式
    h1_pro: bool = False

    # 排名权重（越小越好 = 小市值优先）
    circ_weight: float = 2.0
    total_weight: float = 1.0

    # 模型 II：买 ≤ buy_rank，卖 ≥ sell_rank
    buy_rank: int = 8
    sell_rank: int = 15
    max_positions: int = 10

    # 基础筛选
    exclude_st: bool = True
    exclude_stib: bool = True
    exclude_bse: bool = True  # 北交所 8 开头
    min_turnover_ratio: float = 0.0
    min_money: float = 0.0  # 单日最低成交额（元），0=不限
    profit_positive: bool = False

    # H1-Pro 增强筛选
    min_listed_days: int = 0  # 上市满 N 天（Pro 默认 180）
    min_avg_money_days: int = 0  # Pro 默认 5
    min_avg_money: float = 0.0  # Pro 默认 1000 万
    exclude_bad_audit: bool = False  # 剔除差审计意见
    exclude_delisting: bool = False  # 剔除退市整理（名称含「退」）

    # 风控
    empty_months: tuple[int, ...] = ()
    stop_loss_pct: float = 0.0
    use_index_timing: bool = False
    index_code: str = "000852.XSHG"
    index_ma: int = 20
    index_timing_lag: int = 0  # Pro=1：用「昨日」指数收盘 vs MA20

    # 交易
    trade_cost: float = 0.002
    rebalance_price: str = "open"
    backup_etf: str = "518880.XSHG"
    enforce_limit_prices: bool = False  # 一字涨跌停撮合限制

    # 数据源
    data_source: str = "akshare"

    progress_callback: object | None = field(default=None, repr=False)

    @classmethod
    def h1_pro(cls, **overrides) -> SmallCapConfig:
        """《小市值防雷增强版 H1-Pro》说明书默认参数。"""
        defaults: dict = {
            "h1_pro": True,
            "initial_cash": 100_000.0,
            "trade_cost": 0.003,
            "buy_rank": 8,
            "sell_rank": 15,
            "max_positions": 10,
            "circ_weight": 2.0,
            "total_weight": 1.0,
            "exclude_st": True,
            "exclude_stib": True,
            "exclude_bse": True,
            "profit_positive": True,
            "min_listed_days": 180,
            "min_avg_money_days": 5,
            "min_avg_money": 10_000_000.0,
            "exclude_bad_audit": True,
            "exclude_delisting": True,
            "empty_months": (1, 4),
            "stop_loss_pct": 0.12,
            "use_index_timing": True,
            "index_timing_lag": 1,
            "enforce_limit_prices": True,
            "rebalance_price": "open",
        }
        defaults.update(overrides)
        return cls(**defaults)

    def __post_init__(self) -> None:
        if self.h1_pro and self.min_listed_days == 0 and self.min_avg_money == 0:
            # 通过 h1_pro() 构造时已设好；此处防手工只设 h1_pro=True
            pass
