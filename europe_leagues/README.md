# 足球预测系统（Europe Leagues）

> ## 速读摘要（TL;DR）
> 即使读不全本文件，看完这 30 行也能拿到 80% 信息。
>
> - **这是什么**：仓库里真正运行的足球预测应用，目录 `europe_leagues/`。
> - **入口真相源**：`prediction_system.py` 只是兼容/发现入口；真正的命令面、参数、JSON 输出 **以 `app/cli.py` 为准**。文档与代码冲突时信代码。
> - **最常用命令**：
>   - 正式预测（唯一预测入口，可注入战术/首发 context）：`predict-match`
>   - 抓盘口快照：`okooo_save_snapshot.py`（可加 `--odds-only` 单独补抓欧赔）
> - **盘口四盘**：欧赔 / 凯利 / 亚盘 / 大小球，走单会话 hub 真实导航一次拿回；解析规则与排障见 [`ODDS_FETCH_GUIDE.md`](ODDS_FETCH_GUIDE.md)。
> - **固定 IP 抗封（单机无代理，默认全开）**：三道防线 = 进程级全局频控闸（`_global_pace_gate`，默认 2.5s，可调 `--min-request-interval`）+ 节奏随机抖动（`_jittered` ±35%）+ stealth 指纹屏蔽（`_install_stealth_script` 抹掉 `navigator.webdriver` 等自动化特征）。无代理时把请求节奏放慢拉抖是降低撞验证墙的根因手段，换模型/换 UA 不是。
> - **持久化边界（已收敛为二元）**：只有 SoT-backed 正式联赛（五大联赛 + 世界杯）走完整写回（teams md / `MEMORY.md` 滚动记忆 / RAG / 归档 / 赛果同步）；其余一切赛事（杯赛、欧战、友谊赛等）走 `archive_only`——**仅归档预测 + 登记赛果同步 + 刷新准确率，不写 MEMORY/RAG/teams md**（`persisted.archive_only=True`、`memory_updated=False`）。详见 [`docs/PRD_足球预测系统_2026.md`](docs/PRD_足球预测系统_2026.md) 第 5 节。
> - **世界杯淘汰赛上下文**：`world_cup` 预测会在核心推理前自动注入 `analysis_context.world_cup_reference`（90 分钟/加时/点球规则、小组赛状态、首发/常规阵容、战术节奏与单场淘汰战意），并写入 `realtime.context_applied.world_cup_reference` 供审计。
> - **找文档**：先看导航路由页 [`docs/INDEX.md`](docs/INDEX.md)，按场景跳转。
> - **凯利解析关键不变量**：主/平/客三路应彼此不同；若三路坍缩成同一个返还率即为解析 bug。
>
> 详细内容见下文分节。

本目录是当前仓库里真正运行的足球预测应用。

## 入口与权威链

当前正式入口分两层：

- `prediction_system.py`：兼容 / 发现入口
- `app/cli.py`：真实 CLI 路由、JSON 输出与命令实现

不要把 `prediction_system.py` 误当成业务主逻辑；真正的命令面、参数和输出约定以 `app/cli.py` 为准。

## 当前正式能力

当前正式 CLI 子命令包括：

- 基础：`list-leagues`、`health-check`、`setup-openclaw`
- 采集与预测：`collect-data`、`predict-match`、`predict-fourteen-issue`
- 结果同步：`pending-results`、`save-result`、`auto-sync-results`、`result-sync-daemon`
- 复盘与治理：`accuracy`、`apply-reanalysis`、`sync-pending-results-review`、`build-season-master-review`
- 文档与清理：`refresh-repo-docs`、`purge-nonreal-data`
- RAG：`rag-rebuild`、`rag-diagnose`、`sync-memory-rag`
- 归档与运行：`migrate-archive`、`harness-list`、`harness-run`

## 当前架构

核心运行链路：

1. `prediction_system.py` 接收命令并转发到 `app/cli.py`
2. `domain/predictor.py` 暴露稳定预测外壳
3. `enhanced_prediction_workflow.py` 负责主编排
4. `domain/*` 提供 inference / postprocess / live / rag / persistence 等领域服务
5. `domain/persistence.py` 负责预测落盘 side effects
6. `runtime/result_sync.py` 与 `result_manager.py` 负责赛果同步、结果闭环与衍生数据更新

> **三轴影子诊断层**：`domain/inference.py` 在主预测之外产出 `tri_axis_consistency`（方向轴/大小球轴/操盘轴 + 临场资金轴）。操盘轴含「让球方向手法矩阵」（亚盘升降盘 × 水位档位 × 欧赔方向 → 阻上/诱上/防客/诱客/阻下，以「让球口诀」并入研判文本）。临场资金轴 = 封盘热门赔率漂移，带置信度质检层（欧赔走冷用亚值/凯利同向共振印证，`drift_confidence` high/low/n/a，过滤单家抓取噪声）。三轴层本身**纯标记、不改概率**，随归档持久化供回测。技术口径见 [`docs/architecture/europe_leagues_architecture.md`](../docs/architecture/europe_leagues_architecture.md) 第 3.7 节。
>
> **2026-06-18 起让球 6 口诀已进入打分链路**（不再只是标签）：① 经 `apply_market_operation_adjustment` 的常开矩阵偏置驱动**胜平负概率**（世界杯样本不足、历史学习权重失效时仍生效）；② 经 `domain/score_projection.py` 驱动**比分方向**（印证类→强侧获胜、诱导类→平局+弱侧），大小球侧由 OU 操盘 6 规则决定，详见架构文档第 3.8 节。比分（`top_scores`）已统一由 `score_projection` 单一数据源产出，网页 / `MEMORY.md` / `teams_2026.md` 三处一致。
>
> **2026-06-29 起世界杯淘汰赛参考信息进入正式预测链**：`EnhancedPredictor._inject_world_cup_reference_context` 会在 `InferencePipelineService.run(...)` 前读取 `world_cup/teams_2026.md` 小组赛表现与实时快照 `阵容` 块，生成 `analysis_context['world_cup_reference']`，并把 `home_form` / `away_form`、淘汰赛 `home_motivation=90` / `away_motivation=90`、`single_elimination=True` 送入模型。网页「参考预测分析」只消费正式结果，不再作为独立展示层口径。

### 共享基础模块（单一事实源 / 无状态工具）

为避免魔法数字与纯逻辑散落在巨石文件里，下列模块集中收口可复用基础能力：

- `domain/constants.py`：算法参数的单一事实源（SoT），收口 Dixon-Coles `RHO_MAP` / `resolve_rho`、主场系数 `HOME_ADVANTAGE_MAP` / `resolve_home_advantage`、strength→ELO 播种公式 `strength_to_seed_rating`。`inference.py` / `result_manager.py` / `ml_prediction_models.py` 统一从这里取值，避免各处复制常量产生漂移。
- `domain/note_parsing.py`：teams md 备注列与比分文本的无状态解析函数（胜负 / 信心 / 比分 / 大小球）。`ResultManager` 的同名 `_parse_*` 方法委托到此处，便于复用与单测。
- `storage/_jsonio.py`：JSON 落盘的原子写（`atomic_write_json`）与带损坏告警的安全读取（`safe_read_json`）。评分（`storage/ratings.py`）、准确率（`storage/accuracy.py`）、归档（`storage/archive.py`）统一走原子写，避免半写文件损坏数据。

## 当前实时盘口回流链

当前正式预测链已经把“赛程抓取、MatchID 定位、快照落盘、预测前注水、缺失盘口补抓”串成闭环：

1. `collect-data` 可优先读取澳客赛程与已存在快照
2. `okooo_fetch_daily_schedule.py` 可自动翻月到目标年月，并按日期分组抽取整天赛程
3. `okooo_save_snapshot.py` 可按 `日期 + 主客队 + 时间` 精确锁定比赛行并抓取 `欧赔 / 亚值 / 大小球 / 凯利`
4. `domain/live.py` 与 `domain/odds.py` 会在预测前注入快照，必要时补抓真实盘口线
5. 预测输出中的 `over_under.line_source=snapshot_final` 代表真实盘口已成功接入正式链
6. `predict-match` 的文本输出在「主胜/平局/客胜」之后会罗列 `比分参考 (Top N)`（带各比分置信度，数据来自结果里的 `top_scores`）
7. 世界杯淘汰赛会额外检查 `world_cup_reference_context` / `analysis_context.world_cup_reference`：小组赛状态、阵容身价/伤停、常规阵容、90 分钟/加时/点球规则必须先注入预测，再用于网页参考分析

当前身份字段约定也已收敛：

- `external_match_id` 只表示澳客真实比赛 ID，必须是纯数字
- `internal_match_id` 表示项目内部比赛键，可为 `league_YYYYMMDD_主队_客队`
- `teams_match_id` 表示 SoT 行身份，通常与 canonical 内部比赛键一致
- 访问澳客赔率页、历史页、快照页时，只允许使用纯数字 `external_match_id`
- 盘口抓取统一走单会话 hub 真实导航：暖首页 → 落地 hub 页 `history.php` → 点 `亚指`/`欧指` 整页跳转 → 页内点 `大小球`/`凯利` tab，一次会话拿回四盘；已移除所有深链回退路径
- 当前正式移动端页面形态：
  - hub 入口（盘口导航起点）：`https://m.okooo.com/match/history.php?MatchID=<external_match_id>`
  - 欧赔 / 凯利落地页（由 hub 点 `欧指` 跳转到达）：`https://m.okooo.com/match/odds.php?MatchID=<external_match_id>`
  - 亚值 / 大小球落地页（由 hub 点 `亚指` 跳转到达，大小球为页内 tab）：`https://m.okooo.com/match/handicap.php?MatchID=<external_match_id>`

当前链路已显式防御：

- 错误月份导致的日期定位失败
- 同日多场比赛误点
- 错误 `match_id` 导致的串场快照
- `match_id` 命中但主客队/日期不一致的旧文件复用
- 图形验证页/滑块验证页误判为正常页面
- 命中验证页后在同一路径上持续重试打转

关键文件：

- `app/cli.py`
- `prediction_system.py`
- `domain/predictor.py`
- `enhanced_prediction_workflow.py`
- `domain/persistence.py`
- `runtime/result_sync.py`
- `result_manager.py`

## SoT 写回边界（二元）

当前持久化边界已收敛为二元：**只有 SoT-backed 正式联赛走完整写回（teams md + MEMORY + RAG + 归档 + 赛果同步）；其余赛事走 `archive_only`（仅归档 + 赛果同步 + 准确率刷新，不写 MEMORY/RAG/teams md）。**

### 1. SoT-backed competitions（唯一允许写回）

以下且仅以下比赛类型允许写回 teams md / `MEMORY.md` 滚动记忆 / RAG / 赛果同步登记：

- `premier_league`
- `la_liga`
- `serie_a`
- `bundesliga`
- `ligue_1`
- `world_cup`

对应主事实源：

- 五大联赛：`<league>/teams_2025-26.md`
- 世界杯：`world_cup/teams_2026.md`

判定逻辑见 `app/cli.py` 的 `SOT_BACKED_LEAGUE_CODES` 与 `is_sot_backed_league()`。

### 2. 其余一切赛事（仅归档同步，不写 MEMORY/RAG/teams md）

杯赛、欧战（`europa_league` / `champions_league` / `conference_league`）、友谊赛（`friendly`）以及任何非上述六个联赛的 competition：`predict-match` 走 `archive_only` 路径（`persist=True, archive_only=True`），**只归档预测 + 登记赛果同步 + 刷新准确率/仪表盘，不写 teams md、不写 `MEMORY.md`、不进 RAG**。

- 结果标 `persisted.archive_only = True`、`persisted.memory_updated = False`、`persisted.archived = True`
- 对应实现：`domain/persistence.py` 的 `persist_archive_only_prediction()`
- 直接跑 `okooo_save_snapshot.py` 快照脚本本就不写 `MEMORY.md`

> 注意：**世界杯属于 SoT-backed 正式联赛**，`predict-match` 会写回 [`world_cup/teams_2026.md`](world_cup/teams_2026.md) + 滚动记忆 + 赛果同步登记。

### 1.1 世界杯淘汰赛预测上下文

世界杯 `world_cup` 在淘汰赛阶段采用 90 分钟常规时间预测口径：常规时间胜者晋级；常规时间打平进入 30 分钟加时；加时仍平进入点球；无客场进球规则。正式预测链会把该规则与两队小组赛常规阵容/首发大名单、小组赛近期表现、阵容实力差距、伤停、战术节奏和赛事战意一起注入 `analysis_context['world_cup_reference']`，并同步暴露到 `result['world_cup_reference_context']`。

该信息会影响：

- `home_form` / `away_form`：由小组赛积分与表现派生，进入模型输入；
- `home_motivation` / `away_motivation`：淘汰赛默认提升到 `90.0`，反映单场出局压力；
- `single_elimination` / `draw_after_90_goes_extra_time`：供推理、RAG 和网页审计识别 90 分钟平局进入加时/点球窗口；
- 网页 `参考预测分析`：展示人员配置、小组赛状态、战术倾向、常规时间比分预测、爆冷/对冲比分及理由。

## 预测与结果闭环

预测 side effects 由 `domain/persistence.py` 统一编排，通常会联动：

- SoT 完整写回，或非 SoT 的 `archive_only` 归档
- `MEMORY.md` 滚动记忆更新
- prediction archive 更新
- RAG 样本 / 索引同步
- result sync registry 登记

赛后结果闭环由下列入口负责：

- `save-result`
- `auto-sync-results`
- `result-sync-daemon`
- `sync-pending-results-review`

结果命中后，`runtime/result_sync.py` 与 `result_manager.py` 会协同刷新：

- SoT 比分/备注
- `MEMORY.md`
- `prediction_archive.json`
- 准确率统计
- RAG / 记忆样本 / review-learning 相关衍生数据

`accuracy --refresh` 仍然可用，但更多是显式重建入口，不是唯一的日常闭环方式。

对 replay / reanalysis 场景，正式维护流新增了 `apply-reanalysis`：它会把选中的 replay 预测显式写回正式统计数据源。对 SoT-backed 联赛，这一步会同步更新 prediction archive 与 `<league>/teams_2025-26.md` 备注中的预测片段；这不是自动发生的，也不是 `accuracy --refresh` 的隐式副作用。

## 官方准确率与 replay 准确率

`accuracy` 输出里的 `overall` / `by_league` 代表正式记录口径，回答“系统正式留下的预测表现如何”；`reanalysis_report` 代表当前模型 replay 口径，回答“当前模型重放历史比赛时会如何表现”。两套统计并存是刻意设计，不会因为存在 `reanalysis_results*.json` 就自动互相覆盖；只有显式执行 `apply-reanalysis` 后，选中的 replay 结果才会进入正式统计数据源。

## 常用命令

### 环境检查

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py setup-openclaw --json
python3 prediction_system.py health-check --json
python3 prediction_system.py list-leagues --json
```

### 采集与预测

```bash
python3 prediction_system.py collect-data --league premier_league --date 2026-05-11 --json
python3 prediction_system.py predict-match --league premier_league --home-team 曼联 --away-team 切尔西 --date 2026-05-11 --json
python3 prediction_system.py predict-fourteen-issue --issue 26082 --json
python3 prediction_system.py harness-run --pipeline match_prediction --league premier_league --home-team 曼联 --away-team 切尔西 --date 2026-05-11 --json
```

> 说明：`predict-match` 仍是默认正式单场预测入口；`harness-run --pipeline match_prediction` 现在已对齐正式 `predict-match` 的 SoT / `archive_only` / friendly(reference-only) / `--no-write` 语义，但更适合在你需要查看阶段 artifacts 与 stage 审计记录时使用。

### 结果同步与复盘

```bash
python3 prediction_system.py pending-results --days-back 14 --json
python3 prediction_system.py auto-sync-results --json
python3 prediction_system.py save-result --match-id premier_league_20260511_曼联_切尔西 --home-score 2 --away-score 1 --json
python3 prediction_system.py result-sync-daemon --json
python3 prediction_system.py sync-pending-results-review --days-back 30 --limit 20 --json
python3 prediction_system.py accuracy --refresh --json
```

### 高级维护：Replay / Reanalysis Apply

```bash
python3 prediction_system.py apply-reanalysis --league la_liga --dry-run --json
python3 prediction_system.py apply-reanalysis --league bundesliga --only-improved --json
python3 prediction_system.py apply-reanalysis --league ligue_1 --only-improved --refresh-accuracy --json
```

推荐顺序是：先准备 `reanalysis_results_<league>.json`，再用 `--dry-run` 预览候选，确认后用 `--only-improved` 做保守 apply，需要时再附带 `--refresh-accuracy`。这条链路属于受控维护动作，不应被当作默认日常赛后闭环。

### RAG 与仓库治理

```bash
python3 prediction_system.py rag-rebuild --json
python3 prediction_system.py rag-diagnose --json
python3 prediction_system.py sync-memory-rag --json
python3 prediction_system.py purge-nonreal-data --json
python3 prediction_system.py refresh-repo-docs --json
```

## 目录概览

```text
europe_leagues/
├── app/cli.py
├── prediction_system.py
├── enhanced_prediction_workflow.py
├── domain/
├── runtime/
├── storage/
├── harness/
├── collectors/
├── .okooo-scraper/
├── premier_league/
├── la_liga/
├── serie_a/
├── bundesliga/
├── ligue_1/
└── world_cup/
```

## 相关文档

本子目录当前仍有效的高价值文档：

- `README_使用指南.md`
- `ODDS_FETCH_GUIDE.md`
- `docs/INDEX.md`
- `docs/PRD_足球预测系统_2026.md`
- `docs/upset_warning_guide.md`
- `../debug-local-odds-access.md`

当前 okooo 相关自动化测试命令：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 -m unittest test_okooo_save_snapshot test_okooo_mobile_access test_okooo_fetch_daily_schedule test_okooo_browser
```

算法 / 持久化 / 评分相关核心测试（本轮体检后全套绿）：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 -m unittest test_algorithm_fixes test_market_fusion test_rating_service test_json_io test_predict_match_e2e test_result_manager
# 或一键全量
python3 -m unittest discover -s . -p "test_*.py"
```

如需显式运行 Playwright 烟雾测试：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
OKOOO_BROWSER_E2E=1 python3 -m unittest test_okooo_browser
```

其中与澳客访问和实时赔率最相关的当前结论是：

- 正式快照链默认走 `local-chrome`
- 默认访问口径是 `iPhone Safari UA + Referer: https://m.okooo.com/`
- 公共移动设备池由 `okooo_mobile_access.py` 统一维护，当前为 `500` 组 `iPhone Safari` profile，分布在多个 iPhone device pool 上
- 命中验证页后，当前入口路径会快速返回 `verification_required` 并停止本路径重试；同时会打开基于 `match_id + market_family` 的 TTL breaker，并在市场页访问前执行最小间隔节流，避免持续撞验证页
- hub 链路在每次整页跳转（→`handicap.php`、→`odds.php`）后及解析完四盘后都做强阻断判定，任意盘口命中验证墙都会把 `blocked` 上抛到顶层触发熔断与换池重入，避免中途撞墙被当成「没开盘」而静默丢数据
- 强阻断判定 `_page_blocked_now` 带「赔率数字逃生阀」：页面已渲染出 ≥6 个 `x.xx` 赔率数字时一律判为正常页、绝不判墙（真实滑块/验证墙不会渲染完整赔率表），避免把解析得到的真实数据误判吞掉
- 欧值 `odds.php` 撞墙时仅标记欧赔/凯利 `blocked`，保留同会话已拿到的真实亚值/大小球；并按 `OUZHI_RETRY_WAITS`（默认 `3,5,10`）阶梯重试，仍失败则触发换新设备指纹 odds-only 会话单独重抓（`--no-odds-fresh-session` 可关闭）
- 快照脚本可选盘口参数：`--market-dwell`（每盘解析前停留秒数，默认 5，等价 `OKOOO_MARKET_DWELL`）、`--ouzhi-retry-waits`（等价 `OKOOO_OUZHI_RETRY_WAITS`）、`--odds-only`（独立冷会话只抓欧赔/凯利）
- 固定 IP 单机抗封（默认全开、无需代理）由三道防线兜底，与代理路线互补：
  - **进程级全局频控闸** `_global_pace_gate`：相邻深度导航强制最小间隔（`OKOOO_MIN_REQUEST_INTERVAL`，默认 `2.5s`，CLI `--min-request-interval` 可调），避免短时间高频请求触发风控
  - **节奏随机抖动** `_jittered(±35%)`：把所有等待/间隔（暖站、重试、市场节流闸）打散成非固定值，规避「机械等长间隔」这一机器人特征
  - **stealth 指纹屏蔽** `_install_stealth_script`：在 `_connect_if_needed` 内经 CDP `Page.addScriptToEvaluateOnNewDocument` 于文档启动前注入，抹掉 `navigator.webdriver`、补 `window.chrome` / `navigator.languages` / permissions，best-effort try/except 不阻断主链
  - 设备指纹池（`okooo_mobile_access.py` 的 `_build_profiles` / `fresh_mobile_profile`）锁定每设备 viewport+DPR、仅轮换 UA 版本，跨池干净轮换，与上述行为层抗封叠加
  - 物理上限说明：单机固定 IP 下抗封是「降低撞墙概率 + 撞墙后自动恢复」，不是 100% 不撞；真正高频/大批量仍需代理资源
- 正式 `predict-match` 已验证可稳定拿到真实欧赔、亚值、大小球、凯利数据
- `premier_league / 伯恩利 vs 狼队 / 2026-05-24 / MatchID=1296105` 已验证真实盘口回流后可修正最终预测方向

仓库根下的 skills 位于：

- `/Users/bytedance/trae_projects/.trae/skills/`

如果文档、skill 与代码冲突，以当前代码实现为准，优先看：

- `app/cli.py`
- `domain/persistence.py`
- `runtime/result_sync.py`
- `result_manager.py`
- `domain/writeback.py`
