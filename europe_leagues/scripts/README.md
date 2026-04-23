# Scripts Index

这个目录用于集中索引项目中可直接执行的脚本（不移动原文件，避免破坏导入路径）。

## 赛程更新
- 当前赛程/比分的批量更新入口是 `teams_2025-26.md`（赛程表比分列只在“已完赛”写 `x-y`，未完赛统一为 `-`）
- 注意：文档里曾提到的 `update_schedules_from_500.py` 目前仓库内不存在，需补齐对应脚本或改用已存在的更新方式

## 球员数据更新
- `python3 generate_premier_league_players_csv.py`：英超官网名单 → `premier_league_players_2026.csv`
- `python3 fill_premier_league_player_name_cn.py`：英超补常用中文名
- `python3 import_players_from_csv.py --csv premier_league_players_2026.csv --league premier_league`：导入英超 CSV → `players/*.json`
- `python3 enrich_premier_league_players_from_live_stats.py`：英超补 FPL/Understat 统计/热区
- `python3 sync_other_leagues_players_from_understat.py`：西甲/德甲/意甲/法甲用 Understat 刷新 players
- `python3 fill_other_leagues_player_name_cn_from_wikidata.py`：四联赛补 Wikidata 中文名/号码
- `python3 supplement_rosters_and_numbers_from_sofascore.py`：四联赛用 Sofascore 补齐阵容与号码
- `python3 audit_players_completeness.py`：四联赛完整性审计报告
