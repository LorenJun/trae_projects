---
name: "football-match-analysis"
description: "足球比赛预测分析skill，集成实时欧赔、亚值页大小球、水位、凯利、多模型融合预测与最终结论输出。Invoke when user needs match prediction, betting analysis, or match odds evaluation."
---

# Football Match Analysis

本 Skill 是当前项目的足球预测主技能。执行时必须优先遵循“真实快照 -> 综合预测 -> 输出终版结论”的链路，而不是只靠静态基本面或固定 2.5 大小球线下结论。

## 当前主流程

1. 确认联赛、主队、客队、比赛日期
2. 尽量定位 `match_id`
3. 调用 `okooo_save_snapshot.py` 获取实时快照
4. 调用 `enhanced_prediction_workflow.py` 或 `prediction_system.py predict-match`
5. 输出最终结论时必须包含：
   - 胜平负概率
   - Top 比分
   - 真实大小球盘口线
   - 大/小水位
   - 风险提示与最终建议

## 关键规则

- 大小球不要默认按 `2.5` 解读，除非真实盘口抓取失败
- 澳客移动端里，大小球优先从 `亚值` 页面内的 `大小球` tab 抓取
- 若已知 `match_id`，优先直连抓取，避免重复赛程模糊匹配
- 若球队在赛程里显示简称，需结合 `okooo_team_aliases.json`
- 终版结论必须区分：
  - `模型概率`
  - `盘口信号`
  - `实战建议`

## 推荐数据源

### 一级数据源

- 中国竞彩网：胜平负、让球赔率
- 澳客移动端：欧赔、亚值、凯利、大小球
- 本地联赛文件：`europe_leagues/<league>/teams_2025-26.md`

### 澳客关键入口

- 热门赛事：`https://m.okooo.com/saishi/remen/`
- 欧赔：`https://m.okooo.com/match/odds.php?MatchID=<id>`
- 亚值：`https://m.okooo.com/match/handicap.php?MatchID=<id>`
- 大小球：`亚值页面内大小球 tab`

## 当前技能与 Agent 协同

| 模块 | 作用 | 当前要求 |
|---|---|---|
| 数据采集Agent | 找比赛、抓实时快照 | 优先拿 `match_id` |
| 比赛分析Agent | 基本面/战术/伤停/战意 | 不单独跳过赔率下唯一结论 |
| 赔率分析Agent | 欧赔/亚值/凯利/大小球 | 真实盘口优先，不用固定线 |
| 结果追踪Agent | 赛果回填、准确率更新 | 以 `teams_2025-26.md` 为准 |

## 预测输出最低标准

最终回答至少应包含：

- `胜平负`: 主胜 / 平 / 客胜 概率
- `比分`: Top 2 或 Top 3
- `大小球`: 真实盘口线 + 大/小水位 + 倾向
- `风险`: 冷门 / 平局风险 / 伤停风险
- `结论`: 稳健玩法和进取玩法

## 项目内推荐命令

### 直接跑单场预测

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

默认预测只依赖本地数据 + 实时赔率快照。若希望把“战术倾向/控球率/上一场首发/球员近期评分趋势”也作为结构化输入注入，可开启 team_context 增强：

- 默认开启；如需关闭：`ENABLE_TEAM_CONTEXT=0`
- 近况窗口：`TEAM_CONTEXT_LAST_N=5`
- 注入位置：`analysis_context['team_context']`
- 诊断位置：`realtime.context_applied['team_context']`

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

### 统一 CLI 入口

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match --league premier_league --home-team 曼联 --away-team 布伦特福德 --date 2026-04-28 --json
```

## 常见失败回退

- 没抓到大小球：回退到 `default_2.5`
- 赛程匹配失败：先用 `okooo_fetch_daily_schedule.py` 落当天赛程，再取 `match_id`
- 遇到简称不一致：更新 `okooo_team_aliases.json`
- `/ou/` 被拦截：改走 `handicap.php` 页面内的 `大小球` tab
