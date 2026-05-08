---
title: Europe Leagues 项目架构与模块划分
owner: europe_leagues
version: v2.3
last_updated: 2026-05-08
---

# Europe Leagues 项目架构与模块划分（技术分析）

本文目标：
- 用“整改后的真实代码结构”解释当前项目的分层、依赖与职责边界
- 说明哪些模块已经拆分完成，哪些地方仍保留兼容壳或存量脚本

范围：
- 代码：`/Users/bytedance/trae_projects/europe_leagues`
- 治理与 persona：`/Users/bytedance/trae_projects/agents/*.md`、`agent_runtime_registry.py`

---

## 1. 仓库总览

当前仓库已不再只是“脚本集合”，而是形成了 6 类稳定模块：
- **接口层**：CLI 入口、JSON envelope、自动化命令
- **编排层**：Harness pipeline/stage/context
- **领域层**：预测主流程编排与拆分后的领域服务
- **采集层**：赛程、快照、上下文与别名归一化
- **模型与存储层**：模型、SoT 写回、归档、统计、运行时路径、RAG 索引
- **治理层**：persona、六维定义、runtime_profile 注入

### 1.1 关键入口

- CLI 兼容入口：`europe_leagues/prediction_system.py`
- CLI 实际实现：`europe_leagues/app/cli.py`
- 领域外壳：`europe_leagues/domain/predictor.py`
- 预测编排核心：`europe_leagues/enhanced_prediction_workflow.py`
- Harness 编排：`europe_leagues/harness/*`
- persona/runtime registry：`agent_runtime_registry.py`

### 1.2 单一事实来源

项目当前对“事实/写回”的约束已分成双路径：
- **五大联赛 SoT**：只有 `premier_league / la_liga / serie_a / bundesliga / ligue_1` 这 5 个联赛，才以 `europe_leagues/<league>/teams_2025-26.md` 作为赛程/赛果/预测备注单一事实来源
- **欧战/杯赛 runtime-only**：不强写联赛 `teams_2025-26.md`，只写 `MEMORY.md`、`prediction_archive.json`、`prediction_memory_odds_samples.json`、`result_sync_registry.json`
- **competition config 已纳入主链**：`europa_league`、`champions_league`、`conference_league` 已进入 `LEAGUE_CONFIG`，都可以直接走 `predict-match`、`harness-run`、`match_id` 快照复用与真实盘口大小球链路，但写回仍保持 `runtime_only`
- **欧战快照目录别名已统一**：主链会按 canonical `league_code` 匹配 `欧联 / 欧罗巴 / 欧冠 / 欧协联` 等目录别名，避免中文目录与英文 `league_code` 脱节
- **历史归档字段已迁移清洗**：`migrate-archive` 会统一回填欧战记录中的 `league_code`、`league_name`、`snapshot_dir`、`snapshot_dir_aliases`、`snapshot_path`、`line_source`，并保留“缺真实快照则不伪造盘口来源”的边界
- **赔率快照/赛程抓取**写入 `europe_leagues/.okooo-scraper/snapshots/` 和 `schedules/`
- **canonical 记忆去重**：同一场非 SoT 比赛优先按真实 `match_id` 去重，只保留 1 条规范记忆
- **RAG 运行时索引**：统一落在 `rag_cases.json`、`rag_index.json`、`rag_registry.json`，由主链预测写入和赛果更新自动刷新

---

## 2. 分层架构

建议继续用“外到内”六层理解当前代码：
- L0 接口层：CLI / Harness
- L1 编排层：orchestration / pipeline / stage
- L2 领域层：特征、赔率、推理、后处理、持久化、RAG 服务
- L3 采集层：赛程、快照、球队上下文、别名归一化
- L4 模型与存储层：Poisson / Dixon-Coles / Fusion / SoT / 归档 / 准确率 / RAG index
- L5 治理层：persona / 六维 / runtime_profile

```mermaid
flowchart TB
  subgraph L0[接口层]
    Compat[prediction_system.py]
    CLI[app/cli.py]
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
    IN[intelligence.py / upset.py]
    LV[live.py / inference.py]
    PP[postprocess.py / persistence.py]
    RAGSVC[rag.py]
    RP[reporting.py / writeback.py / team_strength.py]
  end

  subgraph L3[采集层]
    COL[collectors/*]
    Legacy[data_collector.py / okooo_* / sofascore_team_context.py]
  end

  subgraph L4[模型与存储层]
    Models[models/*]
    Storage[storage/*]
    Runtime[runtime/*]
    RAGIDX[runtime/rag_store.py]
    SoT[teams_2025-26.md]
  end

  subgraph L5[治理层]
    Persona[agents/*.md]
    Registry[agent_runtime_registry.py]
  end

  Compat --> CLI
  CLI --> DP --> EP
  HarnessCLI --> HP --> EP
  EP --> FE
  EP --> OD
  EP --> IN
  EP --> LV
  EP --> PP
  EP --> RAGSVC
  EP --> RP
  EP --> COL
  EP --> Legacy
  EP --> Models
  EP --> Storage
  EP --> Runtime
  RAGSVC --> RAGIDX
  Storage --> SoT
  Registry --> CLI
  Registry --> HP
  Registry --> EP
  Persona --> Registry
```

---

## 3. 核心模块划分

### 3.1 接口层

| 模块 | 文件 | 当前职责 |
|---|---|---|
| CLI 兼容入口 | `prediction_system.py` | 对外保持旧命令路径不变，内部仅转发到 `app/cli.py` |
| CLI 主实现 | `app/cli.py` | 子命令路由、JSON envelope、`runtime_profile` 注入、命令级编排 |
| Harness Core | `harness/core.py` | `HarnessContext` / `PipelineStage` / `HarnessPipeline` / 审计输出 |
| Football Harness | `harness/football.py` | 注册 `match_prediction`、`result_recording` pipeline，并透传 `collect_data` 阶段拿到的 `match_id` / `match_time` |

要点：
- `prediction_system.py` 现在是兼容壳，不再承担真实命令实现
- 自动化调用应继续使用 `python3 prediction_system.py ... --json`
- 新功能优先落在 `app/cli.py`，避免再次把兼容层做厚
- 与 RAG 直接相关的正式命令已收敛为 `rag-rebuild`、`rag-diagnose`、`sync-memory-rag`

### 3.2 编排与领域层

| 模块 | 文件 | 当前职责 |
|---|---|---|
| 领域外壳 | `domain/predictor.py` | 对接口层暴露 `DomainPredictor`，屏蔽内部实现细节 |
| 预测编排核心 | `enhanced_prediction_workflow.py` | 保留主流程 orchestration，协调 live refresh、推理、后处理、持久化 |
| 特征服务 | `domain/features.py` | EWMA、联赛大小球学习、analysis_context 增强 |
| 赔率服务 | `domain/odds.py` | 盘口解析、历史赔率参考、大小球线解析与补抓 |
| 情报与爆冷 | `domain/intelligence.py`、`domain/upset.py` | 比赛画像、市场共振、爆冷风险识别 |
| 临场与推理 | `domain/live.py`、`domain/inference.py` | 快照刷新、输入准备、核心推理链、临场修正 |
| 后处理与持久化 | `domain/postprocess.py`、`domain/persistence.py` | 概率归一、凯利、结果对象装配、缓存/归档/MEMORY 写回 |
| RAG 服务 | `domain/rag.py` | 混合检索 RAG 服务封装、检索结果聚合、历史赔率相似盘口并入 |
| 报告/写回/球队实力 | `domain/reporting.py`、`domain/writeback.py`、`domain/team_strength.py` | 报告格式化、`teams_2025-26.md` 写回、球队强度分析 |

要点：
- `EnhancedPredictor` 仍然存在，但已从“超大业务全集”收缩为 orchestrator
- 当前最重要的结构变化不是删除旧类，而是把高耦合逻辑迁到可复用服务模块
- `TeamDataManager` 等旧名称保留兼容别名，用于降低外部调用回归风险
- `HybridRAGService` 已成为主预测链标准组成部分，`retrieved_memory` 与 `retrieved_memory_explanation` 属于正式结果结构

### 3.3 采集层

| 模块 | 文件 | 当前职责 |
|---|---|---|
| 采集包 | `collectors/okooo.py`、`sporttery.py`、`sofascore.py`、`aliasing.py`、`odds_snapshots.py` | 新的采集/归一化/快照读取边界 |
| 存量脚本 | `data_collector.py`、`okooo_*`、`sofascore_team_context.py` | 仍作为历史入口与调试脚本保留 |
| 联赛数据 | `<league>/analysis/*`、`players/*.json` | 赛程快照、赔率落盘、球员与上下文数据 |

要点：
- `collectors/` 是整改后的正式抽象层
- `okooo_*` 和 `data_collector.py` 仍在使用，但更适合作为脚本入口或兼容实现
- `OddsSnapshotRepository` 已集中处理 CSV/JSON 快照读取与 `current_odds` 转换

### 3.4 模型与存储层

| 模块 | 文件 | 当前职责 |
|---|---|---|
| 模型 | `models/poisson.py`、`models/dixon_coles.py`、`models/fusion.py` | 核心概率模型与模型融合 |
| 存储 | `storage/teams_md.py`、`archive.py`、`accuracy.py` | SoT 读写、归档、准确率统计的稳定边界 |
| 运行时 | `runtime/cache.py`、`runtime/paths.py`、`runtime/memory_samples.py`、`runtime/result_sync.py`、`runtime/rag_store.py` | 缓存、路径、滚动记忆样本、赛果自动同步与 RAG 索引管理 |
| 赛果管理 | `result_manager.py` | 历史兼容的赛果回填与 archive 迁移管理器 |

### 3.5 RAG 记忆层

RAG 已从“可选实验层”升级为当前正式架构的一部分，职责分为三段：
- `runtime/rag_store.py`：从 `prediction_archive.json`、`prediction_memory_odds_samples.json`、`analysis/odds/*_odds.json` 与 `.okooo-scraper/snapshots/**/*.json` 构建混合检索索引
- `domain/rag.py`：封装 `HybridRAGService`，供主预测链读取 `similar_cases`、`market_cases`、`upset_cases`
- `domain/postprocess.py`：将检索结果转成 `retrieved_memory_explanation`

当前正式行为：
- `predict-match` / `predict-schedule` 会自动读取或重建 RAG 索引
- 新预测会原生把 `RAG记忆:` 写入 `MEMORY.md`
- `sync-memory-rag` 主要用于把旧归档中的 `retrieved_memory_explanation` 回填到历史记忆条目
- 赛果回填或滚动记忆变化后会自动刷新 `prediction_memory_odds_samples.json` 与 RAG 索引

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
  participant Memory as MEMORY.md / runtime archive
  participant SoT as teams_2025-26.md

  U->>Compat: predict-match --json
  Compat->>CLI: main()
  CLI->>DP: predict_match(...)
  DP->>EP: predict_match(...)
  EP->>Live: prepare_prediction_inputs(...)
  EP->>Infer: run(...)
  EP->>RAG: retrieve_match_memory(...)
  EP->>Persist: persist_prediction(...)
  Persist->>SoT: 五大联赛 SoT 写回预测备注
  Persist->>Memory: 写入 MEMORY.md / prediction_archive.json / prediction_memory_odds_samples.json / rag_index
  Persist->>CLI: 欧战/杯赛仅写 runtime-only 归档与滚动记忆
  EP-->>CLI: result + runtime_profile
  CLI-->>U: JSON envelope
```

### 4.2 赛后回填

```mermaid
flowchart LR
  A[predict-match / predict-schedule] --> R[result_sync_registry.json]
  R --> T[result-sync-daemon / auto-sync-results]
  T --> B[result_manager.py]
  B --> C[league SoT / teams_2025-26.md]
  B --> M[MEMORY.md / prediction_archive.json / prediction_memory_odds_samples.json]
  B --> G[rag_cases.json / rag_index.json / rag_registry.json]
  C --> D[accuracy --refresh]
  M --> D
  G --> D
  D --> E[accuracy_stats.json]
```

### 4.3 Harness 编排

```mermaid
flowchart TB
  H[harness-run match_prediction] --> S1[collect_data stage]
  S1 --> S2[predict_match stage with match_id/match_time pass-through]
  S2 --> OUT[inputs / artifacts / stages / runtime_profile]
```

---

## 5. 本轮整改结果

本轮已经完成的核心整改包括：
- CLI 路由迁入 `app/cli.py`，`prediction_system.py` 降为兼容壳
- `EnhancedPredictor` 的 features、odds、writeback、upset、intelligence、snapshot、reporting、postprocess、persistence、live、inference、team_strength 已下沉到 `domain/` 或 `collectors/`
- `collectors/`、`models/`、`storage/`、`runtime/` 四类目录已落地
- `runtime_profile` 已贯穿 CLI、Harness、预测结果对象和 `prediction_archive.json`
- `runtime/result_sync.py` 已提供“预测后按开球时间推算完赛后 2 小时自动同步赛果”的运行时调度能力
- `runtime/memory_samples.py` 已把滚动记忆、赔率变化与已完赛结果同步成结构化样本，供历史赔率参考复用
- `runtime/rag_store.py` + `domain/rag.py` 已形成完整混合检索 RAG 记忆层，并接入主预测链
- `MEMORY.md` 新预测条目已支持原生写入 `RAG记忆:`，`sync-memory-rag` 只负责历史回填
- 大小球正式预测已切换为“真实盘口强约束”模式：仅接受 `snapshot_final / snapshot_initial`
- 当缺少真实大小球盘口时，主链保留胜平负预测，但 `over_under` 会明确返回 `missing_real_line`，不再退回 `default_2.5`
- `domain/live.py` 已支持按 `match_id` 复用本地已有快照，即使 `--no-refresh-odds` 也会优先读取真实盘口
- `europa_league`、`champions_league`、`conference_league` 均已完成正式 competition config 接线，验证可命中本地欧战中文快照目录并输出 `runtime_only + snapshot_final`
- 项目主范围内的 Python 模块已统一补充文件头 `模块说明`，方便后续定位职责边界
- 保留旧入口与兼容类型，避免外部自动化调用中断

仍然保留的现实约束：
- `enhanced_prediction_workflow.py` 仍是关键 orchestration 文件，还不是“极薄外壳”
- `data_collector.py`、`okooo_*`、`result_manager.py` 等历史脚本仍在被主流程或周边脚本依赖
- 仍需逐步把更多脚本入口收敛到 `collectors/`、`storage/` 与 `app/cli.py`

---

## 6. 整改后目录（现状）

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
    rag.py
    upset.py
    intelligence.py
    live.py
    inference.py
    postprocess.py
    persistence.py
    reporting.py
    writeback.py
    team_strength.py
  collectors/
    aliasing.py
    odds_snapshots.py
    okooo.py
    sofascore.py
    sporttery.py
  models/
    poisson.py
    dixon_coles.py
    fusion.py
  storage/
    teams_md.py
    archive.py
    accuracy.py
  runtime/
    cache.py
    paths.py
    memory_samples.py
    result_sync.py
    rag_store.py
  enhanced_prediction_workflow.py
  prediction_system.py
  result_manager.py
  data_collector.py
  okooo_*.py
  <league>/teams_2025-26.md
```

兼容策略：
- `prediction_system.py` 保留旧调用路径
- `optimized_prediction_workflow.py` 保留旧结果结构兼容
- `TeamDataManager` 等旧命名通过兼容别名继续可用
- `scripts/sync_memory_rag_explanations.py` 保留为历史数据补写脚本，但新预测不再依赖它才能出现 `RAG记忆`

---

## 7. 与 persona/六维的运行时承接

当前仓库已把 persona 六维真正接入运行时输出：
- 文档来源：`agents/*.md`
- registry：`agent_runtime_registry.py`
- CLI 注入：`app/cli.py build_json_result()`
- Harness 注入：`harness/core.py HarnessPipeline.execute()`
- 预测结果注入：`EnhancedPredictor.predict_match()`
- 归档注入：`result_manager.py` / `prediction_archive.json`

```mermaid
flowchart LR
  A[agents/*.md] --> B[agent_runtime_registry.py]
  B --> C[CLI runtime_profile]
  B --> D[Harness runtime_profile]
  B --> E[predict_match runtime_profile]
  E --> F[prediction_archive.json]
```

---

## 8. 快速定位

| 你想改什么 | 优先改哪里 | 备注 |
|---|---|---|
| 新增命令/对外入口 | `app/cli.py` | `prediction_system.py` 仅保留兼容 |
| 新增编排任务 | `harness/football.py` | 先定义 stage，再接 handler |
| 调整预测主链 | `enhanced_prediction_workflow.py`、`domain/*` | 优先改对应 service，而不是把逻辑再塞回主文件 |
| 调整 RAG 记忆层 | `domain/rag.py`、`runtime/rag_store.py`、`domain/postprocess.py` | 同时关注索引、召回结果和解释文本 |
| 调整采集来源 | `collectors/*`、必要时 `okooo_*` | 旧脚本仍可能是实际抓取入口 |
| 调整写回/归档 | `domain/writeback.py`、`domain/persistence.py`、`storage/*` | 注意 SoT 与 archive 一致性 |
| 调整 persona/runtime | `agents/*.md`、`agent_runtime_registry.py` | 输出会影响 CLI / Harness / archive |

---

## 9. 本次验证结论

2026-05-08 本轮完成后，已验证以下链路可运行：
- `python3 prediction_system.py list-leagues --json`
- `python3 prediction_system.py harness-list --json`
- `python3 prediction_system.py health-check --json`
- `python3 prediction_system.py predict-match --json`
- `python3 prediction_system.py auto-sync-results --json`
- `python3 prediction_system.py accuracy --refresh --json`
- `python3 prediction_system.py rag-rebuild --json`
- `python3 prediction_system.py rag-diagnose --json`
- `python3 prediction_system.py sync-memory-rag --dry-run --json`
- `python3 prediction_system.py predict-match --league champions_league --home-team 阿森纳 --away-team 马竞 --date 2026-05-08 --match-id 1324472 --no-refresh-odds --json`
- `python3 prediction_system.py predict-match --league conference_league --home-team 西甲巴列卡 --away-team 斯特堡 --date 2026-05-08 --match-id 1324700 --no-refresh-odds --json`
- `python3 prediction_system.py harness-run --pipeline match_prediction --league champions_league --home-team 阿森纳 --away-team 马竞 --date 2026-05-08 --match-id 1324472 --json`

本轮额外修复：
- `predict-match --no-refresh-odds` 现在会同时跳过大小球补抓，避免在“禁用刷新”场景下仍然等待澳客抓取超时
- `poisson_analysis.py` 已从非法 Markdown 文本整理为可编译的说明脚本，整仓 Python 语法检查可通过
- 已完成一次基于本地快照的 RAG 端到端验证：新预测可生成 `retrieved_memory_explanation`，并原生写入 `MEMORY.md`
