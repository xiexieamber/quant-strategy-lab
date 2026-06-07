"""
从聚宽 JQData 下载 A 股等真实行情数据（需注册并申请试用）。

用法示例:
    from src.data.fetch_jqdata import fetch_ohlcv_jq

    df = fetch_ohlcv_jq("000001.XSHE", start="2020-01-01", end="2024-12-31")

账号配置（二选一）:
    1. 环境变量: JQ_USERNAME / JQ_PASSWORD
    2. 直接传参: fetch_ohlcv_jq(..., username="手机号", password="密码")

申请试用: https://www.joinquant.com/default/index/sdk
文档: https://www.joinquant.com/help/api/help?name=JQData
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_dotenv() -> None:
    """从项目根目录的 .env 文件加载 JQ_USERNAME / JQ_PASSWORD。"""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key in ("JQ_USERNAME", "JQ_PASSWORD") and value:
            os.environ.setdefault(key, value)

# 常见 A 股代码对照（聚宽格式）
SYMBOL_HINTS = {
    "平安银行": "000001.XSHE",
    "贵州茅台": "600519.XSHG",
    "招商银行": "600036.XSHG",
}


def _ensure_auth(username: str | None = None, password: str | None = None) -> None:
    """登录聚宽 JQData，重复调用是安全的。"""
    _load_dotenv()
    try:
        import jqdatasdk as jq
    except ImportError as e:
        raise ImportError(
            "请先安装 jqdatasdk: python3 -m pip install jqdatasdk"
        ) from e

    user = username or os.environ.get("JQ_USERNAME")
    pwd = password or os.environ.get("JQ_PASSWORD")

    if not user or not pwd:
        raise ValueError(
            "缺少聚宽账号。请设置环境变量 JQ_USERNAME / JQ_PASSWORD，"
            "或在函数中传入 username / password。"
            "申请试用: https://www.joinquant.com/default/index/sdk"
        )

    jq.auth(user, pwd)
    remaining = jq.get_query_count()
    if remaining.get("total", 0) <= 0:
        raise RuntimeError(
            "JQData 查询额度已用完，请到聚宽官网查看账号状态或升级套餐。"
        )


def get_jq_date_range(username: str | None = None, password: str | None = None) -> tuple[str, str]:
    """
    查询当前账号允许获取的数据日期范围（试用账号通常只有近 1 年）。

    返回:
        (start, end)，格式 "YYYY-MM-DD"
    """
    import jqdatasdk as jq

    _ensure_auth(username, password)
    info = jq.get_account_info()
    start = info["date_range_start"][:10]
    end = info["date_range_end"][:10]
    return start, end


def _clamp_date_range(
    start: str,
    end: str | None,
    username: str | None = None,
    password: str | None = None,
) -> tuple[str, str]:
    """把请求日期限制在账号权限范围内。"""
    allowed_start, allowed_end = get_jq_date_range(username, password)
    req_end = end or allowed_end
    clamped_start = max(start, allowed_start)
    clamped_end = min(req_end, allowed_end)
    if clamped_start > clamped_end:
        raise ValueError(
            f"请求的日期 {start} ~ {req_end} 超出账号权限范围 "
            f"({allowed_start} ~ {allowed_end})。"
            f"试用账号只能获取该区间内的数据。"
        )
    return clamped_start, clamped_end


def fetch_ohlcv_jq(
    symbol: str,
    start: str = "2020-01-01",
    end: str | None = None,
    save_path: Path | None = None,
    username: str | None = None,
    password: str | None = None,
) -> pd.DataFrame:
    """
    从聚宽 JQData 下载 OHLCV 数据，返回格式与 fetch_ohlcv() 一致。

    参数:
        symbol: 聚宽代码，如 "000001.XSHE"（平安银行）、"600519.XSHG"（茅台）
        start: 开始日期 "YYYY-MM-DD"
        end: 结束日期，默认到今天
        save_path: 可选，保存 CSV 路径
        username / password: 聚宽账号（也可用环境变量）

    返回:
        包含 Open, High, Low, Close, Volume 列的 DataFrame
    """
    import jqdatasdk as jq

    _ensure_auth(username, password)

    start, end = _clamp_date_range(start, end, username, password)

    raw = jq.get_price(
        symbol,
        start_date=start,
        end_date=end,
        frequency="daily",
        fields=["open", "high", "low", "close", "volume"],
        skip_paused=False,
        fq="pre",  # 前复权，回测常用
    )

    if raw is None or raw.empty:
        raise ValueError(
            f"未能获取 {symbol} 的数据。请检查：\n"
            f"  1. 代码格式是否正确（A 股用 000001.XSHE / 600519.XSHG）\n"
            f"  2. 日期范围是否有交易日\n"
            f"  3. JQData 账号是否有效"
        )

    df = raw.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    df.index = pd.to_datetime(df.index)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(save_path)

    return df


def list_all_stocks() -> pd.DataFrame:
    """获取全部 A 股列表（代码、名称、上市日期等）。"""
    import jqdatasdk as jq

    _ensure_auth()
    return jq.get_all_securities(types=["stock"])
