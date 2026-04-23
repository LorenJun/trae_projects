---
project_name: "Football Prediction Agent"
version: "1.1.0"
author: "LorenJun"
email: "jl.07221@qq.com"
created_date: "2026-04-18"
last_updated_date: "2026-04-22"
---

# 🏆 足球比赛预测分析Agent

## 项目概述

本项目是一个专业的足球比赛预测分析系统，集成多维度数据分析、机器学习模型和博彩数据研究，提供专业的比赛预测和投注建议。

### 核心功能
- 多联赛（欧洲五大联赛）比赛分析
- 实时赔率数据获取与分析
- 凯利指数深度研究
- 泊松分布概率预测
- 预测准确率跟踪与优化

---

## 项目架构

```
trae_projects/
├── agent.md                          # 本文件 - 项目总览
├── package.json                      # 项目依赖配置
├── .gitignore                        # Git忽略文件
│
├── agents/                           # 专业Agent目录
│   ├── data_collector_agent.md      # 数据采集Agent
│   ├── match_analyzer_agent.md      # 比赛分析Agent
│   ├── odds_analyzer_agent.md       # 赔率分析Agent
│   └── result_tracker_agent.md      # 结果追踪Agent
│
├── .trae/                            # Trae技能目录
│   └── skills/
│       ├── football-match-analysis/  # 足球分析核心技能
│       ├── browser-use/              # 浏览器自动化
│       ├── agent-browser/            # Agent浏览器
│       ├── playwright-cli/           # Playwright工具
│       ├── chrome-mcp/               # Chrome DevTools
│       └── feishu-cli/               # 飞书集成
│
├── europe_leagues/                   # 欧洲联赛数据
│   ├── premier_league/               # 英超
│   ├── la_liga/                      # 西甲
│   ├── serie_a/                      # 意甲
│   ├── bundesliga/                   # 德甲
│   ├── ligue_1/                      # 法甲
│   ├── .okooo-scraper/               # 运行时产物（快照/日志/赛程抓取结果）
│   ├── okooo_save_snapshot.py        # 抓澳客实时赔率快照
│   ├── optimized_prediction_workflow.py # 预测流程（写回 teams_2025-26.md）
│   ├── enhanced_prediction_workflow.py  # 增强预测流程（写回 teams_2025-26.md）
│   └── README.md                     # 联赛数据说明
│
├── docs/                             # 文档目录
│   ├── standards/                    # 标准规范
│   │   ├── coding_standards.md       # 编码规范
│   │   ├── data_format.md            # 数据格式规范
│   │   └── workflow.md               # 工作流程规范
│   └── tutorials/                    # 教程文档
│
├── poisson_analysis.py               # 泊松分布分析
├── analyze_prediction_accuracy.py    # 历史准确率分析脚本（旧）
├── predictions/                      # 历史输出（旧）
└── docs/                             # 文档目录（标准/教程）
```

---

## 单一事实来源（重要）

项目当前以 `europe_leagues/<league>/teams_2025-26.md` 作为单一事实来源（Single Source of Truth）：
- 赛程表（6列）：`日期 | 时间 | 主队 | 比分 | 客队 | 备注`
- 比分列：已完赛填写 `x-y`，未完赛统一为 `-`
- 备注列：写入赛前预测信息（机器可解析），赛后可追加 `✅/❌`
- 文件内 `## 预测记录` 段落用于复盘/沉淀经验（可人读，也可作为扩展学习输入）

已废弃的数据目录/模板（仓库可能仍保留部分历史文件，但不再作为主流程输入）：
- `prediction_history/`（已迁移并删除）
- `analysis/results/results_template.md`（已删除）
- `analysis/predictions/*_predictions*.md`（预测流程不再生成）

---

## 专业Agent说明

### 1. 数据采集Agent (Data Collector Agent)
**职责**：
- 从竞彩网、澳客等权威网站获取实时赔率数据
- 抓取球队信息、伤病名单、首发阵容
- 收集历史比赛数据

**输入**：比赛日期、对阵双方
**输出**：结构化的比赛数据

### 2. 比赛分析Agent (Match Analyzer Agent)
**职责**：
- 球队基本面分析（排名、状态、攻防能力）
- 战术打法对比
- 历史交锋记录研究
- 战意评估

**输入**：结构化比赛数据
**输出**：多维度分析报告

### 3. 赔率分析Agent (Odds Analyzer Agent)
**职责**：
- 凯利指数深度分析
- 欧赔与亚盘变动研究
- 庄家心理博弈分析
- 赔率与基本面偏差识别

**输入**：赔率数据
**输出**：赔率分析报告

### 4. 结果追踪Agent (Result Tracker Agent)
**职责**：
- 记录预测结果
- 跟踪实际比赛结果
- 统计预测准确率
- 生成优化建议

**输入**：预测数据 + 实际结果
**输出**：准确率分析报告

---

## 标准工作流程

### 完整比赛分析流程

```
Step 1: 数据采集
        ↓
   [数据采集Agent]
        ↓
Step 2: 基本面分析
        ↓
   [比赛分析Agent]
        ↓
Step 3: 赔率分析
        ↓
   [赔率分析Agent]
        ↓
Step 4: 综合预测
        ↓
   [写回 teams_2025-26.md 备注列]
        ↓
Step 5: 赛后追踪
        ↓
   [结果追踪Agent]
        ↓
Step 6: 准确率统计与优化
```

---

## 常用命令（可跑通）

在项目根目录 `/Users/bytedance/trae_projects`：

### 1) 抓实时赔率快照（澳客）
```bash
cd europe_leagues
python3 okooo_save_snapshot.py --driver local-chrome --league 西甲 --team1 '毕尔巴鄂竞技' --team2 '奥萨苏纳' --date 2026-04-22
```

### 2) 生成预测并写回联赛文档
```bash
cd europe_leagues
python3 -c "from optimized_prediction_workflow import generate_prediction_report; print(generate_prediction_report('la_liga','2026-04-22'))"
```

### 3) 统计准确率（直接解析 teams_2025-26.md）
```bash
cd europe_leagues
python3 result_manager.py --update-accuracy
```

输出位置：
- `europe_leagues/.okooo-scraper/runtime/accuracy_stats.json`

运行时产物（不入库）：
- `europe_leagues/.okooo-scraper/`
- `.prediction_cache/`

---

## 技术栈

| 类别 | 技术 | 用途 |
|------|------|------|
| **编程语言** | Python 3.8+ | 核心脚本 |
| **浏览器自动化** | browser-use / playwright | 数据采集 |
| **数据处理** | pandas / numpy | 数据分析 |
| **概率模型** | Poisson / Dixon-Coles | 进球预测 |
| **文档格式** | Markdown | 报告输出 |
| **协作工具** | Feishu | 团队协作 |

---

## 数据规范

### 预测数据格式

| 字段 | 类型 | 说明 | 必填 |
|------|------|------|------|
| 比赛日期 | String | YYYY-MM-DD | ✅ |
| 主队 | String | 主队名称 | ✅ |
| 客队 | String | 客队名称 | ✅ |
| 预测结果 | Enum | 主胜/平局/客胜 | ✅ |
| 预测比分 | String | 格式如 "2-1" | ✅ |
| 预测概率 | Float | 0.0-1.0 | ✅ |
| 大小球 | Enum | 大球/小球 | ✅ |
| 让球盘 | String | 格式如 "利物浦-0.5" | ✅ |
| 实际结果 | Enum | 赛后填写 | - |
| 实际比分 | String | 赛后填写 | - |
| 预测正确 | Boolean | 赛后填写 | - |

---

## Git 工作流

### 分支策略
- `main` - 稳定版本
- `develop` - 开发分支
- `feature/*` - 功能分支
- `hotfix/*` - 紧急修复

### 提交信息规范
```
类型: 简短描述

详细描述（可选）
```

**类型**：
- `feat` - 新功能
- `fix` - 修复bug
- `docs` - 文档更新
- `refactor` - 重构
- `test` - 测试

---

## 联系方式

- **维护者**: LorenJun
- **邮箱**: jl.07221@qq.com
- **项目路径**: /Users/bytedance/trae_projects

---

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0.0 | 2026-04-18 | 初始版本，框架搭建完成 |
