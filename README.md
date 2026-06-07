# Quant Strategy Lab

一个面向初学者的**量化策略学习与搭建**仓库。从零开始，一步步学习：获取数据 → 编写策略 → 回测验证 → 分析结果。

## 这个项目能做什么？

| 阶段 | 说明 | 对应目录 |
|------|------|----------|
| 1. 学习概念 | 了解量化交易的基本流程 | `docs/` |
| 2. 获取数据 | 下载股票/ETF 历史价格 | `src/data/` |
| 3. 编写策略 | 实现买卖信号逻辑 | `src/strategies/` |
| 4. 回测验证 | 用历史数据模拟交易 | `src/backtest/` |
| 5. 运行示例 | 一键跑通第一个策略 | `scripts/` |

## 快速开始

### 第一步：安装 Python 环境

需要 Python 3.10 或以上版本。

```bash
# 进入项目目录
cd quant-strategy-lab

# 创建虚拟环境（推荐）
python3 -m venv .venv
source .venv/bin/activate   # Windows 用: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 第二步：运行第一个策略（双均线）

```bash
python scripts/run_backtest.py
```

如果一切正常，你会看到：
- 下载了苹果股票（AAPL）的历史数据
- 运行了「双均线策略」回测
- 输出了总收益率、最大回撤等指标

## 项目结构

```
quant-strategy-lab/
├── docs/                 # 学习文档
├── data/                 # 数据目录（原始数据不上传 GitHub）
├── src/
│   ├── data/             # 数据获取模块
│   ├── strategies/       # 策略逻辑
│   └── backtest/         # 回测引擎
├── scripts/              # 可执行脚本
├── notebooks/            # Jupyter 交互式学习
├── tests/                # 单元测试
└── strategies/           # 每个策略的独立配置
```

## 学习路线（建议顺序）

1. 阅读 [入门指南](docs/01-入门指南.md)
2. 阅读 [策略开发流程](docs/02-策略开发流程.md)
3. 运行 `scripts/run_backtest.py`，观察输出
4. 打开 `notebooks/01_双均线策略入门.ipynb` 逐步理解
5. 修改 `strategies/dual_ma/config.yaml` 里的参数，重新回测
6. 在 `src/strategies/` 下编写自己的策略

## 第一个示例策略：双均线

**核心思想（用大白话说）：**

- 算两条「平均线」：短期（如 5 天）和长期（如 20 天）
- 短期线从下方穿过长期线 → **买入**（趋势可能向上）
- 短期线从上方穿过长期线 → **卖出**（趋势可能向下）

这是量化领域最经典的入门策略之一，重点在于理解**回测流程**，而不是策略本身能否赚钱。

## 重要提醒

> 本仓库仅用于**学习与研究**，不构成任何投资建议。
> 历史回测表现好，不代表未来能赚钱。实盘交易前请充分理解风险。

## 常用命令

```bash
# 运行回测
python scripts/run_backtest.py

# 运行测试
pytest tests/ -v

# 启动 Jupyter（交互式学习）
jupyter notebook notebooks/
```

## License

MIT
