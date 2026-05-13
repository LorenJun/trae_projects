---
document_title: "数据格式规范"
version: "1.4.0"
last_updated: "2026-05-08"
---

# 数据格式规范

> 当前正式流程  
> 1. `prediction_system.py collect-data` 或赛程抓取定位 `match_id`  
> 2. `prediction_system.py predict-match / predict-schedule` 执行增强预测，并自动接入 RAG 记忆层  
> 3. 五大联赛 SoT 写回 `europe_leagues/<league>/teams_2025-26.md`；欧战/杯赛写入 `MEMORY.md` 与 runtime-only 归档  
> 4. 赛后用 `prediction_system.py save-result`、`auto-sync-results`、`result-sync-daemon` 或 `bulk_fetch_and_update.py` 回填  
> 5. 最后用 `prediction_system.py accuracy --refresh --json` 刷新胜负 / 比分 / 大小球统计  
> 可审计编排入口：`prediction_system.py harness-run --pipeline ... --json`  
> 关键检查项：`over_under.line`、`line_source`、`over_under.market.final`、`retrieved_memory_explanation`

本规范定义当前项目正式使用的数据格式，确保以下几类数据在项目内保持一致：

- 联赛主文件 `teams_2025-26.md`
- 滚动记忆 `MEMORY.md`
- `collect-data` / Harness 输入输出结构
- 澳客实时快照 JSON
- 预测输出结构
- 赛果回填与准确率统计相关结构

## 当前事实与写回边界

正式主流程采用双路径：

- 五大联赛 SoT：`europe_leagues/<league>/teams_2025-26.md`
- 欧战/杯赛：`/Users/bytedance/trae_projects/MEMORY.md`
- 运行时归档：`prediction_archive.json`、`prediction_memory_odds_samples.json`、`result_sync_registry.json`
- RAG 索引：`rag_cases.json`、`rag_index.json`、`rag_registry.json`
- 欧战正式 competition config：`europa_league`、`champions_league`、`conference_league`
- 欧战快照目录别名：允许 `欧联 / 欧罗巴 / 欧冠 / 欧协联` 与 canonical `league_code` 双向兼容
- 历史欧战归档迁移：统一回填 `league_code`、`league_name`、`snapshot_dir`、`snapshot_dir_aliases`、`snapshot_path`、`line_source`

运行时数据允许写入：

- `europe_leagues/.okooo-scraper/snapshots/`
- `europe_leagues/.okooo-scraper/schedules/`
- `europe_leagues/.okooo-scraper/runtime/`

以下旧目录不再作为正式主流程输出目标：

- `predictions/`
- `reports/`
- `analysis/predictions/*.md`
- `analysis/results/*.md`

## 目录结构

```text
docs/
└── standards/
    ├── coding_standards.md
    ├── data_format.md
    └── workflow.md
```

## Markdown 表格格式

### 通用表格规范

- 数字列右对齐
- 文字列左对齐
- 状态列可居中

示例：

```markdown
| 文字列（左对齐） | 数字列（右对齐） | 状态列（居中） |
|-----------------|----------------:|:--------------:|
| 内容1           |              10 |      ✅        |
| 内容2           |              20 |      ❌        |
```

## 联赛主文件格式

### 文件位置

- `europe_leagues/<league>/teams_2025-26.md`

### 赛程表结构

| 字段 | 类型 | 示例 | 说明 |
|------|------|------|------|
| 日期 | Date | `2026-04-28` | 比赛日期 |
| 时间 | Time | `03:00` | 开球时间 |
| 主队 | String | `曼联` | 主队名称 |
| 比分 | String | `-` / `1-0` | 未赛为 `-`，已赛为 `x-y` |
| 客队 | String | `布伦特福德` | 客队名称 |
| 备注 | String | `预测:主胜；比分:1-0/1-1；大小:小3.0(...)` | 预测摘要与赛后标记 |

示例：

```markdown
| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |
|------|------|------|------|------|------|
| 2026-04-28 | 03:00 | 曼联 | - | 布伦特福德 | 预测:主胜；比分:1-0/1-1；大小:小3.0(大12.5%/小87.5%) |
```

## 滚动记忆 `MEMORY.md` 格式

### 文件位置

- `/Users/bytedance/trae_projects/MEMORY.md`

### 当前预测滚动区块

```markdown
<!-- prediction-memory:start -->
> 滚动预测准确率： 已完赛 2 场 | 胜平负 100.0% (2/2) | 比分 0.0% (0/2) | 大小球 50.0% (1/2)

- [europa_league|阿斯顿维拉|诺丁汉森林] 2026-05-08 欧联半决赛 阿斯顿维拉 vs 诺丁汉森林 -> 主胜 (46.7%) | 比分: 1-0 > 2-0 > 1-1 | 大小球: 小球 2.5 (56.9%) | 盘口: ... | 风险: ... | 赛果: 主胜 4-0 | RAG记忆: ... | 记忆ID: 1324666 | 更新时间: 2026-05-08 11:58:00
<!-- prediction-memory:end -->
```

### 关键字段

- `RAG记忆:`：由主预测链原生写入的 `retrieved_memory_explanation`
- `记忆ID:`：优先使用真实 `match_id` 或 canonical 记忆键
- `赛果:`：完赛后回填真实结果
- `更新时间:`：该条记忆最后刷新时间

## 澳客赛程 JSON 格式

### 文件位置

- `europe_leagues/.okooo-scraper/schedules/<league>/<date>.json`

### 核心字段

```json
{
  "league": "premier_league",
  "date": "2026-04-28",
  "matches": [
    {
      "match_id": "1296070",
      "time": "03:00",
      "kickoff_time": "03:00",
      "home_team": "曼联",
      "away_team": "布伦特",
      "status": "待进行",
      "history_url": "https://m.okooo.com/match/history.php?MatchID=1296070",
      "score": null,
      "home_score": null,
      "away_score": null
    }
  ]
}
```

说明：

- `away_team` 可能是简称，如 `布伦特`
- 昵称、简称差异需通过 `europe_leagues/okooo_team_aliases.json` 处理
- 已结束比赛允许带 `score`、`home_score`、`away_score`
- 批量回填流程会直接消费赛程中的 `status=已结束` 与比分字段

## `collect-data` 输出结构

### 推荐用途

- 作为 `predict-match`、`harness-run --pipeline match_prediction` 的上游输入
- 作为批量赛程分析与数据检查的标准化返回

### 推荐结构

```json
{
  "league": "premier_league",
  "date": "2026-04-28",
  "count": 1,
  "matches": [
    {
      "match_id": "1296070",
      "home_team": "曼联",
      "away_team": "布伦特福德",
      "match_time": "03:00",
      "status": "待进行",
      "sources": ["okooo_schedule", "local_snapshot"]
    }
  ]
}
```

说明：

- `collect-data` 会优先复用赛程中的 `match_id`
- 若本地已存在快照，允许通过 `sources` 反映已挂接到 `odds_data`
- 该结构是预测主流程的标准上游形态之一

## 澳客实时快照 JSON 格式

### 文件位置

- `europe_leagues/.okooo-scraper/snapshots/<league>/<主队>vs<客队>.json`

### 当前推荐结构

```json
{
  "captured_at": "2026-04-28T02:05:10",
  "driver": "local-chrome",
  "match_id": "1296070",
  "league": "premier_league",
  "match_date": "2026-04-28",
  "match_time": "03:00",
  "event": "曼联vs布伦特福德",
  "home_team": "曼联",
  "away_team": "布伦特福德",
  "schedule": {
    "text": "曼联 03:00 布伦特福德",
    "href": "https://m.okooo.com/match/history.php?MatchID=1296070"
  },
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
    "delta": {"over": 0.0, "line": 0.0, "under": 0.0},
    "_flow": "asian_inner_tab"
  },
  "凯利": {
    "initial": {"home": 0.93, "draw": 0.93, "away": 0.93},
    "final": {"home": 0.95, "draw": 0.95, "away": 0.95}
  }
}
```

### `大小球` 字段规范

| 字段 | 类型 | 示例 | 说明 |
|------|------|------|------|
| `found` | Boolean | `true` | 是否成功抓到大小球 |
| `initial.over` | Float | `1.86` | 初始大球水位 |
| `initial.line` | Float | `3.0` | 初始盘口线 |
| `initial.under` | Float | `1.94` | 初始小球水位 |
| `final.over` | Float | `1.86` | 即时大球水位 |
| `final.line` | Float | `3.0` | 即时盘口线 |
| `final.under` | Float | `1.94` | 即时小球水位 |
| `delta.over` | Float | `0.0` | 大球水位变化 |
| `delta.line` | Float | `0.0` | 盘口线变化 |
| `delta.under` | Float | `0.0` | 小球水位变化 |
| `_flow` | String | `asian_inner_tab` | 抓取路径来源 |
| `_fallback_from` | String | `history_tab` | 若为 fallback，记录前序链路来源 |

### `_flow` 推荐取值

| 值 | 说明 |
|------|------|
| `asian_inner_tab` | 从 `handicap.php` 页面内 `大小球` tab 抓到 |
| `overunder_php` | 从 `overunder.php` fallback 抓到 |
| `daxiao_php` | 从 `daxiao.php` fallback 抓到 |
| `desktop_ou` | 从桌面 `/ou/` fallback 抓到 |

项目当前推荐优先级：

1. `asian_inner_tab`
2. `overunder.php` / `daxiao.php`
3. `desktop /ou/`

## 预测输入 `current_odds` 结构

`EnhancedPredictor.predict_match()` 内部使用的 `current_odds` 推荐结构：

```json
{
  "欧赔": {
    "initial": {"home": 2.06, "draw": 3.45, "away": 3.10},
    "final": {"home": 1.92, "draw": 3.78, "away": 3.74}
  },
  "亚值": {
    "initial": {"home_water": 1.85, "handicap_text": "半/一", "away_water": 1.99},
    "final": {"home_water": 1.89, "handicap_text": "平/半", "away_water": 2.96}
  },
  "大小球": {
    "initial": {"over": 1.86, "line": 3.0, "under": 1.94},
    "final": {"over": 1.86, "line": 3.0, "under": 1.94}
  },
  "凯利": {
    "initial": {"home": 0.93, "draw": 0.93, "away": 0.93},
    "final": {"home": 0.95, "draw": 0.95, "away": 0.95}
  }
}
```

说明：

- `current_odds` 一般由实时快照提取而来
- 若预测前刷新失败，也可能来自本地已有 snapshot 或采集阶段挂接的数据
- 即使使用 `--no-refresh-odds`，若本地已存在同 `match_id` 快照，主链也会优先复用真实盘口

## 预测输出格式

### 核心输出字段

```json
{
  "prediction": "主胜",
  "confidence": 0.64,
  "final_probabilities": {
    "home_win": 0.42,
    "draw": 0.30,
    "away_win": 0.28
  },
  "top_scores": [
    {"score": "1-0", "probability": 0.18},
    {"score": "1-1", "probability": 0.15}
  ],
  "over_under": {
    "over": 0.125,
    "under": 0.875,
    "total_lambda": 1.90,
    "line": 3.0,
    "line_source": "snapshot_final",
    "market": {
      "initial": {"over": 1.86, "line": 3.0, "under": 1.94},
      "final": {"over": 1.86, "line": 3.0, "under": 1.94}
    }
  },
  "realtime": {
    "okooo": {
      "attempted": true,
      "refreshed": true,
      "snapshot_path": "/Users/bytedance/trae_projects/europe_leagues/.okooo-scraper/snapshots/premier_league/曼联vs布伦特福德.json",
      "match_id": "1296070",
      "driver": "local-chrome",
      "headed": false,
      "errors": []
    },
    "context_applied": {
      "ewma_form": {"home": {"available": true}, "away": {"available": true}},
      "okooo_totals_fetch": {"attempted": true, "ok": true},
      "team_context": {"attempted": true, "ok": true}
    }
  },
  "retrieved_memory": {
    "summary": {
      "retrieved_count": 8,
      "market_case_count": 5,
      "live_market_followup": {
        "applied": true,
        "eligible_count": 2,
        "eligible_match_ids": ["1326947", "1302909"],
        "avg_path_score": 0.72,
        "avg_weight_bonus": 0.05,
        "supported_outcome_rate": 0.5,
        "dominant_historical_outcome": "主胜",
        "recommended_action": "observe",
        "advice": "临场建议: 历史盘口轨迹样本已命中，但支持方向仍不够集中，建议继续观察实时盘口变化。"
      }
    },
    "similar_cases": [],
    "market_cases": [],
    "upset_cases": []
  },
  "retrieved_memory_explanation": "RAG召回5场相似比赛，其中已完赛4场...",
  "live_betting_advice": "临场建议: 历史盘口轨迹样本已命中，但支持方向仍不够集中，建议继续观察实时盘口变化。"
}
```

当缺失真实大小球盘口时，正式输出会改为：

```json
{
  "over_under": {
    "available": false,
    "reason": "missing_real_line",
    "line": null,
    "line_source": "missing_real_line",
    "market": {
      "initial": null,
      "final": null
    }
  }
}
```

### `over_under` 字段规范

| 字段 | 类型 | 示例 | 说明 |
|------|------|------|------|
| `over` | Float | `0.125` | 大球概率 |
| `under` | Float | `0.875` | 小球概率 |
| `total_lambda` | Float | `1.90` | 预期总进球 |
| `line` | Float | `3.0` | 实际用于计算的盘口线 |
| `line_source` | String | `snapshot_final` | 盘口线来源 |
| `market.initial.over` | Float | `1.86` | 初始大球水位 |
| `market.initial.line` | Float | `3.0` | 初始盘口线 |
| `market.initial.under` | Float | `1.94` | 初始小球水位 |
| `market.final.over` | Float | `1.86` | 即时大球水位 |
| `market.final.line` | Float | `3.0` | 即时盘口线 |
| `market.final.under` | Float | `1.94` | 即时小球水位 |

### `line_source` 推荐取值

| 值 | 说明 |
|------|------|
| `analysis_context` | 显式由上下文覆盖 |
| `snapshot_final` | 来自实时快照 `大小球.final.line` |
| `snapshot_initial` | 来自实时快照 `大小球.initial.line` |
| `missing_real_line` | 未抓到真实盘口，正式链路不再输出默认盘口结论 |

### 使用规则

- 当 `line_source=snapshot_final` 时，说明真实大小球已成功注入预测
- 当 `line_source=missing_real_line` 时，允许保留胜平负预测，但正式大小球结论应显示“待补真实盘口”
- 当 `market.final` 非空时，最终结论中应同步展示大/小水位
- 不允许把真实 `3.0`、`2.75` 的比赛继续按固定 `2.5` 解释
- 不允许在正式归档、MEMORY 或准确率统计里把缺真实盘口样本伪装成 `default_2.5`
- 若 `realtime.okooo.refreshed=false`，应视为旧快照或降级数据，需要在结论中明确标注
- `retrieved_memory_explanation` 非空时，说明 RAG 记忆层已参与最终解释
- `retrieved_memory.summary.live_market_followup` 非空时，说明历史盘口轨迹门槛已参与 RAG 增强与临场建议生成
- `live_betting_advice` 非空时，说明当前比赛已命中“`1X2` 赔率接近 + 大小球变化接近”的历史轨迹门槛

### 历史盘口一致性与临场建议字段

| 字段 | 类型 | 示例 | 说明 |
|------|------|------|------|
| `realtime.context_applied.live_outcome_adjustment.historical_market_alignment.applied` | Bool | `true` | 历史盘口一致性是否已参与第 8 步赛果修正 |
| `historical_market_alignment.aligned_match_ids` | List[String] | `["1326947"]` | 命中的历史盘口一致性样本 `match_id` |
| `retrieved_memory.summary.live_market_followup.eligible_match_ids` | List[String] | `["1326947","1302909"]` | 满足 `1X2` 赔率接近且大小球变化接近的历史盘口轨迹样本 |
| `retrieved_memory.summary.live_market_followup.avg_path_score` | Float | `0.72` | 历史盘口轨迹接近度均值 |
| `retrieved_memory.summary.live_market_followup.recommended_action` | String | `follow` / `hedge` / `observe` | 临场操作建议类别 |
| `live_betting_advice` | String | `临场建议: ...` | 面向最终输出的人类可读建议 |

## 可选：球队状态增强（team_context）

预测流程默认会在运行时通过 Sofascore 抓取双方近 N 场的状态信息，并注入到 `analysis_context['team_context']`。如需关闭：`ENABLE_TEAM_CONTEXT=0`。

- 环境变量：
  - `ENABLE_TEAM_CONTEXT=0`（关闭）
  - `TEAM_CONTEXT_LAST_N=5`
- team_id 缓存文件：
  - `europe_leagues/.okooo-scraper/runtime/sofascore_team_ids.json`

### `analysis_context.team_context` 结构

```json
{
  "ok": true,
  "source": "sofascore",
  "league": "premier_league",
  "home": {
    "ok": true,
    "team_id": 42,
    "name": "Manchester United",
    "recent": {"matches": 5, "points": 10, "gf": 8, "ga": 4},
    "avg_possession": 54.2,
    "formations": [{"formation": "4-2-3-1", "count": 3}],
    "last_lineup": {"formation": "4-2-3-1", "starters": [{"name": "Player A", "rating": 7.2}]},
    "key_players": [{"name": "Player A", "avg_rating": 7.25, "matches": 3}]
  },
  "away": {
    "ok": true,
    "team_id": 99,
    "name": "Brentford",
    "recent": {"matches": 5, "points": 7, "gf": 6, "ga": 6},
    "avg_possession": 48.7,
    "formations": [{"formation": "3-5-2", "count": 2}],
    "last_lineup": {"formation": "3-5-2", "starters": [{"name": "Player B", "rating": 7.0}]},
    "key_players": [{"name": "Player B", "avg_rating": 7.10, "matches": 4}]
  }
}
```

### `realtime.context_applied.team_context`（诊断字段）

```json
{
  "attempted": true,
  "ok": true,
  "provider": "sofascore",
  "home_form": 4,
  "away_form": 4
}
```

说明：

- `analysis_context['home_form'] / ['away_form']` 会在未显式提供时，基于 `recent.points / recent.matches` 推导为 1..5
- 该增强为 best-effort；失败时 `ok=false`，并包含 `error`，但不会阻断预测

## 赛果与准确率统计相关格式

### 赛程比分列

- 未赛：`-`
- 已赛：`x-y`

### 备注列可解析字段

常见格式：

- `预测:主胜`
- `比分:1-0/1-1`
- `大小:小3.0(大12.5%/小87.5%)`
- `✅` / `❌`

建议保持可机读前缀：

- `预测:主胜`
- `比分:1-0/1-1`
- `大小:小2.5(0.58)`

### 准确率统计关注项

- 胜平负命中率
- 比分 Top 命中率
- 大小球命中率

### 准确率文件结构

运行时统计文件：

- `europe_leagues/.okooo-scraper/runtime/accuracy_stats.json`

推荐结构：

```json
{
  "overall": {
    "total_predictions": 20,
    "correct_predictions": 12,
    "win_accuracy": 60.0,
    "total_score_predictions": 18,
    "correct_score_predictions": 5,
    "score_accuracy": 27.78,
    "total_ou_predictions": 17,
    "correct_ou_predictions": 10,
    "ou_accuracy": 58.82
  },
  "by_league": {
    "premier_league": {
      "total_predictions": 4,
      "correct_predictions": 3,
      "win_accuracy": 75.0
    }
  },
  "last_updated": "2026-05-04T12:00:00"
}
```

## 文件命名规范

### 联赛主文件

- 格式：`teams_YYYY-YY.md`
- 示例：`teams_2025-26.md`

### 赛程 JSON

- 格式：`YYYY-MM-DD.json`
- 示例：`2026-04-28.json`

### 快照 JSON

- 格式：`<主队>vs<客队>.json`
- 示例：`曼联vs布伦特福德.json`

## 日期和时间格式

### 日期

- 格式：`YYYY-MM-DD`
- 示例：`2026-04-28`

### 时间

- 格式：`HH:MM`
- 示例：`03:00`

### 日期时间组合

- 格式：`YYYY-MM-DD HH:MM`
- 示例：`2026-04-28 03:00`

## 验证检查清单

### 快照验证

- `match_id` 存在
- `欧赔` 存在
- `亚值` 存在
- `大小球.found=true`
- `大小球.final.line` 非空
- `_flow` 已记录

### 预测输出验证

- `final_probabilities` 存在
- `top_scores` 存在
- `over_under.line` 存在，或 `over_under.available=false`
- `over_under.line_source` 存在
- `over_under.market.final` 非空，或明确为 `missing_real_line`
- 欧战/杯赛记录应能看到 canonical `league_code`，必要时包含 `snapshot_dir` / `snapshot_path`

### 主流程写回验证

- 五大联赛正式写回发生在 `teams_2025-26.md`
- 欧战/杯赛正式写回发生在 `MEMORY.md` 与 `.okooo-scraper/runtime/`
- 不把主流程结果写入旧 `predictions/`、`reports/` 目录
- 批量回填与准确率刷新会更新 `teams_2025-26.md`、`MEMORY.md` 与 `.okooo-scraper/runtime/`
