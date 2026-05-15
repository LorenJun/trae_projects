# Scripts Index

这个目录用于集中索引项目中可直接执行的脚本（不移动原文件，避免破坏导入路径）。

## 赛程更新
- 当前正式赛程/比分更新以各联赛 `teams_2025-26.md` 为 SoT（比分列只在“已完赛”写 `x-y`，未完赛统一为 `-`）
- 单场预测后的正式写回入口是 `python3 prediction_system.py predict-match ...` / `predict-schedule ...`
- 赛后回填入口是 `python3 prediction_system.py save-result ...`、`auto-sync-results ...`、`result-sync-daemon ...`，批量脚本可用仓库根目录 `bulk_fetch_and_update.py`
- 不再引用不存在的 `update_schedules_from_500.py`

## 球员数据更新
- `python3 generate_premier_league_players_csv.py`：英超官网名单 → `premier_league_players_2026.csv`
- `python3 fill_premier_league_player_name_cn.py`：英超补常用中文名
- `python3 import_players_from_csv.py --csv premier_league_players_2026.csv --league premier_league`：导入英超 CSV → `players/*.json`
- `python3 enrich_premier_league_players_from_live_stats.py`：英超补 FPL/Understat 统计/热区
- `python3 sync_other_leagues_players_from_understat.py`：西甲/德甲/意甲/法甲用 Understat 刷新 players
- `python3 fill_other_leagues_player_name_cn_from_wikidata.py`：四联赛补 Wikidata 中文名/号码
- `python3 supplement_rosters_and_numbers_from_sofascore.py`：四联赛用 Sofascore 补齐阵容与号码
- `python3 audit_players_completeness.py`：四联赛完整性审计报告

## 记忆维护
- `python3 scripts/sync_memory_rag_explanations.py`：把 `prediction_archive.json` 中已有的 `RAG` 记忆解释回填到 `MEMORY.md` 对应预测记录
