---
title: Europe Leagues 项目架构与模块划分
owner: europe_leagues
version: v2.6
last_updated: 2026-06-18
---

# Europe Leagues 项目架构与模块划分（技术分析）

本文目标：
- 用当前仓库中的真实代码结构解释分层、依赖与职责边界
- 更新文档中过时的路径描述，特别是 runtime 文件、CLI 命令集与环境依赖边界
- 区分正式主链、兼容壳、支撑脚本与历史存量实现

范围：
- 代码：`/Users/bytedance/trae_projects/europe_leagues`
- 治理与 persona：`/Users/bytedance/trae_projects/agents/*.md`、`/Users/bytedance/trae_projects/agent_runtime_registry.py`

---

## 1. 仓库总览

当前仓库已经形成稳定的“正式主链 + runtime 落盘 + 支撑脚本”结构，可按 6 层理解：
- **接口层**：`prediction_system.py`、`app/cli.py`、`harness/*`
- **编排层**：`domain/predictor.py`、`enhanced_prediction_workflow.py`
- **领域层**：`domain/*` 中的特征、赔率、临场、推理、后处理、持久化、RAG、报告与写回服务
- **采集层**：`collectors/*` 及仍保留的 `okooo_*`、`data_collector.py`、`sofascore_team_context.py`
- **模型与存储层**：`models/*`、`storage/*`、`runtime/*`、`.okooo-scraper/*`
- **治理层**：`agents/*.md`、`agent_runtime_registry.py`

这里的“仓库架构”有两个关键变化已经固定下来：
- 对外命令入口已经收敛到 `app/cli.py`，`prediction_system.py` 只保留兼容壳
- 运行时数据不再散落在根目录，而是统一通过 `runtime/paths.py` 指向 `.okooo-scraper/runtime/`、`.okooo-scraper/snapshots/`、`.okooo-scraper/schedules/`

对 Hermes 或其他接入方的固定识别规则：
- 可以从 `prediction_system.py` 发现项目入口
- 但必须继续下钻到 `app/cli.py` 识别并执行真实命令
- 不要把 `prediction_system.py` 当作业务主逻辑实现层

### 1.1 关键入口

- CLI 兼容入口：`europe_leagues/prediction_system.py`
- CLI 实际实现：`europe_leagues/app/cli.py`
- 领域外壳：`europe_leagues/domain/predictor.py`
- 预测编排核心：`europe_leagues/enhanced_prediction_workflow.py`
- Harness 编排：`europe_leagues/harness/core.py`、`europe_leagues/harness/football.py`
- runtime 路径统一入口：`europe_leagues/runtime/paths.py`
- persona/runtime registry：`agent_runtime_registry.py`

### 1.2 正式联赛范围

当前正式纳入 `LEAGUE_CONFIG`、可直接通过主链命令调用的 competition code 共有 8 个：
- 五大联赛：`premier_league`、`la_liga`、`serie_a`、`bundesliga`、`ligue_1`
- 欧战：`europa_league`、`champions_league`、`conference_league`

需要特别注意：
- 仓库里出现某个联赛目录，不等于它已经进入正式主链
- 例如 `afc_champions_league/` 当前更像数据目录或快照落盘目录，不属于现行 `LEAGUE_CONFIG`

### 1.3 事实写回与归档边界

项目当前的“事实写回”为二元边界：
- **SoT 联赛完整写回**（五大联赛 + 世界杯）：`europe_leagues/<league>/teams_2025-26.md`（世界杯 `teams_2026.md`）+ 项目根 `MEMORY.md` 滚动记忆 + RAG + 归档
- **非 SoT 赛事 `archive_only`**：仅归档预测 + 登记赛果同步，不写 `MEMORY.md`/RAG/teams md
- **运行时归档与索引**：`europe_leagues/.okooo-scraper/runtime/*.json`

其中 `.okooo-scraper/runtime/` 已经是当前实现里的统一 runtime 数据目录，实际可见文件包括：
- `prediction_archive.json`
- `prediction_memory_odds_samples.json`
- `result_sync_registry.json`
- `accuracy_stats.json`
- `rag_cases.json`
- `rag_index.json`
- `rag_registry.json`
- `sofascore_team_ids.json`

另外还有两类运行时目录：
- `europe_leagues/.okooo-scraper/snapshots/`：赔率快照、抓取结果、欧战别名目录快照
- `europe_leagues/.okooo-scraper/schedules/`：赛程抓取结果

这意味着文档、脚本或调用方如果还把 `prediction_archive.json` 理解为“根目录文件”，已经不再准确；当前真实落点是 `.okooo-scraper/runtime/`。

---

## 2. 分层架构

建议继续用“外到内”六层理解当前代码：
- L0 接口层：CLI / Harness / health-check / setup-openclaw
- L1 编排层：orchestration / pipeline / stage
- L2 领域层：特征、赔率、临场、推理、后处理、持久化、RAG、报告、写回
- L3 采集层：赛程、快照、球队上下文、别名归一化、driver 适配
- L4 模型与存储层：Poisson / Dixon-Coles / Fusion / SoT / runtime archive / RAG index / 路径管理
- L5 治理层：persona / agent roles / runtime_profile

```mermaid
flowchart TB
  subgraph L0[接口层]
    Compat[prediction_system.py]
    CLI[app/cli.py]
    EnvCmd[health-check / setup-openclaw]
    HarnessCLI[harness-run / harness-list]
  end

  subgraph L1[编排层]
    DP[domain/predictor.py]
    EP[enhanced_prediction_workflow.py]
    HP[HarnessPipeline]
  end

  subgraph L2[领域层]
    FE[features.py]
    OD[odds.py]
    LV[live.py]
    IF[inference.py]
    PP[postprocess.py]
    PS[persistence.py]
    RAGSVC[rag.py]
    RP[reporting.py]
    WB[writeback.py]
    TS[team_strength.py]
    UP[intelligence.py / upset.py]
  end

  subgraph L3[采集层]
    COL1[collectors/sporttery.py]
    COL2[collectors/okooo.py]
    COL3[collectors/sofascore.py]
    COL4[collectors/aliasing.py / odds_snapshots.py]
    Legacy[data_collector.py / okooo_* / sofascore_team_context.py]
  end

  subgraph L4[模型与存储层]
    Models[models/*]
    Storage[storage/*]
    Paths[runtime/paths.py]
    Runtime[runtime/cache.py / memory_samples.py / result_sync.py / rag_store.py]
    RuntimeDir[.okooo-scraper/runtime/*.json]
    Snapshots[.okooo-scraper/snapshots]
    SoT[<league>/teams_2025-26.md]
    Memory[MEMORY.md]
  end

  subgraph L5[治理层]
    Persona[agents/*.md]
    Registry[agent_runtime_registry.py]
    Skill[skill docs]
    Hermes[Hermes]
  end

  Compat --> CLI
  CLI --> EnvCmd
  CLI --> DP --> EP
  CLI --> HarnessCLI
  HarnessCLI --> HP
  HP --> EP
  EP --> FE
  EP --> OD
  EP --> LV
  EP --> IF
  EP --> PP
  EP --> PS
  EP --> RAGSVC
  EP --> RP
  EP --> WB
  EP --> TS
  EP --> UP
  EP --> COL1
  EP --> COL2
  EP --> COL3
  EP --> COL4
  EP --> Legacy
  EP --> Models
  EP --> Storage
  EP --> Paths
  EP --> Runtime
  Runtime --> RuntimeDir
  COL2 --> Snapshots
  Storage --> SoT
  PS --> Memory
  Registry --> CLI
  Registry --> HP
  Registry --> EP
  Persona --> Registry
  Skill --> Compat
  Hermes --> Compat
```

---

## 3. 核心模块划分

### 3.1 接口层

| 模块 | 文件 | 当前职责 |
|---|---|---|
| CLI 兼容入口 | `prediction_system.py` | 保持旧调用路径不变，内部直接转发到 `app/cli.py` |
| CLI 主实现 | `app/cli.py` | 子命令注册、参数解析、JSON envelope、`runtime_profile` 注入、命令级编排 |
| 环境检查与安装指引 | `app/cli.py` | 提供 `health-check`、`setup-openclaw`，输出依赖状态、driver 状态与安装建议 |
| Harness Core | `harness/core.py` | 定义 `HarnessContext`、`PipelineStage`、`HarnessPipeline`，负责阶段执行、审计记录与 `runtime_profile` 注入 |
| Football Harness | `harness/football.py` | 注册 `match_prediction`、`result_recording` 两类 pipeline，桥接 collect / predict / save-result / accuracy 到正式业务能力 |

当前正式 CLI 命令已经不只包含业务命令，还包含运维/环境命令：
- 业务命令：`predict-match`、`predict-fourteen-issue`、`collect-data`、`save-result`、`auto-sync-results`、`accuracy`
- RAG 命令：`rag-rebuild`、`rag-diagnose`、`sync-memory-rag`
- 编排命令：`harness-list`、`harness-run`
- 环境命令：`health-check`、`setup-openclaw`

这说明接口层现在承担两种职责：
- 对业务主链提供统一命令入口
- 对澳客抓取环境和 openclaw 依赖提供统一的可观测性入口

其中 Harness 需要特别强调两点：
- Harness 不是独立于 CLI 的第二入口，而是 `app/cli.py` 暴露出来的一组正式命令
- Skill 与 Hermes 在需要阶段化、可审计输出时，应选择 `harness-run`，而不是绕开 CLI 直接调用 `harness/*.py`

### 3.2 编排层与领域层

| 模块 | 文件 | 当前职责 |
|---|---|---|
| 领域外壳 | `domain/predictor.py` | 对接口层暴露 `DomainPredictor`，屏蔽内部大文件实现 |
| 主编排 | `enhanced_prediction_workflow.py` | 维护 `LEAGUE_CONFIG` 与预测主链 orchestration |
| 特征服务 | `domain/features.py` | EWMA、analysis context、上下文增强与赛前补齐 |
| 赔率服务 | `domain/odds.py` | 盘口解析、真实大小球线补齐、历史赔率参考 |
| 临场服务 | `domain/live.py` | 实时刷新、已有快照复用、driver 透传、上下文注入 |
| 推理服务 | `domain/inference.py` | 组织核心推理输入、盘口/概率校准与主预测输出；产出 `tri_axis_consistency` 影子诊断层（见 3.7）；`apply_market_operation_adjustment` 用让球 6 口诀常开偏置驱动胜平负概率 |
| 比分投影 | `domain/score_projection.py` | 比分（top3）唯一数据源：亚值口诀定方向、OU 6 规则定大小球，在泊松网格交集上取条件概率（见 3.8） |
| 后处理 | `domain/postprocess.py` | 概率归一、凯利、review-learning 调整、大小球水位引擎（`extract_over_under_market_signal` 含 OU 操盘 6 规则）、结果对象整形与 RAG 解释文本拼装 |
| 持久化 | `domain/persistence.py` | 作为 `PredictionPersistenceService` owner 编排预测落盘，统一写入 `MEMORY.md`、runtime archive、滚动记忆样本、RAG 索引与赛果同步登记 |
| RAG 服务 | `domain/rag.py` | 封装 `HybridRAGService`，供主链读取结构化相似案例与轻量决策增强 |
| 报告服务 | `domain/reporting.py` | 预测报告格式化与 RAG 记忆解释输出 |
| 文本写回 | `domain/writeback.py` | 写回 `teams_2025-26.md` 备注列 |
| 球队实力 | `domain/team_strength.py` | 球队强弱、伤病与比赛画像支撑 |
| 情报/爆冷 | `domain/intelligence.py`、`domain/upset.py` | 市场共振、爆冷风险、错配提示；`_derive_lineup_edge` 用首发身价+缺阵驱动主客 λ（见 3.9） |

要点：
- `EnhancedPredictor` 仍然是当前主链核心，不是极薄壳
- 但高耦合逻辑已经明显拆到 `domain/*`，形成较清晰的服务边界
- `domain/persistence.py` 已成为业务主链与 runtime 文件系统之间的关键落盘桥
- `domain/live.py` 已承担“先复用已有快照，再按 driver 刷新”的临场输入组织职责

### 3.3 采集层

| 模块 | 文件 | 当前职责 |
|---|---|---|
| 澳客适配 | `collectors/okooo.py` | driver 状态探测、快照读取适配、默认 `local-chrome` 正式链与显式 `browser-use` 调试入口 |
| 赛程采集 | `collectors/sporttery.py` | `collect-data` 与 Harness `collect_data` 阶段的主要采集入口 |
| SofaScore 采集 | `collectors/sofascore.py` | 球队上下文、资料补充与辅助抓取 |
| 归一化与快照仓库 | `collectors/aliasing.py`、`collectors/odds_snapshots.py` | 队名别名归一、CSV/JSON 快照读取 |
| 存量脚本 | `data_collector.py`、`okooo_*`、`sofascore_team_context.py` | 历史入口、调试脚本、补数脚本或兼容实现 |
| 联赛数据目录 | `<league>/analysis/*`、`players/*.json` | 赔率历史、快照、球员资料、联赛侧上下文数据 |

采集层当前最大的架构特征不是“只剩一个入口”，而是“双形态并存”：
- 正式抽象层已经在 `collectors/*`
- 历史脚本仍然大量存在，并且部分仍被主流程间接依赖

此外，澳客采集已不只是网页抓取：
- `collectors/okooo.py` 同时承担依赖探测与 driver 选择
- `app/cli.py health-check` 会直接消费它输出的 `browser-use` / `local-chrome` 状态

### 3.4 模型、存储与 runtime 层

| 模块 | 文件 | 当前职责 |
|---|---|---|
| 模型 | `models/poisson.py`、`models/dixon_coles.py`、`models/fusion.py` | 核心概率模型与融合逻辑 |
| SoT 存储 | `storage/teams_md.py` | 五大联赛 `teams_2025-26.md` 的稳定读写边界 |
| 归档存储 | `storage/archive.py` | `.okooo-scraper/runtime/prediction_archive.json` 的读写边界 |
| 统计存储 | `storage/accuracy.py` | `.okooo-scraper/runtime/accuracy_stats.json` 的读写边界 |
| 路径管理 | `runtime/paths.py` | 统一管理 `MEMORY.md`、runtime、snapshots、schedules 与 teams 文件路径 |
| 滚动记忆样本 | `runtime/memory_samples.py` | 从 `MEMORY.md`、archive、赛果中构建结构化赔率样本 |
| 赛果同步 | `runtime/result_sync.py` | 预测后登记、到期检查、后台轮询与 match_id 迁移 |
| RAG 索引 | `runtime/rag_store.py` | 构建 `rag_cases.json`、`rag_index.json`、`rag_registry.json` 并提供检索 |
| 结果管理 | `result_manager.py` | archive / result / accuracy 底座能力，承接兼容型赛果写回与联动更新 |

这里有一个容易被忽略但很重要的设计点：
- `storage/*` 管的是“稳定文件边界”
- `runtime/*` 管的是“行为与索引更新”
- `.okooo-scraper/runtime/*` 才是“物理落盘位置”

也就是说，当前不是简单的 “storage = 文件、runtime = 内存”：
- `storage/*` 偏向稳定读写 API
- `runtime/*` 偏向运行时流程、增量同步和索引维护

### 3.5 RAG 记忆层

RAG 已经是主链的正式组成部分，当前职责拆成三段：
- `runtime/rag_store.py`：从 archive、滚动记忆样本、历史 odds 文件、快照目录构建混合检索索引
- `domain/rag.py`：封装 `HybridRAGService`，对主链暴露结构化检索能力
- `domain/postprocess.py`：把检索结果转成 `retrieved_memory_explanation`

RAG 当前真实依赖的数据源包括：
- `.okooo-scraper/runtime/prediction_archive.json`
- `.okooo-scraper/runtime/prediction_memory_odds_samples.json`
- `*/analysis/odds/*_odds.json`
- `.okooo-scraper/snapshots/**/*.json`

当前正式行为：
- `predict-match` / `predict-fourteen-issue` 可自动读取或按需重建 RAG 索引
- 新预测会把 `RAG记忆:` 原生写入 `MEMORY.md`（仅 SoT 联赛）
- 赛果回填后，滚动记忆样本与 RAG 索引会联动刷新

### 3.6 支撑脚本与测试边界

仓库根目录仍然存在大量支撑脚本，它们不是主链分层的一部分，但构成当前工程现实：
- 批处理与补数：`backfill_odds_*`、`migrate_prediction_history_to_teams_md.py`
- 球员与名单更新：`batch_update_players.py`、`update_2026_players.py`、`supplement_rosters_and_numbers_from_sofascore.py`
- 排名与数据维护：`update_standings_from_*`
- 旧预测/旧工作流兼容：`optimized_prediction_workflow.py`、`match_predictor.py`
- 脚本式测试：`test_data_collector.py`、`test_okooo_browser.py`、`test_result_manager.py`

这些文件说明当前仓库仍不是“完全收敛到一个 package”：
- 正式主链已经收敛
- 但维护、回填、补数、兼容与调试仍大量依赖根目录脚本

---

## 3.7 三轴影子诊断层（tri_axis_consistency）

这是 `domain/inference.py` 在主预测之外产出的一个**纯诊断影子层**，落在结果对象的 `tri_axis_consistency` 字段。核心纪律：**只标注、不改方向、不改概率、不加权**。三轴/各信号同源于同一市场、彼此高度相关，所以这里做的是「同向→标注加强、背离→标注矛盾」的门控，绝不做加权叠加（避免重复计价）。

实现入口：`InferencePipelineService.compute_tri_axis_consistency(...)`，调用点在 `domain/inference.py` 主链尾部，输入取自当前 odds 快照（`欧赔/亚值/凯利/大小球`）。

### 轴与信号构成

| 轴 / 信号 | 来源 | 含义 |
|---|---|---|
| 方向轴 `direction_axis` | 模型最终 1X2 | 领先方向 + 强度（`decisive`=非平局且领先 ≥0.10） |
| 大小球轴 `ou_axis` | 大小球概率 | over/under 谁占优 + 强度 |
| 操盘轴 `operation_axis` | 庄家形态判定 | 真实盘/诱导盘/中性盘 + 诱导方向（含让球方向手法矩阵，见下） |
| 临场资金轴 `market_drift` | 封盘热门赔率漂移 | 封盘最低赔率方相对开盘的赔率漂移（见下） |

### 让球方向操盘手法矩阵（`direction_handicap_matrix`）

`classify_market_operation_pattern(...)` 在操盘轴内附带一张「亚盘升降盘 × 让球方水位档位 × 欧赔方向」的方向手法矩阵，落在判定结果的 `direction_handicap_matrix`，并经三轴层透传到 `tri_axis_consistency.direction_handicap`，最终以「让球口诀[…]」并入 `verdict_summary`。

口径（世界杯无伤病/阵容数据，用**欧赔热门方升/降**代理「有无利空」：热门赔率降=被加注/真热，升=被看衰/疑似利空）：

| 盘口 | 看哪侧水位 | 欧赔方向 | 判定 `verdict_dir` | 含义 |
|---|---|---|---|---|
| 升盘（让球加深） | 上盘(热门)高水 | 主降 | `block_up_home_genuine` | ✅阻上(主真赢) |
| 升盘 | 上盘高水 | 主升 | `lure_up_home_fade` | ⚠️诱上(主难赢) |
| 升盘 | 上盘低水 | —（非主降） | `lure_up_hot_death` | ⚠️诱上(大热必死) |
| 降盘（门槛降低） | 下盘(客)低水 | 主升/客降 | `protect_dog_genuine` | ⚠️防客(客拿分) |
| 降盘 | 下盘低水 | 主降/急降盘 | `lure_dog_no_point` | ✅诱客(客无分,主稳) |
| 降盘 | 下盘高水 | — | `block_down_dog_hard` | ✅阻下(客难打出,主稳) |

实现要点：
- **水位口径**：`home_water/away_water` 存的是小数赔率全值，判档前先转港水（`港水=赔率-1`），再按 ≤0.85 低 / ≥0.95 高 / 之间 中分档。
- **触发门控**：仅当亚盘有动作（`hm`）且能取到让球方终盘水位时启用；升盘看热门(上盘)水位、降盘看客队(下盘)水位。
- **优先级**：降盘+下盘低水时，「主看衰/客加注→防客」优先于「急降盘→诱客」。升盘+低水若欧赔同时深压主队（smart money 背书）则不判诱上，让位给既有升盘背书印证逻辑。

> **2026-06-18 起口径变化（常开偏置 + 驱动比分方向）**：原「矩阵只打标、不单独加权改概率」的纪律已调整。现行口径分两条落地链路：
> 1. **驱动胜平负概率（`apply_market_operation_adjustment`，常开偏置路）**：历史学习权重路（`operation_weight_learner`）在世界杯样本不足（< `MIN_SAMPLES=80`）时 `reliability=0`，导致 6 口诀过去只透传标签。现新增类常量 `_DIRECTION_MATRIX_BIAS`（6 口诀 → 强侧视角有符号偏置）+ `_direction_matrix_bias` classmethod，构成**不依赖历史样本量的常开矩阵偏置路** `delta_matrix`（`±0.04` 封顶 = bias×0.18），与历史权重路 `delta_learned` 同向求和后合成 `delta`（`±0.06` 封顶）；正=印证强侧（从平局让给强侧），负=诱导（从强侧回撤，60% 平局 / 40% 冷门）。两路皆 0 时早退 `no_signal_label_only`。
> 2. **驱动比分方向（`domain/score_projection.py`，见 3.8）**：`verdict_dir` 经 `_VERDICT_DIR_INTENT` 映射为比分方向意图（印证类→强侧获胜；诱导类→平局+弱侧），覆盖原「模型 1X2 定方向」。
>
> 三轴影子层本身仍只标注、不改概率；上述加权发生在**主链 `apply_market_operation_adjustment` 与比分投影模块**，不在三轴诊断层内。

### 临场资金轴 + 置信度质检层（`market_drift`）

`_compute_market_drift(...)` 计算「封盘热门（封盘最低赔率方）相对开盘的赔率漂移」：
- `favorite_drifting_out=True`：封盘热门赔率较开盘上行（资金离场、热门走冷），经验上与平局/爆冷正相关
- 阈值 `+0.05` 仅作监控基线，样本不足以调参

关键设计是**置信度质检层**：欧赔/亚值/凯利数学同源（同一笔资金投射到不同盘口），它们同向移动是「真实资金流」的相互印证，可滤掉单家欧赔抓取噪声——但**不是独立证据，绝不加权**。`drift_confidence`：
- `high`：欧赔走冷 + 亚值或凯利同向印证（≥1 个）→ 真实资金流
- `low`：欧赔孤证（亚值、凯利均不印证或缺失）→ 疑似抓取噪声，**降级不触发背离标记**
- `n/a`：欧赔未达阈值，无需质检

大小球漂移（`ou_line_drift`，line/水位）是唯一真正独立的维度（总进球预期，与胜负方向资金无关），**单独记录、不并入胜负资金共振**。

#### 临场压盘诱多识别（`ou_line_down_low_water_trap`，`domain/postprocess.py`）

大小球水位引擎 `extract_over_under_market_signal(...)` 新增一条静态手法识别：当赛前格局被压（`ou_line_down` 降盘）、但 over 侧静态处在**中低水位**（庄家压低盘面却不肯为大球开高赔付），且模型未判小球（`goal_pressure != 'under'`）、且与「盘水背离加成」互斥（背离要求水位仍在下降，此处只看静态中低水）时，判定为「压盘诱小、暗里防大」，给 over 侧加成 `pace_shift`（上限 0.10，按水位档位缩放），把比分推向大格局，并追加信号 `ou_line_down_low_water_over_trap`。

持久化字段：`market_drift` 含 `favorite_side/favorite_open/favorite_close/drift/threshold/favorite_drifting_out/favorite_steaming_in/drift_confidence/confirmations/confirm_count/ou_line_drift`。

### 背离标记与研判结论

`divergence_flags` 三类背离（纯标注）：
- `direction_follows_luring_side`：方向轴与庄家诱导方向一致 → 警惕跟庄入坑
- `decisive_winner_but_low_scoring`：方向看决定性大胜但大小球偏小 → “大胜低进球”矛盾
- `favorite_drifting_out_against_direction`：封盘热门走冷、资金离场且与模型方向同向 → 平局/爆冷风险上升。**质检门控：仅当 `drift_confidence=high` 才触发，孤证降级不报**

`verdict_summary` 把三轴揉成一句人类可读研判（`_compose_tri_axis_verdict`），仅供人工参考。CLI `_print_tri_axis`（`app/cli.py`）以「临场→{热门}走冷(±X·共振/孤证)」展示。

### 持久化与回测

`tri_axis_consistency`（含嵌套 `market_drift`）在 `result_manager.py` 的 compact 白名单内，归档自动持久化到 `.okooo-scraper/runtime/prediction_archive.json`，供后续回测。全量 68 样本回测中，封盘走冷场按置信度切分：共振(high)组热门没兑现约 70%，孤证(low)组被正确降级，验证质检层确实在区分真实资金流与单源噪声。

---

## 3.8 比分投影单一数据源（`domain/score_projection.py`）

比分（top3 预测比分）此前散落在三处各算各的：网页卡片用本地实现按「方向+大小球」从泊松网格重算，而 `MEMORY.md` 与 `teams_2026.md` 直接取模型原始 `top_scores`，导致同一场三处对不上。2026-06 起抽出 `domain/score_projection.py` 作为**唯一比分数据源**，网页 `scripts/build_world_cup_daily_html.py`、写回 `domain/writeback.py`、持久化 `domain/persistence.py` 全部 import 它，模型原始 `top_scores` 仅作兜底。

核心三函数：
- `direction_of(d)`：**比分方向**。优先用亚值让球口诀 `verdict_dir` 经 `_VERDICT_DIR_INTENT` 映射——印证类（`block_up_home_genuine`/`lure_dog_no_point`/`block_down_dog_hard`）→ 让球方(`fav_side`)获胜；诱导类（`lure_up_home_fade`/`lure_up_hot_death`/`protect_dog_genuine`）→ 强侧不赢，在 平局/弱侧 中取模型概率更高者。**口诀未触发时回退模型 1X2**。口诀来源优先 `tri_axis_consistency.direction_handicap`，回退 `realtime.context_applied.market_operation_pattern.direction_handicap_matrix`。
- `allowed_outcomes(d)`：允许展示的胜负结果集合。印证类仅放行强侧获胜；诱导类放行 平局+弱侧；爆冷（中/高）再全放行。无口诀时回退模型方向，并保留「平局概率达标并入平局 / 方向为平局并入第二高方向 / 爆冷放行反向」逻辑。
- `project_scores_for_side(d)`：在「方向允许集 ∩ 大小球同侧」的泊松网格格子上算条件概率，取 top3。大小球侧由 OU 操盘 6 规则经 `over_under` 概率体现（`_build(apply_dir, apply_ou)`）。候选不足 3 个时尾部在 `allowed_outcomes` 内放宽 OU 凑满 top3（既有契约），不再无视方向从全 1X2 拉取。

> **已知缺陷（待修）**：方向=平局 且 OU 判大球 且不对称 λ 时，平局比分（对角线）与「大球同侧」硬约束求交集后，merge-then-sort 可能把平局比分全部挤掉。建议修法「方向保底配额」（平局方向至少保留 1 个平局比分再用第二方向补足），尚未实施。

回归测试 `test_score_projection_direction.py`（8 例）覆盖印证/诱导/无口诀回退/OU 约束/来源回退；用真实分类器 `classify_market_operation_pattern` 构造模拟盘口验证过 `lure_up_home_fade`、`lure_up_hot_death` 两条诱导口诀能正确把比分方向从主胜翻为平局/弱侧。

---

## 3.9 阵容驱动 λ（首发身价 + 缺阵 → 进攻强度）

澳客「阵容」标签（`form.php`，`https://m.okooo.com/match/form.php?MatchID=<id>`）作为常开数据腿接入正式预测链，把首发身价对比与缺阵损失折算成主客进攻强度 λ 的乘性微调，让实力差与临场减员体现到胜平负/比分/大小球概率上。

数据流：
- 采集 `okooo_save_snapshot.py`：`local-chrome` 点开「阵容」标签 → 8× `scrollBy` 触发懒加载 → 回滚顶部解析 `document.body.innerText`，抽出首发身价/总身价/缺阵人数与身价损失/首发名单（统一归一到「万」），写入快照 `阵容` 键。
- 传输 `okooo_live_snapshot.py`：`extract_current_odds` 把快照 `阵容` 归一到 `current_odds["阵容"]`（`found` → `available`），随 `match.odds_data` 流入推理链。
- 调 λ `domain/intelligence.py`：`_derive_lineup_edge(current_odds)` 计算
  - `value_edge = clip(log(首发身价_主/首发身价_客) × 0.05, ±0.06)`；
  - `injury_edge = clip(对方缺阵贡献 − 我方缺阵贡献, ±0.05)`，单边贡献 = `缺阵人数 × 0.006 + 缺阵身价损失 / max(主,客首发身价) × 0.20`（对方缺阵助我）；
  - 合成 `home_adv/away_adv` 增量，并入 `_build_match_intelligence` 的 `quant_adjustment`，最终 `home/away_lambda_scale = clip(1.0 + adv × 0.6, 0.90 ~ 1.10)`；
  - `quant_adjustment.lineup_edge` 落诊断字段（available/home_edge/away_edge/value_edge/injury_edge），并把「首发身价/缺阵」摘要追加为研判 signal。阵容不可用或身价缺失时安全早退（零偏置）。

回归测试 `test_lineup_lambda_adjustment.py`（12 例）覆盖传输（含未找到/缺字段跳过）与 λ-edge（等身价无偏置/强弱单调+封顶/缺阵助对手+封顶/不可用安全/真实捷克 vs 南非）。实测捷克 vs 南非（首发身价 8938万 vs 1345万 ~6.6x、南非缺阵 2 人）→ `home_lambda_scale=1.0454 / away_lambda_scale=0.9546`。

---

## 4. 端到端流程图

### 4.1 单场预测

```mermaid
sequenceDiagram
  autonumber
  participant U as User/Automation
  participant Compat as prediction_system.py
  participant CLI as app/cli.py
  participant DP as DomainPredictor
  participant EP as EnhancedPredictor
  participant Live as LiveRefreshService
  participant Infer as InferencePipelineService
  participant RAG as HybridRAGService
  participant Persist as PredictionPersistenceService
  participant SoT as teams_2025-26.md
  participant Memory as MEMORY.md
  participant Runtime as .okooo-scraper/runtime/*

  U->>Compat: predict-match --json
  Compat->>CLI: main()
  CLI->>DP: predict_match(...)
  DP->>EP: predict_match(...)
  EP->>Live: prepare_prediction_inputs(...)
  Live->>Live: 复用已有快照 / 刷新澳客快照
  EP->>Infer: run(...)
  EP->>RAG: retrieve_match_memory(...)
  EP->>Persist: persist_prediction(...)
  Persist->>SoT: 五大联赛写回预测备注
  Persist->>Memory: 追加滚动记忆
  Persist->>Runtime: archive / memory_samples / result_sync / rag_index
  EP-->>CLI: result + runtime_profile
  CLI-->>U: JSON envelope
```

### 4.2 赛后回填

```mermaid
flowchart LR
  A[predict-match / predict-fourteen-issue] --> R[.okooo-scraper/runtime/result_sync_registry.json]
  R --> T[result-sync-daemon / auto-sync-results]
  T --> B[result_manager.py]
  B --> C[league teams_2025-26.md]
  B --> M[MEMORY.md]
  B --> P[.okooo-scraper/runtime/prediction_archive.json]
  B --> S[.okooo-scraper/runtime/prediction_memory_odds_samples.json]
  B --> G[.okooo-scraper/runtime/rag_cases.json / rag_index.json / rag_registry.json]
  C --> D[accuracy --refresh]
  M --> D
  P --> D
  S --> D
  G --> D
  D --> E[.okooo-scraper/runtime/accuracy_stats.json]
```

### 4.3 Harness 编排

```mermaid
flowchart TB
  Compat[prediction_system.py]
  CLI[app/cli.py]
  H1[harness-run match_prediction]
  H2[harness-run result_recording]
  P1[HarnessPipeline]
  C1[collect_data stage]
  C2[predict_match stage]
  R1[save_result stage]
  R2[refresh_accuracy stage]
  OUT[inputs / artifacts / stages / runtime_profile / error]
  Skill[Skill]
  Hermes[Hermes]

  Skill --> Compat
  Hermes --> Compat
  Compat --> CLI
  CLI --> H1
  CLI --> H2
  H1 --> P1
  H2 --> P1
  P1 --> C1
  C1 --> C2
  P1 --> R1
  R1 --> R2
  C2 --> OUT
  R2 --> OUT
```

Harness 当前应理解为：
- `prediction_system.py` 只是发现入口，真实命令执行落在 `app/cli.py`
- `harness-run` 是正式 CLI 链路中的“可审计分支”，不是平行框架
- `match_prediction` 与 `result_recording` 都由 `HarnessPipeline` 组织阶段执行，并输出结构化审计结果
- Hermes 管理“何时选择 Harness 命令”，Skill 管理“什么时候应该走 Harness”，Harness 自身只管理阶段化执行

### 4.4 环境依赖链路

环境相关功能已经进入正式接口层，而不是散落在 README 说明中：
- `setup-openclaw`：输出安装引导与下一步建议
- `health-check`：汇总依赖、driver、联赛配置、runtime 文件可用性
- `collectors/okooo.py`：实际提供 `browser-use` / `local-chrome` 可用性判断

当前 driver 策略也已经明确写入代码：
- 默认仅走 `local-chrome`
- `browser-use` 只在显式指定调试时使用，不再作为正式链自动回退

---

## 5. 当前实现状态（按代码现状）

从当前代码可直接确认的架构结论如下：
- `prediction_system.py` 已经彻底收缩成兼容壳，真实命令逻辑集中在 `app/cli.py`
- `app/cli.py` 已不仅是“命令分发器”，同时也是 JSON envelope、runtime_profile、环境健康检查与安装指引入口
- `enhanced_prediction_workflow.py` 仍是最重的主编排文件，说明系统虽已模块化，但还没有彻底去中心化
- `domain/persistence.py` 是正式写回枢纽，负责把预测结果同步到 `MEMORY.md`、runtime archive、滚动记忆样本、RAG 索引和赛果同步登记
- `runtime/paths.py` 让主链对物理路径解耦，是当前 runtime 目录收敛的关键
- `collectors/okooo.py` 已从纯工具函数升级为“环境状态 + driver 选择 + 快照访问”的桥接层
- `harness/core.py` 与 `harness/football.py` 已提供可审计、可阶段化的正式编排能力，而不是简单的脚本包装

当前仍保留的现实约束：
- `result_manager.py` 依然很大，兼容职责重
- `data_collector.py`、`okooo_*`、`sofascore_team_context.py` 等存量脚本仍然存在且有现实依赖
- 根目录支撑脚本数量很多，说明维护工作尚未完全收敛到 package API
- 测试文件仍以脚本式分布在根目录，工程化测试体系还不统一

---

## 6. 当前目录（架构视角）

```text
europe_leagues/
  app/
    cli.py
  harness/
    core.py
    football.py
  domain/
    predictor.py
    features.py
    odds.py
    live.py
    inference.py
    postprocess.py
    persistence.py
    rag.py
    reporting.py
    writeback.py
    team_strength.py
    intelligence.py
    upset.py
  collectors/
    okooo.py
    sporttery.py
    sofascore.py
    aliasing.py
    odds_snapshots.py
  models/
    poisson.py
    dixon_coles.py
    fusion.py
  storage/
    teams_md.py
    archive.py
    accuracy.py
  runtime/
    paths.py
    cache.py
    memory_samples.py
    result_sync.py
    rag_store.py
  .okooo-scraper/
    runtime/
      accuracy_stats.json
      prediction_archive.json
      prediction_memory_odds_samples.json
      result_sync_registry.json
      rag_cases.json
      rag_index.json
      rag_registry.json
      sofascore_team_ids.json
    snapshots/
    schedules/
    chrome_profile/
  enhanced_prediction_workflow.py
  prediction_system.py
  result_manager.py
  data_collector.py
  okooo_*.py
  sofascore_team_context.py
  sync-pending-results-review / auto-sync-results
  backfill_odds_*.py
  update_*.py
  test_*.py
  <league>/teams_2025-26.md
```

兼容策略仍然存在于以下几类文件：
- `prediction_system.py`：旧 CLI 路径兼容
- `optimized_prediction_workflow.py`：旧工作流/旧结果结构兼容
- `result_manager.py`：历史赛果与归档处理兼容
- 根目录大量脚本：历史操作习惯与批量维护任务兼容

---

## 7. 与 persona/六维的运行时承接

当前仓库已经把 persona 六维接入运行时输出：
- 文档来源：`agents/*.md`
- registry：`agent_runtime_registry.py`
- CLI 注入：`app/cli.py build_json_result()`
- Harness 注入：`harness/core.py HarnessPipeline.execute()`
- 预测结果注入：`EnhancedPredictor.predict_match()`
- 归档继承：archive 写回与结果对象都带 `runtime_profile`

```mermaid
flowchart LR
  A[agents/*.md] --> B[agent_runtime_registry.py]
  B --> C[CLI runtime_profile]
  B --> D[Harness runtime_profile]
  B --> E[predict_match runtime_profile]
  E --> F[JSON envelope / archive / pipeline result]
```

这部分的架构意义在于：
- persona 不再只是文档说明
- 它已经影响 CLI 输出、Harness 审计结果与预测归档结构

---

## 8. 快速定位

| 你想改什么 | 优先改哪里 | 备注 |
|---|---|---|
| 新增命令或调整参数 | `app/cli.py` | `prediction_system.py` 仅保留兼容 |
| 新增 pipeline | `harness/football.py`、`harness/core.py` | 先定义 stage，再补 handler |
| 调整主预测编排 | `enhanced_prediction_workflow.py`、`domain/predictor.py` | 主链入口仍集中在这里 |
| 调整三轴诊断/临场资金质检 | `domain/inference.py`（`compute_tri_axis_consistency` / `_compute_market_drift`） | 纯标记不改方向；质检门控见 3.7，CLI 展示在 `_print_tri_axis` |
| 调整临场快照/driver | `domain/live.py`、`collectors/okooo.py` | 优先围绕 `local-chrome` 正式链调整；`browser-use` 仅看显式调试场景 |
| 调整 RAG 索引与解释 | `runtime/rag_store.py`、`domain/rag.py`、`domain/postprocess.py` | 一边改索引，一边改解释文本 |
| 调整写回与归档 | `domain/persistence.py`、`domain/writeback.py`、`storage/*` | 注意 SoT、MEMORY、runtime archive 的一致性 |
| 调整 runtime 落盘路径 | `runtime/paths.py` | 不要在业务模块里硬编码路径 |
| 调整赛果同步 | `runtime/result_sync.py`、`result_manager.py` | 同时影响登记、轮询、回填 |
| 调整环境检查或安装提示 | `app/cli.py`、`collectors/okooo.py`、`/Users/bytedance/trae_projects/scripts/setup_openclaw_env.sh` | 这是当前 openclaw / 澳客依赖入口 |
| 调整 persona/runtime_profile | `agents/*.md`、`agent_runtime_registry.py` | 会影响 CLI / Harness / 预测输出 |
| 批量更新球员/赔率/排名 | 根目录 `update_*`、`backfill_*`、`batch_*` 脚本 | 这些仍是现实维护入口 |

---

## 9. 本次更新依据

本次文档更新以当前代码与目录现状为准，重点核对了以下文件：
- 入口与命令：`prediction_system.py`、`app/cli.py`
- 编排：`domain/predictor.py`、`enhanced_prediction_workflow.py`、`harness/core.py`、`harness/football.py`
- 推理诊断层：`domain/inference.py`（`compute_tri_axis_consistency` / `_compute_market_drift` / `_compose_tri_axis_verdict` / `classify_market_operation_pattern` 让球方向手法矩阵）、`domain/postprocess.py`（`extract_over_under_market_signal` 临场压盘诱多识别）、`app/cli.py`（`_print_tri_axis`）
- 采集：`collectors/okooo.py`
- 存储与 runtime：`storage/__init__.py`、`storage/archive.py`、`storage/accuracy.py`、`storage/teams_md.py`、`runtime/paths.py`、`runtime/memory_samples.py`、`runtime/result_sync.py`、`runtime/rag_store.py`
- 领域写回：`domain/live.py`、`domain/persistence.py`、`domain/reporting.py`、`domain/writeback.py`
- 治理：`agent_runtime_registry.py`
- 物理目录：`europe_leagues/.okooo-scraper/runtime/`

因此，本文档描述的是“当前代码已经呈现出的架构现状”，不是历史整改计划，也不是未来目标图。
