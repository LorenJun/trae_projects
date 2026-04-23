---
project_name: "Football Prediction Agent"
version: "1.0.0"
author: "LorenJun"
created_date: "2026-04-18"
---

# 🏆 足球比赛预测分析系统

一个专业的足球比赛预测分析系统，集成多维度数据分析、机器学习模型和博彩数据研究。

---

## 项目简介

本项目是一套完整的足球比赛预测分析框架，包含：

- 📊 **数据采集Agent** - 从多个权威数据源采集比赛数据
- 🔍 **比赛分析Agent** - 多维度比赛基本面分析
- 💰 **赔率分析Agent** - 凯利指数、盘口深度分析
- 📈 **结果追踪Agent** - 准确率统计与持续优化

---

## 快速开始

### 1. 查看项目总览

首先阅读 [agent.md](./agent.md) 了解项目架构。

### 2. 了解专业Agent

| Agent | 文档 | 职责 |
|-------|------|------|
| 数据采集Agent | [agents/data_collector_agent.md](./agents/data_collector_agent.md) | 赔率、球队信息采集 |
| 比赛分析Agent | [agents/match_analyzer_agent.md](./agents/match_analyzer_agent.md) | 基本面、战术分析 |
| 赔率分析Agent | [agents/odds_analyzer_agent.md](./agents/odds_analyzer_agent.md) | 凯利指数、盘口分析 |
| 结果追踪Agent | [agents/result_tracker_agent.md](./agents/result_tracker_agent.md) | 准确率统计、优化 |

### 3. 阅读标准规范

| 规范 | 文档 |
|------|------|
| 数据格式规范 | [docs/standards/data_format.md](./docs/standards/data_format.md) |
| 工作流程规范 | [docs/standards/workflow.md](./docs/standards/workflow.md) |
| 编码规范 | [docs/standards/coding_standards.md](./docs/standards/coding_standards.md) |

---

## 环境安装

### 1. 系统要求

- Python 3.9+（推荐 3.9 或 3.10）
- macOS / Linux
- `pip` 和 `venv`
- 可选：Node.js 18+（如需使用 `feishu-cli`）

### 2. 创建 Python 虚拟环境

```bash
cd /Users/bytedance/trae_projects
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
```

### 3. 安装核心 Python 依赖

以下依赖可覆盖当前主流程、CLI 命令和大多数数据脚本：

```bash
python3 -m pip install -r requirements.txt
```

说明：
- `requests`：用于抓取公开页面和接口
- `playwright`：用于浏览器自动化抓取
- `websocket-client`：用于部分实时抓取脚本

### 4. 安装 Playwright 浏览器

如果你需要运行澳客、比赛搜索、浏览器采集等脚本，还需要安装浏览器内核：

```bash
python3 -m playwright install chromium
```

### 5. 安装 browser-use（可选）

以下场景需要 `browser-use`：
- `data_collector.py` 的真实浏览器采集
- `okooo_save_snapshot.py` 的 user-like 浏览
- 某些 `analysis/predictions/data_scraper.py` 脚本

安装命令：

```bash
python3 -m pip install -r requirements-openclaw.txt
```

如果未安装 `browser-use`，系统当前会自动降级为 `mock` 数据模式，适合流程联调，但不适合真实数据验证。
同时运行时会明确提示：请让 `openclaw` 自行执行 `python3 -m pip install browser-use` 后再重试真实采集链路。

### 缓存策略（默认实时）

预测流程的 `.prediction_cache` 已默认关闭（不读不写），以优先保证实时最新数据。

如需临时开启本地缓存（用于性能压测或离线调试），可显式设置：

```bash
export ENABLE_PREDICTION_CACHE=1
```

恢复实时模式：

```bash
unset ENABLE_PREDICTION_CACHE
```

### 6. OpenClaw 一键初始化（推荐）

如果你的目标是让 `openclaw` 直接完整使用本系统，优先执行：

```bash
bash scripts/setup_openclaw_env.sh
```

这个脚本会自动完成：
- 创建 `.venv`
- 安装 `requirements-openclaw.txt`
- 安装 `Playwright Chromium`
- 检测到 `npm` 时自动执行 `npm install`

### 7. 安装 feishu-cli（可选）

仓库根目录的 `package.json` 当前包含：
- `feishu-cli`

如需使用飞书文档/消息相关能力，可执行：

```bash
cd /Users/bytedance/trae_projects
npm install
```

---

## 工具说明

### 必需工具

| 工具 | 用途 | 是否必需 |
|------|------|---------|
| Python 3.9+ | 运行主流程、CLI、统计脚本 | 是 |
| venv / pip | 管理 Python 环境 | 是 |

### 推荐工具

| 工具 | 用途 | 是否推荐 |
|------|------|---------|
| Playwright + Chromium | 浏览器自动化抓取 | 推荐 |
| browser-use | 更像人工操作的抓取链路 | 推荐 |
| requests | HTTP 数据抓取 | 推荐 |
| websocket-client | 实时快照脚本 | 推荐 |

### 可选工具

| 工具 | 用途 | 是否可选 |
|------|------|---------|
| Node.js / npm | 安装 `feishu-cli` | 可选 |
| feishu-cli | 飞书文档 / 消息集成 | 可选 |

---

## 运行验证

完成安装后，建议按顺序验证：

### 1. 检查系统环境

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py setup-openclaw --json
python3 prediction_system.py health-check --json
```

### 2. 查看可用联赛

```bash
python3 prediction_system.py list-leagues --json
```

### 3. 预测单场比赛

```bash
python3 prediction_system.py predict-match \
  --league la_liga \
  --home-team 巴塞罗那 \
  --away-team 皇家马德里 \
  --date 2026-05-11 \
  --json
```

### 4. 采集比赛数据

```bash
python3 prediction_system.py collect-data \
  --league la_liga \
  --date 2026-05-11 \
  --json
```

### 5. 查看准确率

```bash
python3 prediction_system.py accuracy --refresh --json
```

---

## OpenClaw 推荐命令

为了方便 `openclaw` 或其他自动化系统调用，建议优先使用 `prediction_system.py` 的非交互子命令：

```bash
python3 prediction_system.py list-leagues --json
python3 prediction_system.py predict-match --league la_liga --home-team 巴塞罗那 --away-team 皇家马德里 --date 2026-05-11 --json
python3 prediction_system.py predict-schedule --league la_liga --date 2026-05-11 --days 2 --json
python3 prediction_system.py collect-data --league la_liga --date 2026-05-11 --json
python3 prediction_system.py pending-results --days-back 14 --json
python3 prediction_system.py save-result --match-id la_liga_20260511_巴塞罗那_皇家马德里 --home-score 2 --away-score 1 --json
python3 prediction_system.py accuracy --refresh --json
python3 prediction_system.py health-check --json
python3 prediction_system.py setup-openclaw --json
```

说明：
- `predict-schedule` 和 `save-result` 会修改 `teams_2025-26.md`
- `collect-data` 在缺少 `browser-use` 时会自动降级到 `mock` 数据模式，并提示 `openclaw` 自行安装 `browser-use`
- `--json` 输出适合自动化系统直接解析
- `health-check` 会返回 `openclaw_dependency_report`
- `setup-openclaw` 会返回完整初始化命令和依赖状态

---

## 项目结构

```
trae_projects/
├── agent.md                          # 项目总览
├── README.md                         # 本文件
├── package.json                      # 项目依赖
│
├── agents/                           # 专业Agent目录
│   ├── data_collector_agent.md      # 数据采集Agent
│   ├── match_analyzer_agent.md      # 比赛分析Agent
│   ├── odds_analyzer_agent.md       # 赔率分析Agent
│   └── result_tracker_agent.md      # 结果追踪Agent
│
├── docs/                             # 文档目录
│   └── standards/                    # 标准规范
│       ├── data_format.md            # 数据格式规范
│       ├── workflow.md               # 工作流程规范
│       └── coding_standards.md      # 编码规范
│
├── .trae/                            # Trae技能目录
│   └── skills/
│       ├── football-match-analysis/  # 足球分析核心技能
│       ├── browser-use/              # 浏览器自动化
│       └── ...
│
├── europe_leagues/                   # 欧洲联赛数据
│   ├── premier_league/               # 英超
│   ├── la_liga/                      # 西甲
│   ├── serie_a/                      # 意甲
│   ├── bundesliga/                   # 德甲
│   ├── ligue_1/                      # 法甲
│   └── README.md                     # 联赛数据说明
│
├── scripts/                          # 脚本目录
│   ├── poisson_analysis.py           # 泊松分布分析
│   └── analyze_prediction_accuracy.py # 准确率分析
│
└── .gitignore                        # Git忽略文件
```

---

## 工作流程

完整的比赛预测流程分为10个步骤：

```
1. 赛前准备 → 2. 数据采集 → 3. 比赛分析 → 4. 赔率分析
→ 5. 综合预测 → 6. 记录预测 → 7. 比赛进行 → 8. 结果记录
→ 9. 月度统计 → 10. 持续优化
```

详细流程请参考 [工作流程规范](./docs/standards/workflow.md)。

---

## 核心技能

### 凯利指数分析
```
低一致，稳；高分散，冷；
临场降，强；临场升，坑
```

### 泊松分布预测
使用泊松分布计算比分概率，详见 [poisson_analysis.py](./scripts/poisson_analysis.py)。

---

## 数据格式

所有数据文件遵循统一格式规范，详见 [数据格式规范](./docs/standards/data_format.md)。

---

## 联系方式

- **维护者**: LorenJun
- **邮箱**: jl.07221@qq.com

---

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0.0 | 2026-04-18 | 初始版本，完整框架搭建 |
