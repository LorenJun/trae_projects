---
project_name: "Football Prediction Agent"
version: "1.3.2"
author: "LorenJun"
email: "jl.07221@qq.com"
created_date: "2026-04-18"
last_updated_date: "2026-05-15"
---

# 足球比赛预测分析Agent

> 当前正式流程  
> 1. `prediction_system.py collect-data` 或赛程抓取定位 `match_id`  
> 2. `prediction_system.py predict-match / predict-schedule` 执行增强预测  
> 3. 结果写回 `europe_leagues/<league>/teams_2025-26.md`  
> 4. 赛后用 `prediction_system.py save-result` 或 `bulk_fetch_and_update.py` 回填  
> 5. 最后用 `prediction_system.py accuracy --refresh --json` 刷新胜负 / 比分 / 大小球统计  
> 可审计编排入口：`prediction_system.py harness-run --pipeline ... --json`  
> 关键检查项：`over_under.line`、`line_source`、`over_under.market.final`

## 项目概述

本项目是一个以欧洲联赛为核心的足球预测系统，当前已完成“赛程/MatchID -> 实时赔率快照 -> `app/cli.py` -> `DomainPredictor` / `EnhancedPredictor` -> teams 写回/准确率统计”的主流程收敛，并支持可选的 Sofascore 球队状态增强（team_context）与 Harness 编排入口。

### 当前核心功能

- 多联赛比赛分析与预测
- 实时欧赔、亚值、凯利抓取
- 从 `亚值` 页面内 `大小球` tab 抓取真实盘口线与水位
- 可选：SofaScore 近况增强（阵型/控球/上一场首发/球员评分趋势）注入 `analysis_context['team_context']`
- 多模型融合、Dixon-Coles、动态调权、临场修正
- EWMA 近况自动回填、缺失大小球自动补抓
- 预测结果写回 `teams_2025-26.md`
- 赛果回填与准确率统计（胜负 / 比分 / 大小球）
- Harness pipeline：`match_prediction` / `result_recording`

## 当前架构

```text
trae_projects/
├── agent.md
├── agents/
│   ├── data_collector_agent.md
│   ├── football_actuary_persona.md
│   ├── match_analyzer_agent.md
│   ├── odds_analyzer_agent.md
│   └── result_tracker_agent.md
├── .trae/skills/
│   ├── football-match-analysis/
│   ├── football-prediction-live-update/
│   ├── okooo-match-finder/
│   ├── browser-use/
│   ├── chrome-mcp/
│   └── playwright-cli/
└── europe_leagues/
    ├── app/cli.py
    ├── harness/
    │   ├── core.py
    │   └── football.py
    ├── domain/
    │   ├── predictor.py
    │   ├── features.py
    │   ├── odds.py
    │   ├── intelligence.py
    │   ├── upset.py
    │   ├── live.py
    │   ├── inference.py
    │   ├── postprocess.py
    │   ├── persistence.py
    │   ├── reporting.py
    │   ├── writeback.py
    │   └── team_strength.py
    ├── collectors/
    ├── models/
    ├── storage/
    ├── runtime/
    ├── prediction_system.py
    ├── enhanced_prediction_workflow.py
    ├── result_manager.py
    ├── <league>/teams_2025-26.md
    └── .okooo-scraper/
```

当前含义：

- `prediction_system.py` 仅作为兼容入口，实际命令实现已迁移到 `app/cli.py`
- `DomainPredictor` 是接口层与预测核心之间的稳定外壳
- `EnhancedPredictor` 仍保留主流程编排职责，但大部分子能力已拆到 `domain/`、`collectors/`、`storage/`、`runtime/`
- `agents/*.md` 与 `agent_runtime_registry.py` 共同决定运行时输出中的 `runtime_profile`

## 单一事实来源

正式预测与赛果学习统一以：

- `europe_leagues/<league>/teams_2025-26.md`

为准。

运行时抓取与缓存目录：

- `europe_leagues/.okooo-scraper/snapshots/`
- `europe_leagues/.okooo-scraper/schedules/`
- `europe_leagues/.okooo-scraper/runtime/`

## 统一职业身份

当前项目所有 Agent、主流程与复盘链路，统一采用：

- [专业纬度足彩数据精算师](./agents/football_actuary_persona.md)

作为上位职业身份约束。

其核心含义是：

- 不只看基本面
- 不只看盘口
- 不只跑模型
- 不只统计结果

而是同时从以下专业纬度工作：

1. 足球业务分析
2. 赔率与盘口交易分析
3. 概率建模
4. 统计验证与模型评估
5. 风险控制与资金管理
6. 数据工程、流程自动化与策略迭代

统一要求：

- 输出中区分 `模型结论`、`盘口结论`、`综合结论`
- 明确样本边界、降级情况与风险提示
- 正式流程服从 `prediction_system.py` -> `app/cli.py`、`DomainPredictor` / `EnhancedPredictor`、`bulk_fetch_and_update.py` 与 `harness`
- 正式写回仍以 `teams_2025-26.md` 为准

## Agent 分工

### 数据采集Agent

负责：

- 获取赛程
- 定位 `match_id`
- 刷新实时快照
- 维护球队简称映射
- 为批量结果回填准备当天已结束比赛

当前要求：

- 优先使用 `okooo_fetch_daily_schedule.py`
- 自动化场景优先使用 `prediction_system.py collect-data`
- 已知 `match_id` 时直接抓快照
- 大小球优先走 `handicap.php` 页面内 `大小球` tab
- 在精算师职业画像中，主要承担“第 6 维数据工程、流程自动化与策略迭代中的输入准备与质量控制”职责

### 比赛分析Agent

负责：

- 基本面、战术、伤停、战意、历史交锋
- 结合赔率上下文解释比分与节奏

当前要求：

- 若真实大小球线已存在，不再沿用固定 `2.5` 解释比赛节奏
- 可结合 `analysis_context['team_context']`、EWMA form 与 `match_intelligence`
- 在精算师职业画像中，主要承担“第 1 维足球业务分析 + 第 5 维风险控制支持中的比赛脚本解释”职责

### 赔率分析Agent

负责：

- 欧赔、亚值、凯利、大小球、水位
- 庄家心理、盘口错配、冷热识别

当前要求：

- 最终报告里要明确输出真实大小球盘口线与大/小水位
- 重点检查 `over_under.line_source` 与 `over_under.market.final`
- 在精算师职业画像中，主要承担“第 2 维赔率与盘口交易分析 + 第 5 维风险控制支持中的盘口风险识别”职责

### 结果追踪Agent

负责：

- 赛果回填
- 准确率统计
- 历史学习数据更新

当前要求：

- 以 `teams_2025-26.md` 为单一事实来源
- 支持 `bulk_fetch_and_update.py` 批量回填
- 统计胜负、比分、大小球准确率
- 在精算师职业画像中，主要承担“第 4 维统计验证与模型评估 + 第 6 维策略迭代支持”职责

## 技能说明

当前项目在 `.trae/skills/` 目录下维护了 13 个技能，按功能分类如下：

### 足球预测核心技能

#### `football-match-analysis`

足球比赛预测分析主技能，执行完整预测流程：

- 优先走 `prediction_system.py predict-match`
- 由 `app/cli.py` -> `DomainPredictor` -> `EnhancedPredictor` 调度真实主链
- 自动拉实时快照并处理大小球补抓
- 使用真实大小球盘口线
- 输出终版结论、必要诊断字段和 `runtime_profile`

触发关键词：比赛预测、单场分析、predict-match、football-match-analysis

#### `football-prediction-live-update`

临场数据更新与滚动记忆管理技能：

- 获取临场数据（首发阵容、伤停更新、赔率变化）
- **覆盖更新** MEMORY.md 中原预测行（非重复添加）
- 使用【临场更新】标记区分初始预测 vs 临场更新
- 必须包含"调整说明"解释预测变化的逻辑
- 每场比赛在滚动记忆中只有**一个最终预测**

标准格式：
```markdown
【临场更新】预测: 主胜 (52.0%) | 比分: 2-1 > 1-0 > 1-1 | 大小球: 小球 2.75 | 亚盘: 赫罗纳-0.5
◦ 临场分析依据:
  - 首发阵容: [...]
  - 伤停更新: ...
  - 赔率变化: 主胜2.13→1.99(↓0.14)
  - 调整说明: 原预测主胜(39.7%)→临场提升为主胜(52%)，赔率走势+亚盘升盘确认信心
```

自动化脚本：
```bash
python3 .trae/skills/football-prediction-live-update/scripts/update_memory_live.py \
    --match-id la_liga_20260515_赫罗纳_皇家社会 \
    --odds-change "2.13->1.99" \
    --reasoning "临场降强信号，亚盘升盘"
```

触发关键词：临场更新、首发阵容、赔率变化、live-update

#### `okooo-match-finder`

澳客比赛定位与快照技能：

- 从 `热门赛事` 或联赛赛程页定位目标比赛
- 解决球队简称、日期歧义、时间歧义
- 获取 `match_id`
- 为后续实时快照抓取、批量采集与预测流程提供稳定输入

推荐命令：
```bash
# 抓某天联赛赛程
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28

# 用 MatchID 直接抓快照
python3 europe_leagues/okooo_save_snapshot.py \
  --driver local-chrome --league 英超 --team1 曼联 --team2 布伦特福德 \
  --date 2026-04-28 --time 03:00 --match-id 1296070 --overwrite
```

触发关键词：澳客、okooo、match-finder、match_id、赛程抓取

#### `sync-pending-results-review`

赛果回填与复盘总结技能：

- 查找待回填状态的预测样本
- 自动或手工补录真实赛果
- 同步更新 `MEMORY.md` 与五大联赛 `teams_2025-26.md`
- 刷新准确率统计与 `prediction_review_learning.json`
- 生成批次复盘总结

一键执行命令：
```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py sync-pending-results-review \
  --days-back 30 --limit 20 --review-sample-limit 8 --json
```

触发关键词：同步比赛结果、复盘预测、sync-pending-results、赛果回填

#### `five-leagues-season-review-rag`

五大联赛赛季复盘与 RAG 记忆技能：

- 聚合已完赛数据到统一 Markdown 文档
- 按联赛拆分复盘
- 错因标签回写 RAG
- 生成三层校准规则复盘、未命中原因分解、自动整改建议

一键执行命令：
```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py build-season-master-review \
  --season 2025-26 --recent-days 7 --days-back 30 --limit 50 --rag-limit 300 --json
```

触发关键词：赛季复盘、rag-review、五大联赛复盘、build-season-master-review

#### `update-five-leagues-schedules`

五大联赛赛程更新技能：

- 用 500 轮次接口更新 `teams_2025-26.md` 赛程段落
- 防误导规则：只有已结束比赛才写真实比分
- 支持数据净化（清理非真实赛程数据）

一键执行：
```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 update_five_leagues_schedule_times.py
```

触发关键词：更新赛程、schedules、赛程刷新、purge-nonreal-data

#### `update-five-leagues-players`

五大联赛球员数据更新技能：

- 英超：官网名单 → 中文名 → FPL 统计 → Understat xG/xA/热区
- 其他四联赛：Understat 统计 → Wikidata 中文名/号码 → Sofascore 补齐阵容
- 输出完整性审计报告

执行流程（9 步）：
```bash
# 1) 英超生成官方名单 CSV
python3 generate_premier_league_players_csv.py

# 2) 英超补中文名
python3 fill_premier_league_player_name_cn.py

# 3) 英超导入 players
python3 import_players_from_csv.py --csv premier_league_players_2026.csv --league premier_league

# 4) 英超补统计/伤病/热区
python3 enrich_premier_league_players_from_live_stats.py

# 5-8) 其他四联赛处理
python3 sync_other_leagues_players_from_understat.py
python3 fill_other_leagues_player_name_cn_from_wikidata.py
python3 supplement_rosters_and_numbers_from_sofascore.py
python3 fill_other_leagues_player_name_cn_from_wikidata.py

# 9) 完整性审计
python3 audit_players_completeness.py
```

触发关键词：更新球员、players、球员数据、阵容更新

### 浏览器自动化技能

#### `agent-browser`

Vercel 出品的浏览器自动化 CLI，AI Agent 首选工具：

- Rust 原生实现，高性能
- 智能元素定位（基于引用 refs）
- 丰富的命令集：点击、填写、滚动、截图
- 会话管理、认证管理、安全特性
- 可视化仪表板

基本用法：
```bash
agent-browser open https://example.com
agent-browser snapshot -i  # 获取带引用的可访问性树
agent-browser click @e1    # 使用引用点击
agent-browser fill @e2 "text"
agent-browser screenshot result.png
```

触发关键词：agent-browser、浏览器自动化、网页抓取、无头浏览器

#### `playwright-cli`

微软出品的浏览器自动化和测试工具：

- 跨浏览器支持（Chromium、Firefox、WebKit）
- 自动等待机制
- 强大的定位器系统
- 测试隔离与并行执行
- 追踪功能（截图、视频）

基本用法：
```bash
playwright-cli open https://example.com --headed
playwright-cli fill "input[name='email']" "test@example.com"
playwright-cli click "button:has-text('Submit')"
playwright-cli screenshot
```

触发关键词：playwright、浏览器测试、端到端测试、自动化测试

#### `browser-use`

AI 驱动的浏览器自动化工具（2.0 版本）：

- 专为 AI Agent 设计
- 成本减半（2.0 版本）
- 云端浏览器基础设施
- 内置代理轮换和验证码解决
- 1000+ 集成（Gmail、Slack、Notion 等）

Python 用法：
```python
from browser_use import Agent, Browser, ChatBrowserUse

browser = Browser()
agent = Agent(
    task="Find the number of stars of the browser-use repo",
    llm=ChatBrowserUse(),
    browser=browser,
)
await agent.run()
```

触发关键词：browser-use、AI 浏览器、云端浏览器、表单填充

#### `chrome-mcp`

Chrome DevTools MCP Server：

- 完整的 Chrome DevTools 能力
- 基于 CDP 协议的稳定自动化
- 深度调试、性能分析
- 远程调试支持

用法：
```bash
# 启动 Chrome 远程调试
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222

# 访问调试页面
open http://localhost:9222
```

触发关键词：chrome-mcp、Chrome 调试、DevTools、CDP、性能分析

### 平台集成技能

#### `feishu-cli`

飞书开放平台命令行工具：

- Markdown ↔ 飞书文档双向无损转换
- 文档管理：创建、编辑、删除、导出
- 知识库管理
- 消息发送（群聊/私聊）
- 权限管理
- 表格操作

基本用法：
```bash
# 创建文档
feishu doc create --title "文档标题" --content "内容"

# 上传 Markdown
feishu doc upload --file README.md --title "README"

# 发送消息
feishu msg send --chat-id "oc_1234567890" --content "Hello"
```

触发关键词：飞书、feishu、lark、文档同步、消息发送

---

**技能使用原则**：

1. 当对话中出现触发关键词时，Agent 会自动加载对应技能
2. 技能文件包含标准流程、格式规范与自动化脚本
3. 足球预测相关技能优先使用 `.trae/skills/` 目录下的版本
4. 详细规范见各技能的 `SKILL.md` 文件

## 当前标准流程

```text
Step 1: 确认联赛、对阵、日期、必要时比赛时间
Step 2: 抓赛程并获取 MatchID / kickoff_time
Step 3: 通过 `collect-data` 复用赛程与已有快照
Step 4: 刷新实时快照（欧赔/亚值/大小球/凯利）
Step 4.5: 自动补 EWMA form、缺失大小球，按需注入 `team_context`
Step 5: 运行 `DomainPredictor` / `EnhancedPredictor` 编排流程
Step 6: 输出最终结论
Step 7: 批量或单场写回 teams_2025-26.md
Step 8: 赛后回填真实比分并更新胜负/比分/大小球准确率
```

## 推荐命令

### 抓赛程

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28
```

### 抓实时快照

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_save_snapshot.py --driver local-chrome --league 英超 --team1 曼联 --team2 布伦特福德 --date 2026-04-28 --time 03:00 --match-id 1296070 --overwrite
```

### 跑单场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match --league premier_league --home-team 曼联 --away-team 布伦特福德 --date 2026-04-28 --json
```

### 批量预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-schedule --league premier_league --date 2026-04-28 --days 1 --json
```

### Harness 入口

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py harness-run --pipeline match_prediction --league premier_league --date 2026-04-28 --home-team 曼联 --away-team 布伦特福德 --json
```

### 批量结果回填

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 bulk_fetch_and_update.py --start 2026-05-01 --end 2026-05-04 --yes
```

## 结果验收标准

预测结果中应优先检查：

- `final_probabilities`
- `top_scores`
- `over_under.line`
- `over_under.line_source`
- `over_under.market.final`
- `realtime.okooo`

若 `line_source=snapshot_final`，说明真实大小球数据已成功进入预测流程。
