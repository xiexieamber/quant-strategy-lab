#!/usr/bin/env python3
"""
测试聚宽 JQData 账号是否配置正确。

运行:
    python3 scripts/test_jq_auth.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.fetch_jqdata import _ensure_auth, get_jq_date_range


def main() -> None:
    print("正在测试聚宽 JQData 登录...")
    _ensure_auth()

    import jqdatasdk as jq

    quota = jq.get_query_count()
    allowed_start, allowed_end = get_jq_date_range()
    print("✅ 登录成功！")
    print(f"   今日剩余查询次数: {quota.get('spare', '?')} / {quota.get('total', '?')}")
    print(f"   账号可获取数据范围: {allowed_start} ~ {allowed_end}")

    # 拉取权限范围内最后 10 个交易日
    df = jq.get_price("000001.XSHE", end_date=allowed_end, count=10)
    print(f"\n✅ 数据拉取成功！平安银行最新 {len(df)} 条")
    print(df[["open", "close", "volume"]])


if __name__ == "__main__":
    main()
