---
name: "update-five-leagues-players"
description: "更新五大联赛 players/*.json（名单→中文名/号码→统计/热区→审计）。当需要批量刷新五大联赛球员数据或赛季更新时调用。"
---

# Update Five Leagues Players

本 Skill 用于把“五大联赛球员数据更新”变成可重复执行的一套流程，包含：

- 英超：官网 squad list 名单 → 中文名（常用译名）→ FPL 出场/伤停/基础技术统计 → Understat xG/xA/射门/热区
- 西甲/德甲/意甲/法甲：Understat 名单+高阶统计 → Wikidata 中文名/号码 → Sofascore 补齐阵容与号码 → 再跑 Wikidata 补中文名
- 最后输出完整性审计报告，定位仍缺失的数据

## 适用场景

- 新赛季开始/转会窗后想刷新全量球员名单
- 想批量补齐中文名、球衣号码、xG/xA、射门热区等字段
- 需要统一更新 `players/*.json` 以避免预测系统“球队数据退化/所有球队一样”

## 重要说明（数据来源与准确性）

- 英超伤病/可出战概率等来源于 `fantasy.premierleague.com`（可直连、字段稳定）。
- Understat 提供 `xG/xA/射门/关键传球/射门坐标`，可生成热区网格，但不提供球衣号码/中文名。
- Wikidata 用于“官方中文名”和（部分球员的）球衣号码字段 `P1618`：它可能给出多个号码（跨俱乐部/跨时期），因此脚本会写入 `shirt_numbers`，只有唯一时才写 `shirt_number`。
- Sofascore 用于补齐“阵容成员 + jerseyNumber”，并标注 `shirt_number_source=sofascore`；它是公开数据源但非联赛官方。
- 若遇到站点反爬/接口变化：以“宁可缺少也不写错”为原则，脚本会跳过低置信匹配。

## 前置条件

- 在项目根目录执行：`/Users/bytedance/trae_projects/europe_leagues`
- Python 3 可用（脚本依赖 `requests`，项目里已在使用）
- 网络可访问：`premierleague.com`、`fantasy.premierleague.com`、`understat.com`、`wikidata.org`、`api.sofascore.com`

## 一键执行（推荐顺序）

### 0)（可选）更新赛程（不影响 players，但建议同批更新）

```bash
python3 update_schedules_from_500.py --league all
```

### 1) 英超：生成官方名单 CSV

从英超官网官方文章生成 `premier_league_players_2026.csv`：

```bash
python3 generate_premier_league_players_csv.py
```

### 2) 英超：补常用中文名（高置信）

```bash
python3 fill_premier_league_player_name_cn.py
```

### 3) 英超：导入到 `premier_league/players/*.json`

```bash
python3 import_players_from_csv.py --csv premier_league_players_2026.csv --league premier_league
```

### 4) 英超：补统计/伤病/热区

```bash
python3 enrich_premier_league_players_from_live_stats.py
```

### 5) 西甲/德甲/意甲/法甲：生成/刷新 players（Understat）

```bash
python3 sync_other_leagues_players_from_understat.py
```

### 6) 西甲/德甲/意甲/法甲：补中文名/号码（Wikidata）

```bash
python3 fill_other_leagues_player_name_cn_from_wikidata.py
```

### 7) 西甲/德甲/意甲/法甲：补齐阵容成员与号码（Sofascore）

```bash
python3 supplement_rosters_and_numbers_from_sofascore.py
```

### 8) 再跑一次 Wikidata（给新增球员补中文名/号码）

```bash
python3 fill_other_leagues_player_name_cn_from_wikidata.py
```

### 9) 完整性审计

```bash
python3 audit_players_completeness.py
```

## 输出文件（你会看到什么变化）

- 英超 CSV：`premier_league_players_2026.csv`
- 英超球员：`premier_league/players/*.json`
- 其他四联赛球员：`la_liga/players/*.json`、`bundesliga/players/*.json`、`serie_a/players/*.json`、`ligue_1/players/*.json`
- Wikidata 缓存：`.cache_wikidata_player_zh.json`

## 字段约定（落盘结构）

- 中文名：`name`（中文）、`english_name`（英文）、`wikidata_id`、`name_cn_source`
- 号码：`shirt_number`（单值）、`shirt_numbers`（多值备选）、`shirt_number_source`
- 统计：
  - 英超：`stats` + `technical_stats` + `rating_metrics` + `heatmap`
  - 其他四联赛：`stats` + `shooting_stats` + `passing_stats` + `advanced_stats` + `heatmap`

## 赛季更新注意事项

脚本内部分“赛季参数”是写死的（例如 Understat 当前用的是 `.../2025`），新赛季开始时需要同步改：

- `generate_premier_league_players_csv.py`（官网文章 URL 可能变化）
- `enrich_premier_league_players_from_live_stats.py`（Understat EPL 年份）
- `sync_other_leagues_players_from_understat.py`（Understat 四联赛年份）

建议做法：先跑一遍脚本确认输出合理，再扩散到全量；必要时在脚本顶部加 `--season` 参数做成可配置。

