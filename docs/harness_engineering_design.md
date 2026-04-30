# Harness Engineering 化设计说明

## 目标

本次改造不是重写预测算法，而是在现有足球预测系统外侧增加一层 `Harness`。

这层 Harness 负责四件事：

1. 统一上下文输入
2. 把任务拆成明确阶段
3. 记录阶段产物与失败点
4. 让旧脚本能力以可编排方式复用

这样做之后，项目从“脚本集合”向“可治理的执行系统”演进。

## 为什么适合当前项目

当前项目已经具备较强的领域能力：

- `data_collector.py` 负责数据采集
- `enhanced_prediction_workflow.py` 负责预测核心
- `result_manager.py` 负责赛果写回与准确率统计
- `prediction_system.py` 负责 CLI 入口

问题不在于能力缺失，而在于这些能力的组合方式仍偏直接调用：

- 上下文以命令参数临时拼装
- 多步任务缺少统一阶段边界
- 阶段产物没有标准化沉淀
- 审计信息分散在 stdout、日志和返回值中

Harness Engineering 的价值就是把这些“能力之间的连接层”显式化。

## 新增结构

新增目录：

```text
europe_leagues/
└── harness/
    ├── __init__.py
    ├── core.py
    └── football.py
```

职责划分：

- `harness/core.py`
  - 定义 `HarnessContext`
  - 定义 `PipelineStage`
  - 定义 `HarnessPipeline`
  - 负责阶段执行、失败捕获、审计记录

- `harness/football.py`
  - 负责把足球领域能力注册为 Pipeline
  - 目前提供：
    - `match_prediction`
    - `result_recording`

- `prediction_system.py`
  - 新增：
    - `harness-list`
    - `harness-run`
  - 作为 Harness 入口，而不是直接替代旧入口

## 设计原则

### 1. Context First

所有 Pipeline 都先接收统一 `inputs`，再把阶段产物写入 `artifacts`。

这意味着：

- 用户输入是显式的
- 中间阶段输出是显式的
- 后续阶段读取上下文有统一方式

例如 `match_prediction` 中：

- 输入包含 `league`、`date`、`home_team`、`away_team`
- `collect_data` 产出比赛列表
- `predict_match` 可复用 `collect_data` 的 `match_id` / `match_time`

### 2. Stage Boundaries

每个阶段只做一件事：

- `collect_data`
- `predict_match`
- `save_result`
- `refresh_accuracy`

这样后面可以很自然地继续加：

- `resolve_match_id`
- `inject_team_context`
- `risk_review`
- `publish_report`

### 3. Reuse Before Rewrite

Harness 不重写核心预测逻辑，而是适配现有模块：

- 采集仍调用 `DataCollector`
- 预测仍调用 `EnhancedPredictor`
- 赛果写回仍调用 `ResultManager`

因此业务风险低，迁移成本小。

### 4. Auditability

每次 Harness 执行都会返回：

- `pipeline`
- `inputs`
- `artifacts`
- `stages`
- `error`

这让自动化系统、Agent 或外部调度器可以稳定消费结果，而不是依赖非结构化日志。

## 当前可用命令

### 查看可用 Pipeline

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py harness-list --json
```

### 用 Harness 跑赛前单场预测

```bash
python3 prediction_system.py harness-run \
  --pipeline match_prediction \
  --league la_liga \
  --home-team 巴塞罗那 \
  --away-team 皇家马德里 \
  --date 2026-05-11 \
  --json
```

### 用 Harness 做赛果回填

```bash
python3 prediction_system.py harness-run \
  --pipeline result_recording \
  --match-id la_liga_20260511_巴塞罗那_皇家马德里 \
  --home-score 2 \
  --away-score 1 \
  --refresh \
  --json
```

## 下一步建议

第一版已经建立了 Harness 骨架，后续建议继续演进：

1. 把 `predict-schedule` 也收敛成 Pipeline
2. 增加 `policy` 层，约束哪些 Pipeline 允许写文件
3. 增加 `artifact persistence`，把每次执行落盘到 `.okooo-scraper/runtime/harness_runs/`
4. 增加 `stage metrics`，统计耗时、失败率、降级原因
5. 把 `team_context`、`odds_snapshot`、`result_review` 分别变成独立阶段

## 结论

这次改造的核心不是“换框架”，而是给现有项目补上了一个面向 Agent 与自动化调用的执行骨架。

从工程视角看，项目现在开始具备 Harness Engineering 的几个关键特征：

- 上下文集中
- 能力分阶段暴露
- 执行结果可审计
- 旧系统可渐进迁移
