---
document_title: "数据格式规范"
version: "1.1.0"
last_updated: "2026-04-27"
---

# 数据格式规范

本规范定义当前项目正式使用的数据格式，确保以下几类数据在项目内保持一致：

- 联赛主文件 `teams_2025-26.md`
- 澳客实时快照 JSON
- 预测输出结构
- 赛果回填与准确率统计相关结构

## 当前单一事实来源

正式主流程只认：

- `europe_leagues/<league>/teams_2025-26.md`

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
| 备注 | String | `主胜；比分:1-0/1-1；大小:小3.0(...)` | 预测摘要与赛后标记 |

示例：

```markdown
| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |
|------|------|------|------|------|------|
| 2026-04-28 | 03:00 | 曼联 | - | 布伦特福德 | 主胜；比分:1-0/1-1；大小:小3.0(大12.5%/小87.5%) |
```

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
      "home_team": "曼联",
      "away_team": "布伦特",
      "status": "未开赛"
    }
  ]
}
```

说明：

- `away_team` 可能是简称，如 `布伦特`
- 昵称、简称差异需通过 `europe_leagues/okooo_team_aliases.json` 处理

## 澳客实时快照 JSON 格式

### 文件位置

- `europe_leagues/.okooo-scraper/snapshots/<league>/<主队>vs<客队>.json`

### 当前推荐结构

```json
{
  "match_id": "1296070",
  "league": "premier_league",
  "date": "2026-04-28",
  "time": "03:00",
  "home_team": "曼联",
  "away_team": "布伦特福德",
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
| `default_2.5` | 未抓到真实盘口，回退默认值 |

### 使用规则

- 当 `line_source=snapshot_final` 时，说明真实大小球已成功注入预测
- 当 `market.final` 非空时，最终结论中应同步展示大/小水位
- 不允许把真实 `3.0`、`2.75` 的比赛继续按固定 `2.5` 解释

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

- `主胜`
- `比分:1-0/1-1`
- `大小:小3.0(大12.5%/小87.5%)`
- `✅` / `❌`

### 准确率统计关注项

- 胜平负命中率
- 比分 Top 命中率
- 大小球命中率

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
- `over_under.line` 存在
- `over_under.line_source` 存在
- `over_under.market.final` 非空或明确说明回退原因

### 主流程写回验证

- 正式写回只发生在 `teams_2025-26.md`
- 不把主流程结果写入旧 `predictions/`、`reports/` 目录
