# 足球预测系统 - 使用指南

> 当前正式流程  
> 1. `prediction_system.py collect-data` 或赛程抓取定位 `match_id`  
> 2. `prediction_system.py predict-match / predict-schedule` 执行增强预测  
> 3. 结果写回 `europe_leagues/<league>/teams_2025-26.md`  
> 4. 赛后用 `prediction_system.py save-result` 或 `bulk_fetch_and_update.py` 回填  
> 5. 最后用 `prediction_system.py accuracy --refresh --json` 刷新胜负 / 比分 / 大小球统计  
> 可审计编排入口：`prediction_system.py harness-run --pipeline ... --json`  
> 关键检查项：`over_under.line`、`line_source`、`over_under.market.final`

## 统一职业身份

当前项目默认采用统一身份：

- `专业纬度足彩数据精算师`

身份定义与职责边界见：

- `../agents/football_actuary_persona.md`

执行本使用指南中的任何预测、回填、统计或编排任务时，都应同时兼顾：

- 足球业务分析
- 赔率与盘口交易分析
- 概率建模
- 统计验证与模型评估
- 风险控制与资金管理
- 数据工程、流程自动化与策略迭代

输出时应明确区分：

- `模型结论`
- `盘口结论`
- `综合结论`

对于跨联赛、杯赛、样本不足、实时快照缺失等场景，必须显式标注边界与风险，不允许只基于单一维度给出强确定性结论。

## 系统概览

这是当前项目的正式使用说明，围绕“collect-data / 赛程 -> 实时快照 -> 综合预测 -> 写回/统计”组织。

核心特性：

- 多模型融合预测
- `collect-data` 统一采集赛程、`match_id` 与本地快照上下文
- 实时欧赔/亚值/凯利抓取
- 亚值页内 `大小球` tab 抓取真实盘口线与水位
- 预测输出内置 `over_under.market`
- `EnhancedPredictor` 自动补 EWMA form、缺失大小球、可选 `team_context`
- `teams_2025-26.md` 作为正式写回与历史学习来源
- 支持 `bulk_fetch_and_update.py` 批量赛果回填
- 支持 `harness-run` 阶段化编排

## 推荐入口

### 标准采集入口

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league premier_league --date 2026-04-28 --json
```

### 单场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match --league premier_league --home-team 曼联 --away-team 布伦特福德 --date 2026-04-28 --json
```

### 批量预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-schedule --league premier_league --date 2026-04-28 --days 1 --json
```

### Harness 编排

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py harness-run --pipeline match_prediction --league premier_league --date 2026-04-28 --home-team 曼联 --away-team 布伦特福德 --json
```

### 赛程预抓

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28
```

## 当前完整预测流程

1. 确认比赛信息：联赛、主客队、日期、必要时比赛时间
2. 若比赛简称可能不一致，先检查 `okooo_team_aliases.json`
3. 优先调用 `prediction_system.py collect-data`，或抓当天赛程获取 `match_id`
4. 调用 `prediction_system.py predict-match` / `predict-schedule`
5. 由 `EnhancedPredictor.predict_match()` 自动：
   - 刷新实时快照
   - 缺失大小球时自动补抓
   - 回填 EWMA 近况
   - 按需注入 `team_context`
6. 输出终版结论时，优先检查：
   - `final_probabilities`
   - `top_scores`
   - `over_under.line`
   - `over_under.line_source`
   - `over_under.market.final`
   - `realtime.okooo`
7. 若任务要求落盘，只写 `teams_2025-26.md`
8. 赛后用 `save-result` 或 `bulk_fetch_and_update.py` 回填，再用 `accuracy --refresh` 更新统计

## 大小球的当前规则

### 真实入口

大小球真实数据优先来自：

- `https://m.okooo.com/match/handicap.php?MatchID=<id>` 页面内的 `大小球` tab

不是优先依赖：

- `/ou/`
- `/overunder.php`
- `/daxiao.php`

### 预测系统里的使用优先级

1. `analysis_context['ou_line']`
2. `current_odds['大小球']['final']['line']`
3. 默认 `2.5`

### 最终输出检查

看到下面这种结构，才说明真实大小球已经真正注入预测：

```json
{
  "line": 3.0,
  "line_source": "snapshot_final",
  "market": {
    "final": {"over": 1.86, "line": 3.0, "under": 1.94}
  }
}
```

## 当前推荐命令

### 直接抓快照

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

### 直接跑 Python 单场预测

```bash
cd /Users/bytedance/trae_projects
python3 - <<'PY'
import sys, json
sys.path.insert(0,'/Users/bytedance/trae_projects/europe_leagues')
from enhanced_prediction_workflow import EnhancedPredictor
p = EnhancedPredictor()
r = p.predict_match(
    '曼联', '布伦特福德', 'premier_league',
    match_date='2026-04-28',
    match_time='03:00',
    match_id='1296070',
    okooo_driver='local-chrome',
    force_refresh_odds=True,
)
print(json.dumps(r['over_under'], ensure_ascii=False, indent=2))
PY
```

### 标准 CLI 单场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match \
  --league premier_league \
  --home-team 曼联 \
  --away-team 布伦特福德 \
  --date 2026-04-28 \
  --time 03:00 \
  --match-id 1296070 \
  --json
```

### 批量结果回填

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 bulk_fetch_and_update.py --start 2026-05-01 --end 2026-05-04 --yes
```

### 刷新准确率

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py accuracy --refresh --json
```

## 可选：球队状态增强（SofaScore）

项目支持在预测时自动抓取双方近 N 场的“阵型/控球/上一场首发/球员评分趋势”，并注入到 `analysis_context['team_context']`。

- 默认开启；如需关闭：`ENABLE_TEAM_CONTEXT=0`
- 场次数：`TEAM_CONTEXT_LAST_N=5`

示例：

```bash
cd /Users/bytedance/trae_projects
TEAM_CONTEXT_LAST_N=5 python3 - <<'PY'
import sys
sys.path.insert(0,'/Users/bytedance/trae_projects/europe_leagues')
from enhanced_prediction_workflow import EnhancedPredictor

p = EnhancedPredictor()
r = p.predict_match('曼联','布伦特福德','premier_league', match_date='2026-04-28', match_time='03:00', match_id='1296070', force_refresh_odds=False)
print(r['analysis_context'].get('team_context', {}).get('ok'))
PY
```

## 常用文件

- `prediction_system.py`：当前正式 CLI 入口
- `enhanced_prediction_workflow.py`：当前主预测流程
- `okooo_save_snapshot.py`：当前主抓取脚本
- `okooo_fetch_daily_schedule.py`：当天赛程与 MatchID 抓取
- `okooo_live_snapshot.py`：实时快照读取与转换
- `okooo_team_aliases.json`：球队简称映射
- `bulk_fetch_and_update.py`：批量赛果回填
- `harness/football.py`：Harness pipeline 定义
- `result_manager.py`：赛果回填与准确率统计底层实现

## 运行时目录

- `europe_leagues/.okooo-scraper/snapshots/`
- `europe_leagues/.okooo-scraper/schedules/`
- `europe_leagues/.okooo-scraper/runtime/`

## 常见问题

### 为什么输出里还是 2.5？

说明真实大小球没有成功注入，检查：

- 快照里是否有 `大小球.found=true`
- `line_source` 是否为 `snapshot_final`
- `market.final` 是否非空

### 为什么定位不到比赛？

优先排查：

- 球队是否是简称
- 是否缺少日期
- 是否缺少比赛时间
- 是否未先获取 `match_id`

### 正式写回写到哪里？

只写：

- `europe_leagues/<league>/teams_2025-26.md`

### 赛后怎么统一更新结果和准确率？

推荐两种入口：

- 单场：`prediction_system.py save-result --match-id ... --home-score ... --away-score ... --json`
- 批量：`bulk_fetch_and_update.py --start ... --end ... --yes`

更新后再执行：

- `prediction_system.py accuracy --refresh --json`
