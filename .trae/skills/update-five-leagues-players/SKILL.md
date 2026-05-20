---
name: "update-five-leagues-players"
description: "更新五大联赛 players/*.json（名单→中文名/号码→统计/热区→审计），并与当前 `europe_leagues` 数据层、team_strength 输入和 CLI-first 主链保持一致。当需要批量刷新五大联赛球员数据或赛季更新时调用。"
---

# Update Five Leagues Players

本 Skill 用于把五大联赛球员数据更新变成一套可重复执行的数据资产维护流程。

## 作用边界

它维护的是球员数据输入层，不直接维护：

- `app/cli.py`
- `prediction_system.py`
- `teams_2025-26.md`
- `MEMORY.md`
- `prediction_archive.json`

它的直接目标是为这些模块提供更可靠输入：

- `europe_leagues/domain/team_strength.py`
- `europe_leagues/enhanced_prediction_workflow.py`
- 各联赛目录下的 `players/*.json`

## 适用场景

- 新赛季开始或转会窗后刷新全量球员名单
- 批量补齐中文名、球衣号码、xG/xA、热区等字段
- 避免预测系统因球员数据缺失而退化

## 主要数据源

- 英超：Premier League 官网 / FPL / Understat
- 西甲 / 德甲 / 意甲 / 法甲：Understat + Wikidata + Sofascore

原则：宁可缺少，也不写错。

## 推荐执行顺序

### 1. 英超官方名单 CSV

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 generate_premier_league_players_csv.py
```

### 2. 英超补中文名

```bash
python3 fill_premier_league_player_name_cn.py
```

### 3. 导入英超球员 JSON

```bash
python3 import_players_from_csv.py --csv premier_league_players_2026.csv --league premier_league
```

### 4. 英超补统计 / 伤病 / 热区

```bash
python3 enrich_premier_league_players_from_live_stats.py
```

### 5. 其他四联赛基于 Understat 刷新

```bash
python3 sync_other_leagues_players_from_understat.py
```

### 6. 补中文名 / 号码（Wikidata）

```bash
python3 fill_other_leagues_player_name_cn_from_wikidata.py
```

### 7. 补齐阵容成员与号码（Sofascore）

```bash
python3 supplement_rosters_and_numbers_from_sofascore.py
```

### 8. 再跑一次 Wikidata 补新增球员

```bash
python3 fill_other_leagues_player_name_cn_from_wikidata.py
```

### 9. 完整性审计

```bash
python3 audit_players_completeness.py
```

## 输出文件

- `premier_league/players/*.json`
- `la_liga/players/*.json`
- `bundesliga/players/*.json`
- `serie_a/players/*.json`
- `ligue_1/players/*.json`
- `premier_league_players_2026.csv`
- `.cache_wikidata_player_zh.json`

## 与正式主链的衔接

- 这些文件是预测系统输入，不是正式对外输出
- 正式预测入口仍应走 `prediction_system.py predict-match` / `predict-schedule` / `harness-run`
- 如果球员数据更新后要验证效果，应通过正式 CLI 观察输出变化，而不是直接改报告模板

## 赛季更新注意事项

部分脚本中赛季参数可能写死，新赛季开始时要同步检查：

- `generate_premier_league_players_csv.py`
- `enrich_premier_league_players_from_live_stats.py`
- `sync_other_leagues_players_from_understat.py`

建议先小范围验证，再扩散到全量。

## 最终规则

这个 Skill 的职责是维护五大联赛球员数据资产；如果说明与 `europe_leagues/README.md`、`README_使用指南.md`、`app/cli.py` 的当前实现冲突，以当前代码实现为准。
