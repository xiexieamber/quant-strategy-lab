#!/usr/bin/env python3
"""
果仁网批量调参回测脚本（非官方内部接口）。

使用前准备
----------
1. 在 Chrome 打开 https://guorn.com/stock?category=stock ，登录并配置好策略
2. F12 → Network → 点「开始回测」→ 选中 runtest 请求
3. Payload 标签 → 右键 Copy value → 保存为 configs/guorn/base_payload.json
4. Request Headers 里复制 Cookie 和 _xsrf 到 .env：
     GUORN_COOKIE=...
     GUORN_XSRF_TOKEN=...

常用命令
--------
# 仅测试能否解析你提供的回测响应
python scripts/guorn_batch_backtest.py --parse-only configs/guorn/sample_response.json

# 预览将要跑哪些参数组合（不发请求）
python scripts/guorn_batch_backtest.py --dry-run

# 跑默认 10 组参数（不含需单独 payload 的 A2）
python scripts/guorn_batch_backtest.py

# 只跑指定编号
python scripts/guorn_batch_backtest.py --variants A1,B1,C1

# 每次请求间隔 3 秒
python scripts/guorn_batch_backtest.py --sleep 3

注意：这是网页内部接口，Cookie 会过期；请小规模使用，勿高频请求。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.guorn.client import run_backtest
from src.guorn.metrics import extract_metrics, rank_results

CONFIG_DIR = ROOT / "configs" / "guorn"
DEFAULT_PAYLOAD = CONFIG_DIR / "base_payload.json"
DEFAULT_VARIANTS = CONFIG_DIR / "variants.json"
RESULTS_DIR = ROOT / "results" / "guorn"


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def set_nested(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    """按 a.b.0.c 路径写入嵌套 dict / list。"""
    keys = dotted_key.split(".")
    cur: Any = data
    for key in keys[:-1]:
        if key.isdigit():
            idx = int(key)
            if not isinstance(cur, list) or idx >= len(cur):
                raise KeyError(f"无法写入路径 {dotted_key}: 列表索引 {idx} 无效")
            cur = cur[idx]
            continue
        if key not in cur:
            nxt = keys[keys.index(key) + 1]
            cur[key] = [] if nxt.isdigit() else {}
        elif isinstance(cur[key], list):
            pass
        elif not isinstance(cur[key], dict):
            cur[key] = {}
        cur = cur[key]
    last = keys[-1]
    if last.isdigit():
        cur[int(last)] = value
    else:
        cur[last] = value


def apply_patches(payload: dict[str, Any], patches: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(payload)
    for key, value in patches.items():
        if "." in key:
            set_nested(body, key, value)
        else:
            body[key] = value
    return body


def append_list_items(payload: dict[str, Any], dotted_path: str, items: list[Any]) -> None:
    """向嵌套 list 追加元素，如 trading_strategy.buy_options。"""
    keys = dotted_path.split(".")
    cur: Any = payload
    for key in keys:
        if key.isdigit():
            cur = cur[int(key)]
        else:
            cur = cur[key]
    if not isinstance(cur, list):
        raise TypeError(f"{dotted_path} 不是 list")
    cur.extend(copy.deepcopy(items))


def build_payload(
    base_payload: dict[str, Any],
    variant: dict[str, Any],
    config_dir: Path,
) -> dict[str, Any] | None:
    payload_file = variant.get("payload_file")
    if payload_file:
        override_path = config_dir / payload_file
        if not override_path.exists():
            return None
        payload = load_json(override_path)
    else:
        payload = copy.deepcopy(base_payload)

    patches = variant.get("patches") or {}
    if patches:
        payload = apply_patches(payload, patches)

    list_appends = variant.get("list_appends") or {}
    for path, items in list_appends.items():
        append_list_items(payload, path, items)

    return payload


def load_variants(path: Path) -> list[dict[str, Any]]:
    variants = load_json(path)
    return [v for v in variants if isinstance(v, dict) and v.get("id")]


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def print_rank_table(ranked: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 100)
    print(f"{'排名':<4} {'编号':<6} {'名称':<18} {'年化':>8} {'夏普':>6} {'回撤':>8} {'2024':>8} {'综合分':>7}")
    print("-" * 100)
    for i, row in enumerate(ranked, start=1):
        if not row.get("ok"):
            print(f"{i:<4} {row.get('variant_id','?'):<6} {row.get('variant_name','?'):<18} 失败: {row.get('error')}")
            continue
        print(
            f"{i:<4} {row['variant_id']:<6} {row['variant_name']:<18} "
            f"{fmt_pct(row.get('annual_return')):>8} "
            f"{row.get('sharpe_ratio') or 0:>6.2f} "
            f"{fmt_pct(row.get('max_drawdown')):>8} "
            f"{fmt_pct(row.get('year_2024_return')):>8} "
            f"{row.get('composite_score', 0):>7.1f}"
        )
    print("=" * 100)


def save_results(ranked: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    slim = []
    for row in ranked:
        slim.append({k: v for k, v in row.items() if k != "raw_response"})
    output_path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="果仁网批量调参回测")
    parser.add_argument(
        "--payload",
        type=Path,
        default=DEFAULT_PAYLOAD,
        help=f"基础请求 JSON（默认 {DEFAULT_PAYLOAD.relative_to(ROOT)}）",
    )
    parser.add_argument(
        "--variants",
        type=str,
        default="",
        help="只跑指定编号，逗号分隔，如 A1,B1,C1",
    )
    parser.add_argument(
        "--variants-file",
        type=Path,
        default=DEFAULT_VARIANTS,
        help="参数组合配置文件",
    )
    parser.add_argument("--sleep", type=float, default=2.0, help="两次请求间隔秒数")
    parser.add_argument("--dry-run", action="store_true", help="只打印组合，不发请求")
    parser.add_argument(
        "--parse-only",
        type=Path,
        help="仅解析本地 runtest 响应 JSON，用于验证指标提取",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="结果输出路径（默认 results/guorn/batch_时间戳.json）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.parse_only:
        response = load_json(args.parse_only)
        metrics = extract_metrics(response)
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        if metrics.get("ok"):
            print(
                f"\n解读: 总收益 {metrics['total_return_pct']}%, "
                f"年化 {metrics['annual_return_pct']}%, "
                f"夏普 {metrics['sharpe_ratio']:.2f}, "
                f"最大回撤 {metrics['max_drawdown_pct']}%, "
                f"2024年 {metrics['year_2024_return_pct']}%"
            )
        return

    if not args.payload.exists():
        if args.dry_run and (CONFIG_DIR / "base_payload.example.json").exists():
            args.payload = CONFIG_DIR / "base_payload.example.json"
            print(f"提示: 使用示例 payload 做 dry-run → {args.payload.name}\n")
        else:
            print(f"找不到 {args.payload}")
            print("请从浏览器复制 runtest 的 Payload 保存为该文件。")
            print(f"可参考模板: {CONFIG_DIR / 'base_payload.example.json'}")
            sys.exit(1)

    base_payload = load_json(args.payload)
    all_variants = load_variants(args.variants_file)

    if args.variants:
        wanted = {v.strip() for v in args.variants.split(",") if v.strip()}
        all_variants = [v for v in all_variants if v["id"] in wanted]

    if not all_variants:
        print("没有可运行的参数组合。")
        sys.exit(1)

    print(f"共 {len(all_variants)} 组参数待回测\n")

    results: list[dict[str, Any]] = []

    for variant in all_variants:
        vid = variant["id"]
        name = variant.get("name", vid)
        print(f"[{vid}] {name} ...", end=" ", flush=True)

        payload = build_payload(base_payload, variant, CONFIG_DIR)
        if payload is None:
            note = variant.get("note", f"缺少 {variant.get('payload_file')}")
            print(f"跳过 ({note})")
            results.append(
                {
                    "ok": False,
                    "variant_id": vid,
                    "variant_name": name,
                    "error": note,
                }
            )
            continue

        if args.dry_run:
            changed = variant.get("patches") or variant.get("payload_file") or "无改动"
            print(f"dry-run, 改动: {changed}")
            continue

        try:
            response = run_backtest(payload)
            metrics = extract_metrics(response)
            row = {
                **metrics,
                "variant_id": vid,
                "variant_name": name,
                "patches": variant.get("patches", {}),
            }
            if metrics.get("ok"):
                print(
                    f"年化 {fmt_pct(metrics['annual_return'])}, "
                    f"夏普 {metrics['sharpe_ratio']:.2f}, "
                    f"回撤 {fmt_pct(metrics['max_drawdown'])}"
                )
            else:
                print(f"解析失败: {metrics.get('error')}")
            results.append(row)
        except Exception as exc:
            print(f"失败: {exc}")
            results.append(
                {
                    "ok": False,
                    "variant_id": vid,
                    "variant_name": name,
                    "error": str(exc),
                }
            )

        if not args.dry_run and variant != all_variants[-1]:
            time.sleep(args.sleep)

    if args.dry_run:
        return

    ranked = rank_results(results)
    print_rank_table(ranked)

    out = args.output or RESULTS_DIR / f"batch_{datetime.now():%Y%m%d_%H%M%S}.json"
    save_results(ranked, out)


if __name__ == "__main__":
    main()
