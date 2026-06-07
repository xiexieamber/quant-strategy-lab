#!/usr/bin/env python3
"""
聚宽 JQData 拉取真实 A 股数据的演示脚本。

使用前:
    1. 在 https://www.joinquant.com 注册账号
    2. 申请 JQData 试用: https://www.joinquant.com/default/index/sdk
    3. 安装依赖: python3 -m pip install jqdatasdk
    4. 配置账号:
         cp .env.example .env   # 填入手机号和密码

运行:
    python3 scripts/test_jq_auth.py      # 先测登录
    python3 scripts/fetch_jqdata_demo.py # 再拉数据
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.fetch_jqdata import fetch_ohlcv_jq, get_jq_date_range


def main() -> None:
    symbol = "000001.XSHE"  # 平安银行
    allowed_start, allowed_end = get_jq_date_range()

    print("=" * 50)
    print("  聚宽 JQData 数据拉取演示")
    print("=" * 50)
    print(f"\n账号可获取范围: {allowed_start} ~ {allowed_end}")
    print(f"正在获取 {symbol} 的最新可用数据...\n")

    df = fetch_ohlcv_jq(symbol, start=allowed_start, end=allowed_end)

    print(f"共 {len(df)} 个交易日\n")
    print("最早 5 行:")
    print(df.head())
    print("\n最新 5 行:")
    print(df.tail())

    save_dir = ROOT / "data" / "raw"
    save_path = save_dir / f"{symbol.replace('.', '_')}.csv"
    fetch_ohlcv_jq(symbol, start=allowed_start, end=allowed_end, save_path=save_path)
    print(f"\n已保存到: {save_path}")
    print(f"最新收盘价: {df['Close'].iloc[-1]:.2f}（日期 {df.index[-1].date()}）")


if __name__ == "__main__":
    main()
