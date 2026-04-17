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
