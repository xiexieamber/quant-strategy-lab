"""果仁 runtest HTTP 客户端（非官方内部接口）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

RUNTEST_URL = "https://guorn.com/stock/runtest"


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def build_headers() -> dict[str, str]:
    _load_dotenv()
    cookie = os.environ.get("GUORN_COOKIE", "").strip()
    xsrf = os.environ.get("GUORN_XSRF_TOKEN", "").strip()

    if not cookie:
        raise ValueError(
            "缺少 GUORN_COOKIE。请从浏览器 Network → runtest → Request Headers 复制 Cookie 到 .env"
        )

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json; charset=UTF-8",
        "Origin": "https://guorn.com",
        "Referer": "https://guorn.com/stock?category=stock",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": cookie,
    }
    if xsrf:
        headers["X-Xsrftoken"] = xsrf
    return headers


def run_backtest(payload: dict[str, Any], *, timeout: int = 120) -> dict[str, Any]:
    headers = build_headers()
    xsrf = os.environ.get("GUORN_XSRF_TOKEN", "").strip()
    params = {"_xsrf": xsrf} if xsrf else None
    resp = requests.post(
        RUNTEST_URL,
        json=payload,
        headers=headers,
        params=params,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "info":
        raise RuntimeError(f"果仁返回: {data.get('data')}（Cookie 可能已过期）")
    return data
