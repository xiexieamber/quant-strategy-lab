# 聚宽 JQData API 参考（AI / 开发者速查）

> 官方文档：[JQData 说明书](https://www.joinquant.com/help/api/doc?name=JQDatadoc) · [API 帮助](https://www.joinquant.com/help/api/help?name=JQData) · [SDK 申请](https://www.joinquant.com/default/index/sdk)  
> GitHub：[JoinQuant/jqdatasdk](https://github.com/JoinQuant/jqdatasdk) · PyPI：`jqdatasdk`

本文档供 **本项目本地 Python 环境** 使用 `jqdatasdk` 时查阅。写代码前先看「常见错误」和「账号权限」。

---

## 1. 两套 API，不要混用

| 名称 | 包/环境 | 用途 | 本项目 |
|------|---------|------|--------|
| **jqdatasdk（JQData SDK）** | `pip install jqdatasdk`，本地 Python | 本地研究、回测、拉数据 | ✅ 使用这个 |
| **官网 API（jqdata）** | 聚宽网站策略编辑器 / 在线 Notebook | 在线写策略、回测 | ❌ 不在本地调用 |

官网 API 文档：<https://www.joinquant.com/help/api/help?name=api>  
本地 SDK 文档：<https://www.joinquant.com/help/api/doc?name=JQDatadoc>

函数名相似但细节不同；本地项目 **只查 JQData / jqdatasdk 文档**。

---

## 2. 安装与认证

```bash
python3 -m pip install jqdatasdk
```

```python
import jqdatasdk as jq

# 登录（重复调用安全）
jq.auth("手机号", "聚宽密码")
# 新用户默认密码常见为手机号后 6 位
```

### 本项目账号配置

- 环境变量：`JQ_USERNAME` / `JQ_PASSWORD`
- 或项目根目录 `.env`（已被 `.gitignore` 忽略）
- 封装函数：见 `src/data/fetch_jqdata.py` 的 `_ensure_auth()`、`_load_dotenv()`

**禁止** 把密码硬编码进 `.py` 文件或提交到 Git。

### 认证后常用检查

```python
jq.get_query_count()      # {'total': ..., 'spare': ...} 今日查询额度
jq.get_account_info()     # 含 date_range_start / date_range_end / expire_time
jq.get_privilege()        # 当前账号数据权限列表
```

`get_account_info()` 返回示例字段：

| 字段 | 含义 |
|------|------|
| `mob` | 手机号 |
| `query_count_limit` | 每日查询上限 |
| `expire_time` | 账号过期时间 |
| `date_range_start` | 可获取数据最早日期 |
| `date_range_end` | 可获取数据最晚日期 |

---

## 3. 账号权限与日期限制（重要）

试用账号通常 **只能获取近约 1 年** 的数据，例如：

```
date_range_start: 2025-02-27
date_range_end:   2026-03-06
```

超出范围会报错：

```
您的账号权限仅能获取YYYY-MM-DD至YYYY-MM-DD的数据，请调整时间参数后重试
```

### 正确做法

```python
from src.data.fetch_jqdata import get_jq_date_range, fetch_ohlcv_jq

start, end = get_jq_date_range()          # 读账号允许范围
df = fetch_ohlcv_jq("000001.XSHE", start=start, end=end)

# 或拉最近 N 个交易日（不要同时传 start_date 和 count）
import jqdatasdk as jq
jq.auth(...)
_, end = get_jq_date_range()
df = jq.get_price("000001.XSHE", end_date=end, count=30)
```

本项目 `fetch_ohlcv_jq()` 会通过 `_clamp_date_range()` 自动裁剪到权限范围内。

---

## 4. 证券代码规范

聚宽 A 股代码格式：**6 位数字 + 交易所后缀**

| 后缀 | 交易所 | 示例 |
|------|--------|------|
| `.XSHE` | 深圳证券交易所 | `000001.XSHE` 平安银行 |
| `.XSHG` | 上海证券交易所 | `600519.XSHG` 贵州茅台 |

常见指数：

| 名称 | 代码 |
|------|------|
| 沪深300 | `000300.XSHG` |
| 中证500 | `000905.XSHG` |
| 上证指数 | `000001.XSHG` |

与 yfinance 对照：

| 股票 | 聚宽 | yfinance |
|------|------|----------|
| 平安银行 | `000001.XSHE` | `000001.SZ` |
| 贵州茅台 | `600519.XSHG` | `600519.SS` |

工具函数：`jq.normalize_code(code)` 尝试规范化代码。

---

## 5. 核心 API 速查

### 5.1 `get_price` — 历史行情（最常用）

```python
jq.get_price(
    security,                    # str 或 list，如 "000001.XSHE"
    start_date=None,             # 与 count 二选一，不可同时使用
    end_date=None,               # 默认较早日限；含该日
    frequency="daily",           # daily / 1d / 1m / 5m / 60m / 1w 等
    fields=None,                 # 默认 open,close,high,low,volume,money
    skip_paused=False,           # True 跳过停牌日
    fq="pre",                    # pre前复权 / none不复权 / post后复权
    count=None,                  # 与 start_date 二选一
    panel=True,                  # 多标的时是否 Panel（新版 pandas 可能自动 False）
    fill_paused=True,
)
```

**返回**：单标的 → `pd.DataFrame`，index 为 datetime，列为 fields。

**fields 可选值**：

`open`, `close`, `high`, `low`, `volume`, `money`, `factor`, `high_limit`, `low_limit`, `avg`, `pre_close`, `paused`

**示例**：

```python
# 区间日线
df = jq.get_price("600519.XSHG", start_date="2025-03-01", end_date="2025-06-01",
                  fields=["open", "close", "volume"], fq="pre")

# 最近 20 个交易日（只传 end_date + count）
df = jq.get_price("000001.XSHE", end_date="2026-03-06", count=20)

# 多标的（panel=False 时返回带 code/time 列的 DataFrame）
df = jq.get_price(["000001.XSHE", "600036.XSHG"], start_date="2025-01-01",
                  end_date="2025-03-01", panel=False)
```

### 5.2 `get_bars` — 按 bar 数量取 K 线

```python
jq.get_bars(
    security,
    count=240,
    unit="1d",                   # 1d / 1m / 5m / 15m / 30m / 60m / 1w / 1M
    fields=("date", "open", "high", "low", "close"),
    include_now=False,
    end_dt=None,
    fq_ref_date=None,
    df=True,
)
```

适合「从某日往前取 N 根 K 线」的场景。

### 5.3 `get_all_securities` — 标的列表

```python
jq.get_all_securities(types=["stock"], date=None)
# types: stock, fund, index, futures, etf, lof, fja, fjb
# 返回 DataFrame，index 为代码，列含 display_name, name, start_date, end_date, type
```

### 5.4 `get_security_info` — 单标的元信息

```python
info = jq.get_security_info("000001.XSHE")
# display_name, name, start_date, end_date, type 等
```

### 5.5 `get_index_stocks` — 指数成分股

```python
stocks = jq.get_index_stocks("000300.XSHG", date="2025-06-01")
# 返回 list[str]
```

### 5.6 `get_industry_stocks` / `get_concept_stocks` — 行业/概念成分

```python
jq.get_industry_stocks("C21", date="2025-06-01")   # 行业编码见 plateData
jq.get_concept_stocks("GN001", date="2025-06-01")
jq.get_industries(name="zjw")
jq.get_concepts()
```

### 5.7 `get_trade_days` / `get_all_trade_days` — 交易日历

```python
jq.get_all_trade_days()                              # 全部交易日 ndarray
jq.get_trade_days(start_date="2025-01-01", end_date="2025-12-31")
jq.get_trade_days(end_date="2025-06-01", count=10)   # 最近 10 个交易日
```

### 5.8 `get_extras` — ST、净值等附加信息

```python
jq.get_extras("is_st", ["000001.XSHE"], start_date="2025-01-01", end_date="2025-06-01")
# info: is_st, acc_net_value, unit_net_value, futures_sett_price, futures_positions
```

### 5.9 `get_fundamentals` — 财务数据

```python
from jqdatasdk import query, valuation, indicator, income, balance, cash_flow

q = query(
    valuation.code,
    valuation.market_cap,
    indicator.roe,
).filter(
    valuation.code.in_(["000001.XSHE", "600519.XSHG"])
)

df = jq.get_fundamentals(q, date="2025-06-01")
# 或 statDate="2024q4" 按财报期
```

财务字段字典：<https://www.joinquant.com/data/dict/fundamentals>

### 5.10 `get_valuation` — 估值数据

```python
jq.get_valuation(["000001.XSHE"], start_date="2025-01-01", end_date="2025-06-01",
                 fields=["pe_ratio", "pb_ratio", "market_cap"])
```

### 5.11 `get_money_flow` — 资金流向

```python
jq.get_money_flow(["000001.XSHE"], start_date="2025-01-01", end_date="2025-06-01")
```

### 5.12 因子相关

```python
jq.get_all_factors()
jq.get_factor_values(["000001.XSHE"], ["roe_ttm", "pe_ratio"],
                     start_date="2025-01-01", end_date="2025-06-01")
```

---

## 6. 常见错误与修复

| 错误 | 原因 | 修复 |
|------|------|------|
| `用户不存在或密码错误` | 账号/密码错或未开通 JQData | 检查 `.env`；到 [SDK 申请页](https://www.joinquant.com/default/index/sdk) 开通 |
| `账号权限仅能获取...的数据` | 日期超出试用范围 | 用 `get_jq_date_range()` 或 `_clamp_date_range()` |
| `(start_date, count) only one param is required` | 同时传了 start_date 和 count | 二选一 |
| `security_list is required` | 必填参数为空 | 检查股票代码 list |
| 返回空 DataFrame | 代码错、非交易日、标的未上市 | 检查 `.XSHE`/`.XSHG` 后缀和日期 |
| 列名是小写 `open/close` | JQData 原生返回小写 | 本项目 `fetch_ohlcv_jq` 已转为 `Open/Close/...` 对齐 yfinance |

---

## 7. 本项目封装（优先使用）

写本项目代码时，**优先用封装**，不要重复造轮子：

| 需求 | 使用 |
|------|------|
| 拉 A 股 OHLCV | `from src.data.fetch_jqdata import fetch_ohlcv_jq` |
| 查账号日期范围 | `from src.data.fetch_jqdata import get_jq_date_range` |
| 拉美股 OHLCV | `from src.data.fetch_data import fetch_ohlcv` |
| 测试登录 | `python3 scripts/test_jq_auth.py` |
| 演示拉数据 | `python3 scripts/fetch_jqdata_demo.py` |

`fetch_ohlcv_jq` 返回列：`Open, High, Low, Close, Volume`（与 `fetch_ohlcv` 一致），可直接喂给 `src/backtest/engine.py` 和 `src/strategies/`。

---

## 8. AI 写代码检查清单

在生成或修改 JQData 相关代码前，确认：

- [ ] 使用的是 `jqdatasdk`，不是聚宽官网在线 API
- [ ] 认证通过 `_ensure_auth()` 或 `jq.auth()`，密码来自 `.env`
- [ ] 日期在 `get_account_info()` 的 `date_range_start` ~ `date_range_end` 内
- [ ] `get_price` 没有同时传 `start_date` 和 `count`
- [ ] A 股代码带 `.XSHE` 或 `.XSHG` 后缀
- [ ] 回测用日线时设 `fq="pre"`（前复权）或明确说明不复权原因
- [ ] 输出 DataFrame 列名与项目约定一致（大写 OHLCV 或在使用处 rename）
- [ ] 不把 `.env` 或密码写进源码

---

## 9. 代码模板

### 模板 A：本项目标准拉数

```python
from src.data.fetch_jqdata import fetch_ohlcv_jq, get_jq_date_range

start, end = get_jq_date_range()
df = fetch_ohlcv_jq("000001.XSHE", start=start, end=end)
```

### 模板 B：原生 SDK 最近 N 天

```python
import jqdatasdk as jq
from src.data.fetch_jqdata import _ensure_auth, get_jq_date_range

_ensure_auth()
_, end = get_jq_date_range()
df = jq.get_price("000001.XSHE", end_date=end, count=30,
                  fields=["open", "close", "volume"], fq="pre")
```

### 模板 C：指数成分 + 批量行情

```python
import jqdatasdk as jq
from src.data.fetch_jqdata import _ensure_auth, get_jq_date_range

_ensure_auth()
start, end = get_jq_date_range()
stocks = jq.get_index_stocks("000300.XSHG")[:10]  # 沪深300前10只
df = jq.get_price(stocks, start_date=start, end_date=end, panel=False, fq="pre")
```

---

## 10. 相关链接

| 资源 | URL |
|------|-----|
| JQData API 文档（用户提供的入口） | https://www.joinquant.com/help/api/doc?name=JQDatadoc&id=10276 |
| JQData 说明书 | https://www.joinquant.com/help/api/help?name=JQData |
| SDK 申请 | https://www.joinquant.com/default/index/sdk |
| 财务数据字典 | https://www.joinquant.com/data/dict/fundamentals |
| 行业/概念分类 | https://www.joinquant.com/data/dict/plateData |
| 指数信息 | https://www.joinquant.com/indexData |
| HTTP API（非 Python） | https://dataapi.joinquant.com/docs |
