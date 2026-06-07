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
from src.data.ak_market import get_date_range_hint
from src.data.jq_cache import QuotaExhaustedError, get_jq_spare_quota
from src.strategies.small_cap.config import SmallCapConfig

st.set_page_config(
    page_title="小市值实验室",
    page_icon="📊",
    layout="wide",
)

st.title("📊 小市值策略实验室")
st.caption("本地回测 · 对应果仁 H1 / 模型 II · 不受 VIP 限制")

with st.sidebar:
    st.header("数据源")
    data_source = st.radio(
        "选择数据来源",
        options=["akshare", "jq"],
        format_func=lambda x: "AkShare（免费推荐）" if x == "akshare" else "聚宽 JQData",
        index=0,
    )

# --- 数据范围提示 ---
jq_start, jq_end, jq_ok = None, None, False
ak_start, ak_end = get_date_range_hint()

if data_source == "jq":
    try:
        from src.data.fetch_jqdata import get_jq_date_range

        jq_start, jq_end = get_jq_date_range()
        spare = get_jq_spare_quota()
        jq_ok = True
        st.info(
            f"**JQData** 可用数据：{jq_start} ~ {jq_end}  \n"
            f"今日剩余额度约 **{spare:,}** 条（每日 100 万）。已缓存数据不会重复下载。"
        )
    except Exception as e:
        st.error(f"聚宽登录失败：{e}\n\n请配置 `.env` 中的 `JQ_USERNAME` / `JQ_PASSWORD`，或改用 AkShare。")
else:
    st.success(
        f"**AkShare** 免费、无每日 100 万条限制。  \n"
        f"建议回测区间：约 **{ak_start}** ~ **{ak_end}**（可自定义更长，数据来自东方财富）。  \n"
        f"⚠️ **首次回测**需下载约 4000+ 只股票数据（约 30～60 分钟），之后本地缓存秒开。"
    )

default_start = pd.Timestamp(jq_start if jq_ok and jq_start else ak_start).date()
default_end = pd.Timestamp(jq_end if jq_ok and jq_end else ak_end).date()

with st.sidebar:
    st.header("策略参数")

    preset = st.selectbox(
        "预设方案",
        [
            "H1-Pro 防雷增强（说明书）",
            "H1 基础",
            "H1 + 1/4月空仓",
            "H1 + 中证1000择时",
            "自定义",
        ],
    )

    is_h1_pro_preset = preset == "H1-Pro 防雷增强（说明书）"

    start = st.date_input("开始日期", value=default_start)
    end = st.date_input("结束日期", value=default_end)

    st.subheader("排名权重")
    circ_w = st.number_input(
        "流通市值权重", min_value=0.0, max_value=10.0,
        value=2.0, step=0.5, disabled=is_h1_pro_preset,
    )
    total_w = st.number_input(
        "总市值权重", min_value=0.0, max_value=10.0,
        value=1.0, step=0.5, disabled=is_h1_pro_preset,
    )

    st.subheader("模型 II")
    buy_rank = st.slider("买入：排名 ≤", 1, 30, 8, disabled=is_h1_pro_preset)
    sell_rank = st.slider("卖出：排名 ≥", 1, 50, 15, disabled=is_h1_pro_preset)
    max_pos = st.slider("最多持股数", 1, 20, 10, disabled=is_h1_pro_preset)

    st.subheader("筛选")
    exclude_st = st.checkbox("排除 ST", value=True, disabled=is_h1_pro_preset)
    exclude_stib = st.checkbox("排除科创板(688)", value=True, disabled=is_h1_pro_preset)
    min_money = st.number_input(
        "最低成交额（万元，0=不限）", min_value=0,
        value=0 if not is_h1_pro_preset else 0,
        step=100, disabled=is_h1_pro_preset,
    ) * 10000
    profit_pos = st.checkbox(
        "扣非净利润 > 0",
        value=is_h1_pro_preset,
        disabled=is_h1_pro_preset,
        help="H1-Pro 默认开启；AkShare/JQ 均支持（Ak 为财务接口近似）",
    )

    st.subheader("风控")
    empty_14 = st.checkbox("1/4 月空仓", value=is_h1_pro_preset, disabled=is_h1_pro_preset)
    use_timing = st.checkbox(
        "中证1000 昨日收盘 MA20 择时", value=is_h1_pro_preset, disabled=is_h1_pro_preset,
    )
    stop_loss = st.slider(
        "止损 %（0=关闭）", 0, 30,
        12 if is_h1_pro_preset else 0,
        disabled=is_h1_pro_preset,
    ) / 100

    st.subheader("交易")
    trade_cost = st.number_input(
        "单边摩擦成本", min_value=0.0, max_value=0.01,
        value=0.003 if is_h1_pro_preset else 0.002,
        step=0.0005, format="%.4f", disabled=is_h1_pro_preset,
    )
    price_type = st.selectbox(
        "调仓价格", ["open", "close"],
        format_func=lambda x: "开盘价" if x == "open" else "收盘价",
        disabled=is_h1_pro_preset,
    )
    initial_cash = st.number_input(
        "初始资金（元）", min_value=10_000,
        value=100_000 if is_h1_pro_preset else 1_000_000,
        step=10_000, disabled=is_h1_pro_preset,
    )

    run_btn = st.button("🚀 开始回测", type="primary", use_container_width=True)

if run_btn:
    if data_source == "jq" and not jq_ok:
        st.stop()

    if preset == "H1-Pro 防雷增强（说明书）":
        cfg = SmallCapConfig.h1_pro(
            start=str(start),
            end=str(end),
            data_source=data_source,
            profit_positive=True,
        )
    else:
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
            data_source=data_source,
        )

    progress = st.progress(0, text="准备回测…")
    status = st.empty()

    def _cb(cur: int, total: int, day: pd.Timestamp, phase: str = "") -> None:
        if total <= 0:
            return
        progress.progress(min(cur / total, 1.0), text=f"{phase} ({cur}/{total})")

    cfg.progress_callback = _cb

    spinner = "正在从 AkShare 下载/读取数据…" if data_source == "akshare" else "正在拉取聚宽数据…"
    with st.spinner(spinner):
        try:
            result = run_small_cap_backtest(cfg)
        except QuotaExhaustedError as ex:
            st.warning(str(ex))
            st.info(
                "💡 **JQData 额度不够时**：\n"
                "1. 明天再点「开始回测」（缓存续拉）\n"
                "2. 或改用左侧 **AkShare** 数据源（推荐）"
            )
            st.stop()
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

    1. 左侧选 **AkShare**（推荐，无额度限制）或 **JQData**
    2. 选 **预设方案** 或自己调参数
    3. 点 **开始回测**

    ### 数据源对比

    | | AkShare | JQData |
    |--|---------|--------|
    | 费用 | 免费 | 试用免费 |
    | 每日额度 | 无硬限制 | 100 万条 |
    | 历史长度 | 较长 | 试用约 1 年 |
    | 扣非净利润筛选 | ❌ | ✅ |
    | 首次速度 | 较慢（需缓存） | 中等 |

    > 历史回测不代表未来收益，仅供学习研究。
    """)
