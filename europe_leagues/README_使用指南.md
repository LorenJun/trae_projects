# 足球预测系统使用指南

本指南面向当前 `europe_leagues/` 正式应用目录，默认以 CLI-first 方式执行。

## 先记住两条原则

- `prediction_system.py` 是兼容 / 发现入口
- `app/cli.py` 是真实命令实现与 JSON 输出入口

因此，日常执行、自动化接入和文档口径都应优先围绕正式 CLI，而不是默认直接 import 底层 Python 类。

## 当前标准工作流

1. 用 `collect-data` 获取赛程、`match_id` 与上下文
2. 用 `predict-match` / `predict-schedule` 执行预测
3. 检查真实盘口与 RAG 相关输出
4. 根据比赛类型写入 SoT 或 runtime-only 归档
5. 赛后使用 `save-result` / `auto-sync-results` / `result-sync-daemon` 回填
6. 需要批次复盘时使用 `sync-pending-results-review`
7. 需要显式重建时再执行 `accuracy --refresh`

## 比赛类型与写回边界

### league-backed / SoT-backed

以下 competition 以 SoT markdown 为主：

- `premier_league`
- `la_liga`
- `serie_a`
- `bundesliga`
- `ligue_1`
- `world_cup`

写回位置：

- 五大联赛：`<league>/teams_2025-26.md`
- 世界杯：`world_cup/teams_2026.md`

### runtime-only

以下 competition 以运行时归档与滚动记忆为主：

- `europa_league`
- `champions_league`
- `conference_league`
- 其他杯赛 / 欧战扩展比赛

写回位置：

- 项目根 `MEMORY.md`
- `.okooo-scraper/runtime/*.json`

## 推荐命令

### 1. 环境检查

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py setup-openclaw --json
python3 prediction_system.py health-check --json
python3 prediction_system.py list-leagues --json
```

### 2. 采集赛程与 MatchID

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league premier_league --date 2026-05-11 --json
```

### 3. 单场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match --league premier_league --home-team 曼联 --away-team 切尔西 --date 2026-05-11 --json
```

### 4. 批量预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-schedule --league premier_league --date 2026-05-11 --days 1 --json
```

如只想查看批量结果而不触发写回副作用：

```bash
python3 prediction_system.py predict-schedule --league premier_league --date 2026-05-11 --days 1 --no-write --json
```

### 5. Harness 审计链路

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py harness-list --json
python3 prediction_system.py harness-run --pipeline match_prediction --league premier_league --home-team 曼联 --away-team 切尔西 --date 2026-05-11 --json
```

### 6. 结果同步

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py pending-results --days-back 30 --json
python3 prediction_system.py auto-sync-results --json
python3 prediction_system.py save-result --match-id premier_league_20260511_曼联_切尔西 --home-score 2 --away-score 1 --json
python3 prediction_system.py result-sync-daemon --json
```

### 7. 复盘与重建

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py sync-pending-results-review --days-back 30 --limit 20 --review-sample-limit 8 --json
python3 prediction_system.py accuracy --refresh --json
python3 prediction_system.py build-season-master-review --season 2025-26 --recent-days 7 --days-back 30 --limit 50 --rag-limit 300 --json
```

### 8. RAG 与仓库维护

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py rag-rebuild --json
python3 prediction_system.py rag-diagnose --json
python3 prediction_system.py sync-memory-rag --json
python3 prediction_system.py purge-nonreal-data --json
python3 prediction_system.py refresh-repo-docs --json
```

## 预测输出重点检查项

执行预测后，优先检查这些字段：

- `final_probabilities`
- `top_scores`
- `over_under.line`
- `over_under.line_source`
- `over_under.market.final`
- `realtime.okooo`
- `retrieved_memory_explanation`
- `realtime.context_applied.live_outcome_adjustment.historical_market_alignment`
- `retrieved_memory.summary.live_market_followup`
- `live_betting_advice`
- `runtime_profile`

## 澳客访问与快照口径

当前正式链路关于 `m.okooo.com` 的访问口径已经固定为：

- 默认 `okooo-driver`：`local-chrome`
- 默认请求特征：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 默认 no-cache 头与 cache-bust 参数
- 公共设备池：`okooo_mobile_access.py` 统一维护，当前为 `100` 组随机 `iPhone Safari` profile

如果本机默认浏览器或裸 `curl` 访问 `odds.php` 返回 `403/405`，不代表正式链不可用；优先确认是否绕过了公共访问策略。

## 大小球当前规则

真实大小球盘口优先来自澳客 `handicap.php` 页面内的 `大小球` tab。

正常情况下：

- 抓到真实盘口时，`line_source` 应接近 `snapshot_final` 或等价真实来源
- 若未抓到真实盘口，应明确落为 `missing_real_line` 或等价缺失状态
- 不应把 `default_2.5` 当成正式结论输出给用户

## 欧赔当前规则

当前欧赔链路已升级为“多公司共识优先”：

- 优先解析多家公司的欧赔明细并生成 `multi_company_consensus`
- `99家平均` 只作为 fallback，不再是默认优先结果
- 预测输出里应优先检查：
  - `market_snapshot.欧赔.company_mode`
  - `market_snapshot.欧赔.companies`
  - `market_snapshot.欧赔.initial`
  - `market_snapshot.欧赔.final`

已验证样例 `la_liga / 埃尔切 vs 赫塔费 / MatchID=1302914` 可稳定拿到：

- 欧赔多公司共识
- 亚值真实盘口
- 大小球真实盘口与水位
- 凯利初赔 / 即赔

## 持久化与赛果闭环

预测 side effects 由 `domain/persistence.py` 统一编排，通常会联动：

- SoT 写回或 runtime-only 归档
- `MEMORY.md` 更新
- prediction archive 更新
- result sync registry 登记
- RAG 样本 / 索引同步

赛果同步由以下正式入口负责：

- `save-result`
- `auto-sync-results`
- `result-sync-daemon`
- `sync-pending-results-review`

结果命中后，`runtime/result_sync.py` 与 `result_manager.py` 会继续推动：

- SoT 比分与备注更新
- `MEMORY.md` 状态迁移
- `prediction_archive.json` 实际赛果字段补齐
- 准确率刷新
- RAG / 记忆样本 / review-learning 相关衍生更新

## 什么时候需要底层 Python 调试

默认不要直接从文档主路径走 `EnhancedPredictor()` 或 `UpsetAnalyzer()`。

只有在下列场景，才建议临时走底层 Python 调试：

- CLI 输出与内部结构不一致，需要检查原始对象
- 某个领域服务行为异常，需要单点验证
- 要调试尚未暴露成 CLI 参数的内部能力

即便如此，也应把 CLI 结果作为正式行为标准，把底层 import 视为开发排查手段，而不是默认工作流。

## 常见误区

### 1. 把 `prediction_system.py` 当成主逻辑

错误。它只是兼容入口；真实实现仍在 `app/cli.py`。

### 2. 把 `accuracy --refresh` 当成唯一日常结果闭环

错误。正常赛果命中后，系统会自动同步多种衍生产物；`accuracy --refresh` 更像显式重建入口。

### 3. 把欧战当成五大联赛 SoT 写回

错误。欧战 / 杯赛默认是 runtime-only 路径，不直接写五大联赛 `teams_2025-26.md`。

### 4. 直接改旧模板目录当正式输出

错误。`analysis/predictions/`、`analysis/results/` 等历史目录不再是正式主流程输出。

## 相关文件

优先参考：

- `README.md`
- `ODDS_FETCH_GUIDE.md`
- `docs/INDEX.md`
- `docs/PRD_足球预测系统_2026.md`
- `docs/upset_warning_guide.md`

如文档与代码冲突，以这些实现为准：

- `app/cli.py`
- `domain/persistence.py`
- `runtime/result_sync.py`
- `result_manager.py`
