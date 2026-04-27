# 澳客实时赔率与大小球抓取指南

## 目标

本指南描述项目当前已经跑通的实时赔率抓取链路，重点覆盖：

- 从 `https://m.okooo.com/saishi/remen/` 或联赛赛程页定位比赛
- 获取 `MatchID`
- 抓取欧赔、亚值、凯利
- 在 `亚值` 页面内切换到 `大小球` 子 tab 抓取真实盘口线与水位
- 将快照直接交给预测流程，输出最终结论

## 当前主流程

1. 先定位比赛：联赛、主客队、日期、必要时补 `match_time`
2. 优先获取 `match_id`
3. 调用 `okooo_save_snapshot.py` 生成实时快照 JSON
4. 由 `enhanced_prediction_workflow.py` 自动读取快照并预测
5. 输出中检查：
   - `over_under.line`
   - `over_under.line_source`
   - `over_under.market.final`

## 关键事实

- 欧赔入口：`https://m.okooo.com/match/odds.php?MatchID=<MatchID>`
- 亚值入口：`https://m.okooo.com/match/handicap.php?MatchID=<MatchID>`
- 大小球真实位置：`亚值页面内的「大小球」tab`，不是优先依赖独立 `/ou/` 页面
- 快照输出目录：`europe_leagues/.okooo-scraper/snapshots/<league>/`
- 赛程缓存目录：`europe_leagues/.okooo-scraper/schedules/<league>/`

## 推荐命令

### 1. 先抓当天赛程并拿到 MatchID

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28
```

输出示例：

- `europe_leagues/.okooo-scraper/schedules/premier_league/2026-04-28.json`

### 2. 用 MatchID 直接抓实时快照

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
  --out-dir /Users/bytedance/trae_projects/europe_leagues/.okooo-scraper/snapshots \
  --overwrite
```

### 3. 直接跑最终预测

```bash
cd /Users/bytedance/trae_projects
python3 - <<'PY2'
import sys, json
sys.path.insert(0,'/Users/bytedance/trae_projects/europe_leagues')
from enhanced_prediction_workflow import EnhancedPredictor

p = EnhancedPredictor()
r = p.predict_match(
    '曼联',
    '布伦特福德',
    'premier_league',
    match_date='2026-04-28',
    match_time='03:00',
    match_id='1296070',
    okooo_driver='local-chrome',
    force_refresh_odds=True,
)
print(json.dumps(r['over_under'], ensure_ascii=False, indent=2))
PY2
```

## 快照字段说明

`okooo_save_snapshot.py` 当前输出的核心字段：

```json
{
  "match_id": "1296070",
  "欧赔": {
    "initial": {"home": 2.06, "draw": 3.45, "away": 3.10},
    "final": {"home": 1.92, "draw": 3.78, "away": 3.74}
  },
  "亚值": {
    "initial": {"home_water": 1.85, "handicap_text": "半/一", "away_water": 1.99},
    "final": {"home_water": 1.89, "handicap_text": "平/半", "away_water": 2.96}
  },
  "大小球": {
    "found": true,
    "initial": {"over": 1.86, "line": 3.0, "under": 1.94},
    "final": {"over": 1.86, "line": 3.0, "under": 1.94},
    "_flow": "asian_inner_tab"
  },
  "凯利": {
    "initial": {"home": 0.93, "draw": 0.93, "away": 0.93},
    "final": {"home": 0.95, "draw": 0.95, "away": 0.95}
  }
}
```

## 预测输出检查点

`EnhancedPredictor.predict_match()` 的大小球输出现在应至少包含：

```json
{
  "line": 3.0,
  "line_source": "snapshot_final",
  "market": {
    "final": {"over": 1.86, "line": 3.0, "under": 1.94},
    "initial": {"over": 1.86, "line": 3.0, "under": 1.94}
  }
}
```

其中：

- `line_source=snapshot_final` 表示真实盘口线来自实时快照
- `market.final` 表示最终使用的真实大/小水位
- 若抓取失败，会退回 `default_2.5`

## 稳定性策略

### 优先级

1. 已知 `match_id` 时，直接抓，不再反复在赛程里模糊匹配
2. 未知 `match_id` 时，优先用 `okooo_fetch_daily_schedule.py` 先落赛程 JSON
3. 球队存在简称时，维护 `okooo_team_aliases.json`
4. 大小球优先走 `亚值页 -> 大小球 tab`
5. `/ou/`、`overunder.php`、`daxiao.php` 仅作为 fallback

### 环境变量

- `OKOOO_REFRESH_LIVE=0`：关闭预测前自动刷新实时快照
- `OKOOO_AUTO_TOTALS=0`：关闭缺失大小球时的自动补抓
- `ENABLE_PREDICTION_CACHE=1`：本地开启预测缓存，默认关闭

## 常见问题

### 1. 为什么抓到了欧赔和亚值，但大小球还是 2.5？

原因通常有三种：

- 没有抓到 `大小球` 字段
- `大小球.final.line` 为空
- 预测时没有成功读到该快照

排查顺序：

1. 先看快照 JSON 有没有 `大小球.found=true`
2. 再看 `over_under.line_source` 是否为 `snapshot_final`
3. 再看 `over_under.market.final` 是否有 `over/line/under`

### 2. 为什么赛程里找不到球队？

常见原因：

- 澳客使用简称，例如 `布伦特` 而非 `布伦特福德`
- 日期没有对上
- 未传 `match_time`

处理方式：

- 更新 `okooo_team_aliases.json`
- 先跑 `okooo_fetch_daily_schedule.py`
- 已知 MatchID 后直接传 `--match-id`

### 3. 哪个脚本是当前正式流程？

以这两个为准：

- `okooo_save_snapshot.py`
- `enhanced_prediction_workflow.py`
