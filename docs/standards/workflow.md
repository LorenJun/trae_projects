---
document_title: "工作流程规范"
version: "1.1.0"
last_updated: "2026-04-27"
---

# 工作流程规范

本规范定义当前项目正式使用的足球预测工作流。所有流程说明都应与以下口径保持一致：

- 以 `europe_leagues/<league>/teams_2025-26.md` 为单一事实来源
- 先定位 `match_id`
- 再刷新实时快照
- 大小球优先从 `亚值` 页面内 `大小球` tab 抓取
- 最终预测检查 `over_under.line`、`line_source`、`over_under.market.final`

## 完整工作流总览

```text
Step 1: 确认比赛信息
        ↓
Step 2: 获取赛程与 MatchID
        ↓
Step 3: 刷新实时快照（欧赔/亚值/大小球/凯利）
        ↓
Step 3.5 (可选): 注入球队状态增强（SofaScore team_context）
        ↓
Step 4: 基本面分析
        ↓
Step 5: 赔率分析
        ↓
Step 6: 综合预测并输出最终结论
        ↓
Step 7: 需要落盘时写回 teams_2025-26.md
        ↓
Step 8: 比赛结束后回填赛果
        ↓
Step 9: 更新准确率与历史学习数据
```

## Agent 执行约束

### 必守规则

- 以 `europe_leagues/<league>/teams_2025-26.md` 作为唯一正式落盘位置
- 优先调用 `prediction_system.py` 的非交互子命令，并统一附带 `--json`
- 除非用户明确要求只读查询，否则涉及预测、回填、统计的任务都应按既定步骤执行，不得跳步
- 历史目录、示例模板、旧版 `predictions/` 和 `reports/` 只可参考，不可作为主流程输出目标
- 缺少实时依赖时可以降级，但必须在输出中标注“降级/模拟数据”，不可混淆为真实临场数据

### 偏航纠正提示词

```text
如果当前任务与标准预测流程不完全一致，请先把任务映射到以下六类之一：环境检查、数据采集、赛前预测、赛果回填、准确率统计、只读查询。

执行规则：
1. 只读查询：允许仅读文档或读取联赛文件，不写入。
2. 数据采集：必须优先确认联赛、日期、球队、可选时间，并尽量获取 match_id。
3. 赛前预测：必须经过“确认比赛 -> 抓赛程/拿 match_id -> 刷新实时快照 -> 分析 -> 生成预测 -> 写回 teams_2025-26.md（如需要）”。
4. 赛果回填：必须经过“确认比赛 -> 校验比分 -> save-result -> 必要时刷新 accuracy”。
5. 准确率统计：直接读取 teams_2025-26.md 与运行时统计结果，不依赖旧模板文件。
6. 若准备创建新的预测 markdown、结果 markdown 或临时主流程文件，视为偏航，立即停止并改回 teams_2025-26.md。
7. 若数据不足，先说明缺口，再执行 collect-data 或降级方案，不允许跳过采集直接输出确定性结论。
```

## 任务类型与推荐入口

| 任务类型 | 推荐入口 | 是否允许写文件 |
|------|------|------|
| 环境检查 | `python3 prediction_system.py health-check --json` | 否 |
| 数据采集 | `python3 prediction_system.py collect-data ... --json` | 否 |
| 单场预测 | `python3 prediction_system.py predict-match ... --json` | 是 |
| 批量预测 | `python3 prediction_system.py predict-schedule ... --json` | 是 |
| 赛果回填 | `python3 prediction_system.py save-result ... --json` | 是 |
| 准确率统计 | `python3 prediction_system.py accuracy --refresh --json` | 是 |
| 待回填赛果查询 | `python3 prediction_system.py pending-results --days-back 14 --json` | 否 |

## Step 1: 确认比赛信息

需要确认：

- 联赛
- 日期
- 主队
- 客队
- 必要时比赛时间

若可能存在简称差异，应先检查：

- `europe_leagues/okooo_team_aliases.json`

## Step 2: 获取赛程与 MatchID

推荐做法：

1. 先抓当天联赛赛程
2. 从赛程 JSON 中定位目标比赛
3. 取出 `match_id`
4. 若球队名不一致，用别名映射修正

推荐命令：

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28
```

输出目录：

- `europe_leagues/.okooo-scraper/schedules/<league>/`

## Step 3: 刷新实时快照

当前正式快照脚本：

- `europe_leagues/okooo_save_snapshot.py`

当前正式快照内容应优先包含：

- `欧赔`
- `亚值`
- `大小球`
- `凯利`

### 大小球真实入口

大小球优先走：

- `https://m.okooo.com/match/handicap.php?MatchID=<id>` 页面内的 `大小球` tab

不是优先走：

- `/ou/`
- `/overunder.php`
- `/daxiao.php`

推荐命令：

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_save_snapshot.py \
  --driver local-chrome \
  --league 英超 \
  --team1 曼联 \
  --team2 布伦特福德 \
  --date 2026-04-28 \
  --time 03:00 \
  --match-id 1296070 \
  --overwrite
```

## Step 3.5 (可选): 注入球队状态增强（SofaScore team_context）

用途：将“近 N 场阵型/控球/上一场首发/球员评分趋势”等信息结构化注入预测上下文，用于提升基本面维度的可量化输入。

- 默认开启；如需关闭：`ENABLE_TEAM_CONTEXT=0`
- 近况窗口：`TEAM_CONTEXT_LAST_N=5`
- 注入位置：`analysis_context['team_context']`
- 缓存：`europe_leagues/.okooo-scraper/runtime/sofascore_team_ids.json`

注意：该步骤为 best-effort，抓取失败不会阻断预测，只会在 `realtime.context_applied.team_context` 中记录错误信息。

## Step 4: 基本面分析

由：

- `agents/match_analyzer_agent.md`

负责内容：

- 排名与实力
- 近期状态
- 战术打法
- 历史交锋
- 伤停与战意
- 与真实盘口节奏是否一致

## Step 5: 赔率分析

由：

- `agents/odds_analyzer_agent.md`

负责内容：

- 欧赔变化
- 亚值变化
- 凯利信号
- 真实大小球盘口线与水位
- 盘口错配与风险提示

## Step 6: 综合预测并输出终版结论

当前正式预测流程：

- `europe_leagues/enhanced_prediction_workflow.py`
- 或 `prediction_system.py predict-match`

终版输出至少应检查：

- `final_probabilities`
- `top_scores`
- `over_under.line`
- `over_under.line_source`
- `over_under.market.final`

当看到：

- `line_source=snapshot_final`

说明真实大小球数据已成功注入预测。

## Step 7: 写回正式文件

若任务要求落盘，只写：

- `europe_leagues/<league>/teams_2025-26.md`

禁止作为主流程目标的旧位置：

- `predictions/`
- `reports/`
- `analysis/predictions/*.md`
- `analysis/results/*.md`

## Step 8: 赛果回填

推荐入口：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py save-result --match-id <match_id> --home-score <n> --away-score <n> --json
```

或：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 result_manager.py --update-accuracy
```

## Step 9: 更新准确率与历史学习数据

当前统计应围绕：

- 胜平负命中率
- 比分 Top 命中率
- 大小球命中率
- 历史复盘与学习数据更新

运行时产物目录：

- `europe_leagues/.okooo-scraper/runtime/`

## 质量检查清单

### 赛前阶段

- 已确认比赛信息
- 已拿到正确 `match_id`
- 已刷新实时快照
- 快照内包含 `欧赔/亚值/大小球/凯利`
- 大小球优先来自 `亚值` 页内 tab
- 预测输出已带 `over_under.market.final`

### 赛后阶段

- 已回填真实比分
- 已更新准确率统计
- 已将有效复盘沉淀到历史学习数据

## 异常处理

### 1. 赛程匹配失败

优先处理：

- 检查日期
- 检查比赛时间
- 检查球队简称
- 更新 `okooo_team_aliases.json`

### 2. 大小球抓取失败

优先处理：

- 检查是否真的进入 `handicap.php`
- 检查是否成功点开 `大小球` tab
- 检查是否被风控拦截
- 若仍失败，可 fallback，但必须在结果中说明已回退默认线

### 3. 结果写回位置错误

若发现流程试图写入旧目录，应立即停止并改回：

- `europe_leagues/<league>/teams_2025-26.md`

## 最佳实践

1. 已知 `match_id` 时直接抓快照，减少不必要的模糊匹配
2. 比赛前 2 小时内重新刷新一次实时快照
3. 真实大小球线存在时，禁止继续按固定 `2.5` 解读比赛
4. 单场终版结论里明确带出盘口线与大/小水位
5. 赛后及时回填赛果并更新准确率
