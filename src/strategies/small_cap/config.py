"""小市值轮动策略配置（对应果仁 H1 / 模型 II）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SmallCapConfig:
    """策略参数，默认值对齐 configs/guorn/base_payload.json 中的 H1。"""

    start: str = "2025-03-01"
    end: str | None = None
    initial_cash: float = 1_000_000.0

    # 排名权重（越小越好 = 小市值优先）
    circ_weight: float = 2.0
    total_weight: float = 1.0

    # 模型 II：买 ≤ buy_rank，卖 ≥ sell_rank
    buy_rank: int = 8
    sell_rank: int = 15
    max_positions: int = 10

    # 筛选
    exclude_st: bool = True
    exclude_stib: bool = True  # 科创板 688
    min_turnover_ratio: float = 0.0  # 日换手率下限（%），0 表示不限制
    min_money: float = 0.0  # 最低成交额（元），0 表示不限制
    profit_positive: bool = False  # 扣非净利润 > 0

    # 风控
    empty_months: tuple[int, ...] = ()  # 如 (1, 4) 表示 1/4 月空仓
    stop_loss_pct: float = 0.0  # 如 0.12 表示亏损 12% 止损，0 表示关闭
    use_index_timing: bool = False
    index_code: str = "000852.XSHG"  # 中证1000
    index_ma: int = 20

    # 交易
    trade_cost: float = 0.002  # 单边费率
    rebalance_price: str = "open"  # open 或 close
    backup_etf: str = "518880.XSHG"  # 空仓时持有黄金 ETF

    # 运行控制
    progress_callback: object | None = field(default=None, repr=False)
