# 足球预测系统架构优化路线图（分阶段设计）

## 1. 文档目标

基于当前仓库真实代码结构，给出一份可执行的架构优化路线图，按 **短期可落地 / 中期重构 / 长期演进** 三个阶段组织，重点解决以下问题：

- 入口层职责过重，CLI 与 Harness 编排边界不清
- 领域核心对象大量依赖 `Dict[str, Any]`，隐式协议过多
- `inference` / `intelligence` / `persistence` / `result_manager` 职责耦合偏深
- Markdown 既承担展示职责，又承担机器 SoT 职责，格式脆弱
- 写回后的副作用链条隐式触发，排障成本高
- 结果统计、RAG 样本、运行时归档尚未完全建立稳定契约

本文不追求“一次性大重构”，而是强调：

1. 先稳住主链
2. 再收敛边界
3. 最后做能力平台化

---

## 2. 当前架构问题总览

### 2.1 入口层问题

当前 `europe_leagues/app/cli.py` 同时承担：

- 子命令注册
- 参数解析
- JSON 输出包装
- runtime profile 注入
- 应用流程编排
- 持久化触发
- Harness 路由补充

这导致 CLI 文件逐步演化为“超大控制器”，对新增命令、复用应用能力、做命令级测试都不友好。

### 2.2 领域层问题

当前核心主链分布在：

- `domain/inference.py`
- `domain/intelligence.py`
- `domain/persistence.py`
- `domain/writeback.py`
- `domain/rag.py`
- `result_manager.py`

问题不是模块数量多，而是边界存在交叉：

- 推断阶段和情报阶段都可能影响最终概率语义
- persistence 不只负责存储，还触发样本同步与索引更新
- result manager 既管 archive，又管 accuracy，又管 migration，又管 memory 回填
- writeback 同时处理业务格式拼接与具体介质写入

这会导致后期调整某个字段时，需要跨多个文件一起验证。

### 2.3 数据契约问题

当前大量核心对象依赖字典透传，例如预测结果、大小球结果、爆冷分析、RAG 召回摘要、归档记录等。这种设计前期迭代快，但中后期会带来：

- 字段名漂移难发现
- 不同模块对同一字段语义理解不一致
- 写回和统计链路容易“静默错误”
- 回归测试难写，只能靠端到端观察结果

### 2.4 存储边界问题

当前双路径写回设计本身是合理的：

- 五大联赛：`teams_2025-26.md`
- 欧战 / 杯赛：`MEMORY.md` + runtime archive

但问题在于 Markdown 同时扮演：

- 人类阅读产物
- 机器事实源
- 增量写回目标
- 统计输入来源

这意味着格式变化会直接冲击解析、更新、统计与回填。

### 2.5 副作用问题

当前写入预测记忆后，还会继续：

- 同步 prediction memory samples
- 同步 RAG index

功能上没有问题，但触发链比较隐式。后续如果继续增加 accuracy refresh、review build、cache invalidate 等动作，调试会越来越难。

---

## 3. 总体优化原则

整个路线图建议遵守 5 个原则：

### 原则 1：先保主链稳定，再动结构
优先保护以下正式主链：

`collect-data -> predict-match / predict-schedule -> writeback -> save-result / auto-sync -> accuracy --refresh`

任何重构都不应先打断这条链。

### 原则 2：先收敛契约，再抽象实现
不要一上来抽很多 service / repository / adapter；先把“输入输出长什么样”固定下来。

### 原则 3：展示层与事实层分离
Markdown 可以继续保留，但应逐步降级为导出视图，而不是唯一事实源。

### 原则 4：副作用显式化
写入、索引、统计、复盘应尽量通过显式事件或显式 pipeline 串联，而不是函数内部顺手触发。

### 原则 5：兼容迁移、分批替换
不追求一次性替换全部老逻辑，优先做“新路径先接入，旧路径逐步退役”。

---

## 4. 短期可落地（1~2 周）

短期目标：**不大改核心算法，优先降低维护成本与回归风险。**

### 4.1 拆分 CLI 入口层

#### 当前问题
`app/cli.py` 过大，编排逻辑与命令注册耦合。

#### 设计建议
将 CLI 拆成：

```text
app/
  cli.py                # 仅保留 parser 装配 + 顶层 dispatch
  commands/
    predict.py          # predict-match / predict-schedule / predict-match-lite
    result.py           # save-result / auto-sync-results / accuracy / pending-results
    rag.py              # rag-rebuild / rag-diagnose / sync-memory-rag
    harness.py          # harness-list / harness-run
    env.py              # health-check / setup-openclaw / list-leagues
  presentation/
    json_result.py      # build_json_result 等统一输出适配
```

#### 实施方式
- 第一步：先只做文件拆分，不改命令参数和输出格式
- 第二步：把 `run_xxx` 函数迁出原文件
- 第三步：保留原有 `main()` 路由逻辑，确保 CLI 行为不变

#### 预期收益
- CLI 入口更清晰
- 命令级测试更容易写
- 后续接 web/API/agent 时更容易复用应用层

---

### 4.2 定义核心预测结果 Schema

#### 当前问题
预测链路各层广泛依赖 `Dict[str, Any]`，字段协议隐式。

#### 设计建议
先不要求全量替换，只先定义最核心的 5 个对象：

```python
PredictionResult
OutcomeProbabilities
OverUnderResult
UpsetAnalysis
RetrievedMemorySummary
```

建议字段示意：

```python
@dataclass
class OutcomeProbabilities:
    home_win: float
    draw: float
    away_win: float

@dataclass
class OverUnderResult:
    available: bool
    line: float | None
    over: float | None
    under: float | None
    line_source: str | None
    market_final: dict[str, Any] | None = None

@dataclass
class PredictionResult:
    league_code: str
    match_date: str
    home_team: str
    away_team: str
    match_id: str | None
    prediction: str
    confidence: float
    final_probabilities: OutcomeProbabilities
    top_scores: list[Any]
    over_under: OverUnderResult | None
    upset: "UpsetAnalysis | None" = None
    retrieved_memory: "RetrievedMemorySummary | None" = None
    runtime_profile: str | None = None
```

#### 实施方式
- 先新增 `domain/contracts.py` 或 `domain/schema.py`
- 先在 CLI 输出前做一次 schema 封装
- writeback / persistence / result manager 先从对象读取字段，但仍兼容旧 dict 输入

#### 预期收益
- 先把“正式字段”定义下来
- 为后续测试、迁移和存储分层打基础

---

### 4.3 给 ResultManager 做第一轮瘦身

#### 当前问题
`result_manager.py` 是明显的超级管理器。

#### 设计建议
短期不重写逻辑，只把职责先拆到文件级：

```text
result/
  manager.py              # 保留对外 facade
  archive_store.py        # prediction_archive 读写
  accuracy_service.py     # 准确率统计
  result_sync_service.py  # 赛果同步与回填
  migration_service.py    # archive migration / backfill
```

#### 实施方式
- 先从纯工具型函数开始迁出
- `ResultManager` 先作为 façade 保持旧接口
- 内部改为组合新服务

#### 预期收益
- 不破坏现有调用方
- 降低后续继续膨胀的风险

---

### 4.4 为 Markdown 写回补契约测试

#### 当前问题
`domain/writeback.py` 的表格匹配和 note 拼接逻辑，最容易在重构时无声损坏。

#### 设计建议
短期先补 4 类测试：

1. 未完赛行写回测试
2. 已完赛行跳过测试
3. 已有预测片段替换测试
4. MatchID / 大小球 / 爆冷字段快照测试

#### 预期收益
- 后续拆 schema、拆 formatter 时不容易把输出打坏

---

### 4.5 把“正式字段 vs 运行时字段”标记清楚

#### 当前问题
有些字段用于展示，有些字段用于统计，有些字段只是中间态；现在边界不够显式。

#### 设计建议
在 schema 层先明确：

- canonical fields：允许进入写回、archive、accuracy
- transient fields：只用于调试、解释、局部增强

例如：

| 字段 | 类型 |
|---|---|
| `prediction` | canonical |
| `final_probabilities` | canonical |
| `top_scores` | canonical |
| `over_under.line_source` | canonical |
| `retrieved_memory.raw_candidates` | transient |
| `debug_signals` | transient |
| `intermediate_weights` | transient |

#### 预期收益
- 后续做 archive / accuracy 时不会混入实验字段

---

## 5. 中期重构（2~6 周）

中期目标：**把当前“脚本式主链”提升为“稳定应用层 + 清晰领域边界”。**

### 5.1 引入 Application Service 层

#### 当前问题
CLI、Harness 都在直接编排领域服务，流程逻辑分散。

#### 设计建议
新增应用层 use case：

```text
application/
  predict_match.py
  predict_schedule.py
  collect_match_context.py
  save_match_result.py
  refresh_accuracy.py
  rebuild_rag.py
```

每个 use case 负责：

- 接收标准 input DTO
- 调用领域服务
- 调用持久化服务
- 返回标准 output DTO

CLI 与 Harness 都只调用 application 层。

#### 预期收益
- 消除“双入口双编排”问题
- 后续接 agent / API / batch 复用成本更低

---

### 5.2 明确 inference 与 intelligence 的边界

#### 当前问题
两者都在碰“最终概率的形成”。

#### 设计建议
重新定义职责：

- `intelligence`：生成情报上下文、修正建议、解释信息，不直接落最终输出
- `inference`：唯一负责产出最终预测结构

建议契约：

```python
class MatchIntelligenceReport:
    signals: list[...]
    quant_adjustment: ...
    scenario_tags: list[str]
    explanation: ...

class InferencePipelineService:
    def run(..., intelligence: MatchIntelligenceReport | None) -> PredictionResult:
        ...
```

#### 预期收益
- 避免重复修正同一信号
- 推断链更容易解释和测试

---

### 5.3 把 writeback 拆成 formatter + repository

#### 当前问题
目前写回逻辑把“生成文本”和“写文件”绑在一起。

#### 设计建议
拆成：

```text
domain/writeback/
  formatter.py        # PredictionNoteFormatter
  teams_repository.py # TeamsMarkdownRepository
  gateway.py          # TeamsWritebackGateway
```

其中：

- formatter：只负责把 `PredictionResult` 转为 note 文本
- repository：只负责读取/匹配/更新 markdown 表格
- gateway：对外保留原有写回入口

#### 预期收益
- 后续要新增 json/export/html 都更容易
- 文本格式调整不会影响底层匹配逻辑

---

### 5.4 把 PredictionPersistenceService 改成“显式存储 + 显式后处理”

#### 当前问题
现在 update memory 后顺手做样本同步和索引更新。

#### 设计建议
拆成两步：

1. `PredictionMemoryRepository.upsert(record)`
2. `PostPersistPipeline.handle(PredictionPersistedEvent)`

事件初期不必上完整消息系统，先用进程内事件对象即可：

```python
@dataclass
class PredictionPersistedEvent:
    record_id: str
    storage_mode: str
    league_code: str
    match_id: str | None
```

订阅者包括：

- sample sync handler
- rag index sync handler
- optional accuracy refresh handler

#### 预期收益
- 副作用链可观测
- 出问题时更容易定位在哪个后处理步骤失败

---

### 5.5 结构化存储前置，Markdown 变导出层

#### 当前问题
Markdown 直接承担机器 SoT，太脆弱。

#### 设计建议
中期优先引入轻量结构化层，不必直接上数据库，可先用：

- `runtime/predictions/*.json`
- 或单一 `sqlite`

推荐优先选 `sqlite`，原因：

- 查询方便
- 幂等更新方便
- 结构比 JSON 文件更稳
- 后续统计和 review 更适合

建议存以下核心表：

- `predictions`
- `prediction_markets`
- `prediction_scores`
- `prediction_memory_links`
- `results`

Markdown 保留为：

- 五大联赛展示导出
- 人工审阅入口
- 回溯辅助文本

#### 预期收益
- 写回、复盘、统计、RAG 召回都有统一结构源
- 文本格式变化不再影响机器事实层

---

### 5.6 为 RAG 层建立 Provider 接口

#### 当前问题
RAG 逻辑较深嵌在预测主链中。

#### 设计建议
定义标准接口：

```python
class MatchMemoryRetriever(Protocol):
    def retrieve_similar_cases(...) -> list[...]: ...
    def retrieve_market_cases(...) -> list[...]: ...
    def retrieve_upset_cases(...) -> list[...]: ...
    def summarize(...) -> RetrievedMemorySummary: ...
```

当前 `HybridRAGService` 作为默认实现。

#### 预期收益
- 以后可替换索引策略、关闭部分召回、按联赛定制召回
- inference 不必理解 RAG 内部细节

---

## 6. 长期演进（1~3 个月）

长期目标：**把当前项目演进成稳定的预测能力平台，而不只是 CLI 工具集合。**

### 6.1 形成统一能力内核（Core Prediction Platform）

长期建议把项目分为四层：

```text
interfaces/
  cli/
  harness/
  api/

application/
  use_cases/

domain/
  inference/
  intelligence/
  rag/
  persistence/
  result/

infrastructure/
  storage/
  collectors/
  indexing/
  exporters/
```

这会让“入口变化”不再影响核心逻辑。

---

### 6.2 建立统一事件总线与任务编排

#### 当前问题
随着能力增多，写回、同步、统计、复盘会越来越像任务流。

#### 设计建议
长期把关键动作事件化：

- `MatchCollected`
- `PredictionGenerated`
- `PredictionPersisted`
- `ResultSaved`
- `AccuracyRefreshed`
- `RagIndexRebuilt`

早期可以用进程内 dispatcher，后期如果需要批量调度，再接外部队列。

#### 预期收益
- 自动化编排能力更强
- Harness 能力能自然升级成真正 pipeline runtime

---

### 6.3 构建统一审计视图与复盘视图

长期不应只输出 CLI JSON 和 Markdown，而应建立统一审计数据模型：

- 本场使用了哪些输入源
- 哪些市场信号生效
- 哪些 intelligence 信号改变了最终概率
- 哪些 RAG 样本真正命中方向
- 赛后哪些模块贡献最大 / 偏差最大

这会让系统真正具备“可解释优化”能力，而不是只能看最终对错。

---

### 6.4 引入版本化模型契约与特征审计

随着规则越来越多，需要能回答：

- 某一场比赛是在哪个规则版本下跑出来的
- 某条大小球结论是受哪个 line_source 约束产生的
- 某个 RAG 策略上线后，准确率是否真的提升

建议长期增加：

- `prediction_version`
- `feature_flags_snapshot`
- `inference_policy_version`
- `rag_policy_version`

这不是为了做复杂平台，而是为了避免“改了很多，但不知道哪条规则带来影响”。

---

### 6.5 为多入口消费做好准备

长期看，这套系统已经不只适合 CLI：

- 可以接 agent runtime
- 可以接 web service
- 可以接定时批量调度
- 可以接 review dashboard

因此建议长期把接口层视为可插拔：

- CLI 是人工入口
- Harness 是编排入口
- API 是服务入口
- Exporter 是报告入口

核心预测逻辑不应依赖其中任何一个。

---

## 7. 推荐实施顺序

建议按下面顺序推进，而不是并行大拆：

### 第一阶段（短期）
1. 拆 CLI 文件
2. 定义核心 schema
3. 给 writeback 补测试
4. ResultManager 文件级瘦身

### 第二阶段（中期前半）
5. 建 application layer
6. 让 CLI / Harness 都走 use case
7. inference / intelligence 重新约定输入输出

### 第三阶段（中期后半）
8. writeback 拆 formatter/repository
9. persistence 改事件后处理
10. 引入结构化存储（优先 sqlite）

### 第四阶段（长期）
11. RAG provider 化
12. 统一事件总线
13. 审计与复盘模型平台化

---

## 8. 风险与注意事项

### 8.1 不要一上来替换全部 Dict
应先从正式输出对象开始替换，不要一次把所有中间态都强类型化，否则成本过高。

### 8.2 不要先移除 Markdown
Markdown 当前仍然是用户可见 SoT 的一部分，短中期应该保留，只是逐步把它降成导出层。

### 8.3 不要在重构时动算法口径
架构重构和算法调优尽量分开，否则无法判断回归来源。

### 8.4 先测写回，再测统计，再测 RAG
因为最脆弱的是文本事实源，其次是统计一致性，最后才是检索策略。

---

## 9. 最终建议（结论）

如果只选最值得优先做的 4 件事，我建议是：

1. **拆 `app/cli.py`**：先把入口层维护成本压下来
2. **定义 `PredictionResult` 等核心 schema**：先收敛正式字段契约
3. **拆 `ResultManager`**：先阻止超级管理器继续膨胀
4. **让结构化存储前置、Markdown 后置**：为后续统计、RAG、复盘打稳定基础

这 4 件事完成后，项目会从“复杂但还能跑的 CLI 系统”，进入“具备稳定演进能力的预测平台”阶段。

---

## 10. 一页版摘要

### 短期可落地
- 拆 CLI
- 建核心 schema
- 补 writeback 契约测试
- 瘦身 ResultManager
- 区分 canonical / transient fields

### 中期重构
- 建 application layer
- 统一 CLI / Harness 编排路径
- 明确 inference / intelligence 边界
- writeback 拆 formatter + repository
- persistence 改显式后处理
- 引入结构化存储
- RAG provider 化

### 长期演进
- 形成统一 prediction platform
- 事件总线化
- 审计 / 复盘模型平台化
- 模型与策略版本化
- 支撑 CLI / Harness / API / Exporter 多入口
