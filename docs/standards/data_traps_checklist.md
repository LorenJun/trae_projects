---
document_title: "竞彩数据陷阱检查清单"
version: "1.1.0"
last_updated: "2026-05-08"
---

# 竞彩数据陷阱检查清单

> 当前正式流程  
> 1. `prediction_system.py collect-data` 或赛程抓取定位 `match_id`  
> 2. `prediction_system.py predict-match / predict-schedule` 执行增强预测，并自动接入 RAG 记忆层  
> 3. 五大联赛 SoT 写回 `europe_leagues/<league>/teams_2025-26.md`；欧战/杯赛写入 `MEMORY.md` 与 runtime-only 归档  
> 4. 赛后用 `prediction_system.py save-result`、`auto-sync-results`、`result-sync-daemon` 或 `bulk_fetch_and_update.py` 回填  
> 5. 最后用 `prediction_system.py accuracy --refresh --json` 刷新胜负 / 比分 / 大小球统计  
> 可审计编排入口：`prediction_system.py harness-run --pipeline ... --json`  
> 关键检查项：`over_under.line`、`line_source`、`over_under.market.final`、`retrieved_memory_explanation`
> 欧战正式 competition config：`europa_league`、`champions_league`、`conference_league` 已进入主链，但写回仍保持 `runtime_only`

本文列出当前 `europe_leagues` 项目里最容易踩的 `10` 条竞彩数据陷阱，并按严重程度排序。每条都包含：

- 典型症状
- 当前链路中的高风险位置
- 检查动作

## 1. 真实盘口线缺失被 fallback 掩盖

- 严重程度：`极高`
- 典型症状：大小球预测里出现 `default_2.5`、`unknown`，但分析时仍被当成真实盘口使用
- 高风险位置：`domain/inference.py` 的 `resolve_over_under_line(...)` 后续链路、`prediction_archive.json` 的 `over_under.line_source`
- 风险说明：旧链路里抓不到真实大小球盘口时，模型仍会继续输出结论；如果统计时不单独分层，会直接污染大小球命中率
- 检查动作：
  - 检查 `over_under.line_source`
  - 强制分层 `snapshot_final / snapshot_initial / missing_real_line / unknown`
  - 当前正式链路已经禁止输出 `default_2.5` 正式预测；若仍出现，视为历史脏数据或回归
  - 在复盘和统计里禁止把历史 `default_2.5` 与真实盘口样本混算
  - 对缺盘口样本，检查是否明确落为 `over_under.available=false` 且 `reason=missing_real_line`

## 2. 双路径写回导致分母错觉

- 严重程度：`极高`
- 典型症状：`teams_2025-26.md` 里的准确率和 `MEMORY.md` 顶部统计、`accuracy_stats.json` 看起来不一致
- 高风险位置：`result_manager.py`、`domain/persistence.py`、`accuracy_stats.json`
- 风险说明：五大联赛写 `teams_2025-26.md`，欧战/杯赛写 `MEMORY.md + prediction_archive.json`；如果统计只扫一个出口，就会误判模型好坏
- 检查动作：
  - 统一使用 `accuracy --refresh --json`
  - 检查 `over_under_report.scope = unified_prediction_sources`
  - 确认 `source_presence` 已覆盖 `teams_sot / memory / archive`

## 3. line_source 历史缺字段

- 严重程度：`极高`
- 典型症状：已完赛样本存在，但 `line_source = unknown`
- 高风险位置：`prediction_archive.json` 的历史 runtime-only 条目
- 风险说明：这类样本已经进入命中率分母，但无法判断它到底属于真实盘口还是 fallback，导致统计解释力下降
- 检查动作：
  - 统计 `by_line_source.unknown`
  - 定位历史条目是否缺 `full_prediction.over_under.line_source`
  - 必要时补历史归档字段，避免长期脏数据堆积
  - 欧战历史记录优先检查 `league_code`、`league_name`、`snapshot_dir`、`snapshot_dir_aliases`、`snapshot_path`、`line_source` 是否已迁移到 canonical 口径

## 4. MatchID 与内部 match_id 混用

- 严重程度：`极高`
- 典型症状：赛果抓到了，但没有更新到正确预测；或同一场比赛出现重复记录
- 高风险位置：`runtime/result_sync.py`、`result_manager.py`、`prediction_archive.json`、`result_sync_registry.json`
- 风险说明：联赛 SoT 常用 `league_date_home_away`，runtime-only 更依赖真实 `external_match_id`；一旦透传不完整，就会导致写回错位或重复建档
- 检查动作：
  - 检查 `match_id / external_match_id / internal_match_id / teams_match_id`
  - 赛后确认 registry 和 archive 是否指向同一场比赛
  - 对非 SoT 比赛优先用真实 `match_id` 去重

## 5. 欧战/杯赛误挂联赛 SoT

- 严重程度：`极高`
- 典型症状：欧联、欧冠、杯赛比赛被错误写进英超、西甲等 `teams_2025-26.md`
- 高风险位置：`domain/persistence.py` 的 canonical identity、`result_manager.py` 的写回路径
- 风险说明：一旦赛事归属错误，不只写回会失败，准确率统计、RAG 召回和历史学习也都会串味
- 检查动作：
  - 检查 `storage_mode`
  - 检查 `competition_stage_name`、`league_name`
  - 检查 `league_code` 是否收口到 `europa_league / champions_league / conference_league`
  - 确认欧战/杯赛是否只写入 `MEMORY.md` 与 runtime archive
  - 确认欧战中文快照目录是否通过 `snapshot_dir` / `snapshot_dir_aliases` 映射回 canonical `league_code`

## 6. RAG 盘口样本不完整

- 严重程度：`高`
- 典型症状：`market_cases` 有召回，但很多样本缺少完整盘口、水位或凯利
- 高风险位置：`runtime/rag_store.py`、`prediction_memory_odds_samples.json`、`analysis/odds/*_odds.json`
- 风险说明：RAG 召回看起来有历史案例，但如果盘口字段缺失，实际对大小球和盘口解释帮助有限
- 检查动作：
  - 检查 `market_cases` 是否带完整盘口字段
  - 检查 `historical_odds_reference` 与 `market_snapshot`
  - 区分 `prediction_case` 与 `market_case`，不要混用

## 7. 快照抓到了，但关键字段没进入最终预测

- 严重程度：`高`
- 典型症状：抓取日志显示成功，但最终结果里缺 `line_source`、`over_under.market.final`、`match_id`
- 高风险位置：`domain/postprocess.py`、`enhanced_prediction_workflow.py`
- 风险说明：采集成功不等于模型和统计都消费成功；字段在中间链路丢失，会让分析层和统计层各看各的
- 检查动作：
  - 检查最终输出 `over_under.line`、`line_source`、`over_under.market.final`
  - 检查 `realtime.okooo.match_id`
  - 检查归档中的 `full_prediction` 是否保留这些字段
  - 对欧战比赛额外检查 `realtime.okooo.snapshot_path`、`snapshot_dir` 是否命中 `欧联 / 欧罗巴 / 欧冠 / 欧协联` 别名目录

## 8. 队名别名与简称归一化不稳

- 严重程度：`高`
- 典型症状：同一支球队在赛程、快照、SoT、记忆里名字不一致，导致匹配失败或重复记录
- 高风险位置：`collectors/aliasing.py`、`result_manager.py` 中的队名归一化
- 风险说明：竞彩数据里简称、中文别名、转写差异很多，不统一会直接影响抓取匹配、写回和赛果同步
- 检查动作：
  - 检查联赛 alias map 是否覆盖当前对阵
  - 检查 `collect-data` 与 SoT 中主客队名称是否一致
  - 对异常简称及时补 alias

## 9. 完赛后赛果回填未闭环

- 严重程度：`高`
- 典型症状：比赛已经结束，但 `actual_score` 仍为空；统计里长期没有新增分母
- 高风险位置：`runtime/result_sync.py`、`result_sync_registry.json`、`result_sync_daemon.log`
- 风险说明：如果回填链路没有闭环，最终看到的是“预测样本越来越多，但已完赛评估越来越空”
- 检查动作：
  - 检查 registry 条目是否 `pending / completed`
  - 检查 `last_error`
  - 检查 daemon 是否加载了最新代码并实际执行

## 10. 小样本波动被当成长期结论

- 严重程度：`中高`
- 典型症状：因为一场 `4-0` 或 `3-1`，就直接得出“大小球模型完全失效”的结论
- 高风险位置：`MEMORY.md` 顶部滚动统计、近期 `accuracy_stats.json`
- 风险说明：当前项目最近已完赛样本仍然偏少，短期表现只能看方向，不能替代长期评估
- 检查动作：
  - 同时查看 `sample_count`
  - 按 `line_source`、`line_bucket`、`league` 分层
  - 不要只看总体百分比，必须同时看分母

## 每次排查的最小检查顺序

```text
1. 先看 match_id / external_match_id / teams_match_id 是否一致
2. 再看 over_under.line / line_source / over_under.market.final 是否完整
3. 再看 league_code / snapshot_dir / snapshot_path 是否已收口到 canonical 口径
4. 再看 storage_mode 是否正确
5. 再看 actual_score 是否已回填
6. 最后看 accuracy --refresh --json 的 over_under_report
```

## 当前最推荐的核对命令

```bash
cd /Users/bytedance/trae_projects/europe_leagues

python3 prediction_system.py accuracy --refresh --json
python3 prediction_system.py migrate-archive --json
python3 prediction_system.py list-leagues --json
python3 prediction_system.py pending-results --days-back 14 --json
python3 prediction_system.py rag-diagnose --json
python3 prediction_system.py sync-memory-rag --dry-run --json
```

## 使用原则

- 先确认数据是不是“真的存在”，再讨论模型好坏
- 先确认口径是否统一，再比较命中率
- 先确认是采集问题、写回问题还是统计问题，再调整模型参数
- 对大小球问题，必须把历史 `default_2.5`、真实盘口、`missing_real_line` 和 `unknown` 分开看
- 对欧战问题，必须先确认它属于正式 `competition config`，再确认是否保持了 `runtime_only + canonical league_code + snapshot_dir_aliases` 口径
