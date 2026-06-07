"""
本地小市值实验室 — 果仁 H1 风格的 Streamlit 界面。

启动:
    streamlit run apps/small_cap_lab/app.py
或:
    python scripts/run_small_cap_lab.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from src.backtest.small_cap_engine import run_small_cap_backtest
from src.data.fetch_jqdata import get_jq_date_range
from src.strategies.small_cap.config import SmallCapConfig

st.set_page_config(
    page_title="小市值实验室",
    page_icon="📊",
    layout="wide",
)

st.title("📊 小市值策略实验室")
st.caption("基于聚宽 JQData 本地回测 · 对应果仁 H1 / 模型 II · 不受 VIP 限制")

# --- 账号数据范围 ---
try:
    jq_start, jq_end = get_jq_date_range()
    st.info(f"当前 JQData 账号可用数据：**{jq_start}** ~ **{jq_end}**（试用账号通常约 1 年）")
except Exception as e:
    st.error(f"聚宽登录失败：{e}\n\n请在项目根目录 `.env` 中配置 `JQ_USERNAME` 和 `JQ_PASSWORD`。")
    st.stop()

with st.sidebar:
    st.header("策略参数")

    preset = st.selectbox(
        "预设方案",
        ["H1 基础", "H1 + 1/4月空仓", "H1 + 中证1000择时", "H1 Pro（全风控）", "自定义"],
    )

    start = st.date_input("开始日期", value=pd.Timestamp(jq_start).date())
    end = st.date_input("结束日期", value=pd.Timestamp(jq_end).date())

    st.subheader("排名权重")
    circ_w = st.number_input("流通市值权重", min_value=0.0, max_value=10.0, value=2.0, step=0.5)
    total_w = st.number_input("总市值权重", min_value=0.0, max_value=10.0, value=1.0, step=0.5)

    st.subheader("模型 II")
    buy_rank = st.slider("买入：排名 ≤", 1, 30, 8)
    sell_rank = st.slider("卖出：排名 ≥", 1, 50, 15)
    max_pos = st.slider("最多持股数", 1, 20, 10)

    st.subheader("筛选")
    exclude_st = st.checkbox("排除 ST", value=True)
    exclude_stib = st.checkbox("排除科创板(688)", value=True)
    min_money = st.number_input("最低成交额（万元，0=不限）", min_value=0, value=0, step=100) * 10000
    profit_pos = st.checkbox("扣非净利润 > 0", value=False)

    st.subheader("风控（本地免费实现）")
    empty_14 = st.checkbox("1/4 月空仓", value=False)
    use_timing = st.checkbox("中证1000 MA20 择时", value=False)
    stop_loss = st.slider("止损 %（0=关闭）", 0, 30, 0) / 100

    st.subheader("交易")
    trade_cost = st.number_input("单边交易成本", min_value=0.0, max_value=0.01, value=0.002, step=0.0005, format="%.4f")
    price_type = st.selectbox("调仓价格", ["open", "close"], format_func=lambda x: "开盘价" if x == "open" else "收盘价")
    initial_cash = st.number_input("初始资金（元）", min_value=100_000, value=1_000_000, step=100_000)

    run_btn = st.button("🚀 开始回测", type="primary", use_container_width=True)

if run_btn:
    # 按预设覆盖部分参数
    p_empty_months: tuple[int, ...] = ()
    p_use_timing = use_timing
    p_stop_loss = stop_loss
    p_profit_pos = profit_pos
    p_min_money = float(min_money)

    if preset == "H1 基础":
        p_empty_months = ()
        p_use_timing = False
        p_stop_loss = 0.0
    elif preset == "H1 + 1/4月空仓":
        p_empty_months = (1, 4)
    elif preset == "H1 + 中证1000择时":
        p_use_timing = True
    elif preset == "H1 Pro（全风控）":
        p_empty_months = (1, 4)
        p_use_timing = True
        p_stop_loss = 0.12
        p_profit_pos = True
        p_min_money = 5_000_000.0
    else:
        p_empty_months = (1, 4) if empty_14 else ()

    cfg = SmallCapConfig(
        start=str(start),
        end=str(end),
        initial_cash=float(initial_cash),
        circ_weight=circ_w,
        total_weight=total_w,
        buy_rank=buy_rank,
        sell_rank=sell_rank,
        max_positions=max_pos,
        exclude_st=exclude_st,
        exclude_stib=exclude_stib,
        min_money=p_min_money,
        profit_positive=p_profit_pos,
        empty_months=p_empty_months,
        stop_loss_pct=p_stop_loss,
        use_index_timing=p_use_timing,
        trade_cost=trade_cost,
        rebalance_price=price_type,
    )

    progress = st.progress(0, text="准备回测…")
    status = st.empty()

    def _cb(cur: int, total: int, day: pd.Timestamp) -> None:
        progress.progress(cur / total, text=f"回测中 {day.strftime('%Y-%m-%d')} ({cur}/{total})")

    cfg.progress_callback = _cb

    with st.spinner("正在拉取聚宽数据并模拟交易，请稍候…"):
        try:
            result = run_small_cap_backtest(cfg)
        except Exception as ex:
            st.error(f"回测失败：{ex}")
            st.stop()

    progress.empty()
    status.success(f"回测完成：{result.start} ~ {result.end}，共 {result.trade_count} 笔交易")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("年化收益", f"{result.annual_return * 100:.2f}%")
    c2.metric("总收益", f"{result.total_return * 100:.2f}%")
    c3.metric("最大回撤", f"{result.max_drawdown * 100:.2f}%")
    c4.metric("夏普比率", f"{result.sharpe_ratio:.2f}")

    st.subheader("资金曲线")
    chart_df = pd.DataFrame(
        {
            "策略": result.equity_curve,
            "中证1000": result.benchmark_curve,
        }
    )
    st.line_chart(chart_df, use_container_width=True)

    if not result.yearly_returns.empty:
        st.subheader("年度收益")
        yr = result.yearly_returns.copy()
        yr["return_pct"] = yr["return"].map(lambda x: f"{x * 100:.2f}%")
        st.dataframe(yr[["year", "return_pct"]], use_container_width=True, hide_index=True)

    with st.expander("每日持仓记录（最近 20 天）"):
        log = result.holdings_log.tail(20).copy()
        log["date"] = log["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(log, use_container_width=True, hide_index=True)

else:
    st.markdown("""
    ### 怎么用？

    1. 左侧选 **预设方案** 或自己调参数（和果仁网页类似）
    2. 点 **开始回测**
    3. 看年化、回撤、资金曲线

    ### 和果仁的区别

    | 功能 | 果仁 | 本地实验室 |
    |------|------|-----------|
    | 小市值排名 + 模型II | ✅ | ✅ |
    | 1/4 月空仓 | VIP/公式 | ✅ 免费 |
    | 中证1000 MA20 择时 | VIP | ✅ 免费 |
    | 成交额/净利润筛选 | 部分 VIP | ✅ 免费 |
    | 数据长度 | 多年 | 试用约 1 年 |
    | 预期ST/审计意见 | ✅ | ❌ 暂未实现 |

    > 历史回测不代表未来收益，仅供学习研究。
    """)
