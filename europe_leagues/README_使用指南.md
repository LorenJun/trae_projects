# 足球预测系统 - 使用指南

## 系统概览

这是当前项目的正式使用说明，围绕“实时快照 -> 综合预测 -> 写回/输出终版结论”组织。

核心特性：

- 多模型融合预测
- 实时欧赔/亚值/凯利抓取
- 亚值页内 `大小球` tab 抓取真实盘口线与水位
- 预测输出内置 `over_under.market`
- `teams_2025-26.md` 作为正式写回与历史学习来源

## 推荐入口

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

### 赛程预抓

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28
```

## 当前完整预测流程

1. 确认比赛信息：联赛、主客队、日期、必要时比赛时间
2. 若比赛简称可能不一致，先检查 `okooo_team_aliases.json`
3. 优先抓当天赛程获取 `match_id`
4. 调用 `okooo_save_snapshot.py` 刷新实时快照
5. 调用 `EnhancedPredictor.predict_match()` 预测
6. 输出终版结论时，优先使用：
   - `final_probabilities`
   - `top_scores`
   - `over_under.line`
   - `over_under.market.final`

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

## 可选：球队状态增强（SofaScore）

项目支持在预测时自动抓取双方近 N 场的“阵型/控球/上一场首发/球员评分趋势”，并注入到 `analysis_context['team_context']`。

- 默认开启；如需关闭：`ENABLE_TEAM_CONTEXT=0`
- 场次数：`TEAM_CONTEXT_LAST_N=5`

示例：

```bash
cd /Users/bytedance/trae_projects
ENABLE_TEAM_CONTEXT=1 TEAM_CONTEXT_LAST_N=5 python3 - <<'PY'
import sys
sys.path.insert(0,'/Users/bytedance/trae_projects/europe_leagues')
from enhanced_prediction_workflow import EnhancedPredictor

p = EnhancedPredictor()
r = p.predict_match('曼联','布伦特福德','premier_league', match_date='2026-04-28', match_time='03:00', match_id='1296070', force_refresh_odds=False)
print(r['analysis_context'].get('team_context', {}).get('ok'))
PY
```

## 常用文件

- `enhanced_prediction_workflow.py`：当前主预测流程
- `okooo_save_snapshot.py`：当前主抓取脚本
- `okooo_fetch_daily_schedule.py`：当天赛程与 MatchID 抓取
- `okooo_live_snapshot.py`：实时快照读取与转换
- `okooo_team_aliases.json`：球队简称映射
- `result_manager.py`：赛果回填与准确率统计

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
