# 🏆 足球预测系统 - 使用指南

## 📋 目录
1. [系统概览](#系统概览)
2. [快速开始](#快速开始)
3. [模块说明](#模块说明)
4. [工作流程](#工作流程)
5. [使用示例](#使用示例)

---

## 🎯 系统概览

这是一个完整的足球比赛预测和结果追踪系统，包含以下特点：

- **多模型融合**: 使用10种不同的预测模型
- **智能缓存**: 避免重复计算，提升效率
- **结果追踪**: 自动保存预测，对比实际结果
- **准确率统计**: 按联赛、按模型统计准确率
- **爆冷分析**: 基于历史案例分析爆冷可能性
- **预测写回**: 直接写回各联赛 `teams_2025-26.md`（赛程表备注列）

---

## 🚀 快速开始

### 0. 环境安装

推荐先创建虚拟环境并安装系统工具：

```bash
cd /Users/bytedance/trae_projects
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

如果你需要运行真实浏览器采集链路，还需要安装：

```bash
python3 -m pip install -r requirements-openclaw.txt
```

说明：
- 未安装 `browser-use` 时，`data_collector.py` / `prediction_system.py collect-data` 会自动降级到 `mock` 数据模式，并提示让 `openclaw` 自行执行 `python3 -m pip install browser-use`
- 如需飞书相关能力，可在仓库根目录执行 `npm install` 安装 `feishu-cli`

### 缓存说明（默认关闭）

预测流程的 `.prediction_cache` 已默认关闭（不读不写），以优先保证实时最新数据。

如需临时开启本地缓存（仅用于性能优化/离线调试），执行：

```bash
export ENABLE_PREDICTION_CACHE=1
```

推荐让 `openclaw` 直接执行一键初始化脚本：

```bash
cd /Users/bytedance/trae_projects
bash scripts/setup_openclaw_env.sh
```

### 1. 交互式菜单（推荐）
```bash
cd europe_leagues
python3 prediction_system.py
```

### 2. 直接运行增强预测
```bash
python3 enhanced_prediction_workflow.py
```

### 3. 结果管理和准确率统计
```bash
python3 result_manager.py --interactive
python3 result_manager.py --show-accuracy
python3 result_manager.py --update-accuracy
```

### 4. 面向 OpenClaw 的 CLI 命令

```bash
python3 prediction_system.py list-leagues --json
python3 prediction_system.py setup-openclaw --json
python3 prediction_system.py health-check --json
python3 prediction_system.py predict-match --league la_liga --home-team 巴塞罗那 --away-team 皇家马德里 --date 2026-05-11 --json
python3 prediction_system.py predict-schedule --league la_liga --date 2026-05-11 --days 2 --json
python3 prediction_system.py collect-data --league la_liga --date 2026-05-11 --json
python3 prediction_system.py pending-results --days-back 14 --json
python3 prediction_system.py save-result --match-id la_liga_20260511_巴塞罗那_皇家马德里 --home-score 2 --away-score 1 --json
python3 prediction_system.py accuracy --refresh --json
```

说明：
- `--json` 输出适合自动化系统直接解析
- `predict-schedule` / `save-result` 会修改联赛 `teams_2025-26.md`
- `setup-openclaw` 会返回完整初始化命令和依赖状态
- `health-check` 会返回 `openclaw_full_ready` 与缺失依赖列表

---

## Agent 调用提示词

以下提示词可直接提供给 `openclaw`、自动化编排器或调度型 Agent，确保执行时遵循当前项目真实流程。

### 统一入口提示词

```text
你正在操作的是一个“以 europe_leagues/<league>/teams_2025-26.md 为单一事实来源”的足球预测项目。

执行任务时必须遵守：
1. 先判断任务类型：环境检查、数据采集、赛前预测、赛果回填、准确率统计、只读查询。
2. 除只读查询外，优先调用 prediction_system.py 的非交互命令，并附带 --json。
3. 所有正式落盘只允许写入对应联赛的 teams_2025-26.md，不要把新结果写入 predictions/、analysis/predictions/*.md 或其它旧目录。
4. 赛前预测必须按“确认比赛 -> collect-data -> 分析 -> predict-match/predict-schedule -> 写回 teams_2025-26.md”执行。
5. 赛后处理必须按“确认比赛 -> 核验比分 -> save-result -> 必要时 accuracy --refresh”执行。
6. 缺少 browser-use、Playwright 或实时源异常时，可以降级，但必须明确标记为 mock/降级数据。
7. 如果用户只要求查看、解释、调研，默认不写文件。
8. 如果准备创建新的主流程 markdown 文件，视为偏航，应立即停止并改回 teams_2025-26.md。
```

### 偏航纠正提示词

```text
如果你发现自己准备跳过数据采集直接生成预测，或者准备把结果写入旧模板文件，请立即停止并回到标准流程：

- 环境检查：health-check
- 数据采集：collect-data
- 单场预测：predict-match
- 批量预测：predict-schedule
- 赛果回填：save-result
- 准确率统计：accuracy --refresh

查架构时优先阅读 README_使用指南.md、README.md、agent.md、docs/standards/workflow.md、agents/*.md。
```

---

## 📦 模块说明

### 核心模块

| 文件 | 功能 |
|-----|------|
| `prediction_system.py` | 统一入口，菜单系统 |
| `enhanced_prediction_workflow.py` | 增强预测主程序 |
| `result_manager.py` | 结果管理和准确率统计 |
| `ml_prediction_models.py` | 10种预测模型 |
| `optimized_prediction_workflow.py` | 原始预测程序 |

### 数据目录
```
europe_leagues/
├── premier_league/    # 英超
├── serie_a/          # 意甲
├── bundesliga/       # 德甲
├── ligue_1/          # 法甲
├── la_liga/          # 西甲
└── .okooo-scraper/      # 运行时产物（快照/日志/准确率输出）
```

---

## 🔄 完整工作流程

### 1. 预测阶段
1. 系统生成未来几天的比赛预测
2. 将预测结果写回各联赛 `teams_2025-26.md` 的赛程表“备注”列

### 2. 比赛结束后
1. 更新赛程表“比分”列为真实赛果（`x-y`）
2. 可在备注列追加 `✅/❌`（或由脚本统一标注）
3. 系统根据 `teams_2025-26.md` 直接统计准确率

### 3. 准确率统计
1. 系统自动计算整体准确率（直接解析 `teams_2025-26.md`）
2. 按联赛统计准确率
3. 输出到 `europe_leagues/.okooo-scraper/runtime/accuracy_stats.json`

---

## 💻 使用示例

### 示例1：交互式更新比赛结果

```bash
python3 result_manager.py --interactive
```

然后选择：
1. 查看待更新的比赛
2. 输入比赛结果（主队进球、客队进球）
3. 查看准确率统计

### 示例2：生成新预测

```bash
python3 prediction_system.py
```

选择：运行增强版预测系统

### 示例3：查看准确率报告

```bash
python3 -c "from result_manager import ResultManager, print_accuracy_report; manager = ResultManager(); stats = manager.update_accuracy_stats(); print_accuracy_report(stats)"
```

---

## 📊 预测报告说明

写回到 `teams_2025-26.md` 备注列的预测信息包含（取决于预测器版本与数据可用性）：

- 预测结果（主胜/平局/客胜）
- 信心指数
- 概率分布可视化
- 最可能比分（Top 3）
- 大小球分析
- 球队实力对比
- 爆冷风险分析
- 动态调权诊断（如可用）

---

## 🔧 预测模型说明

系统包含10种预测模型：

1. **泊松分布模型** - 基于进球概率
2. **Dixon-Coles模型** - 修正泊松分布
3. **Elo评级系统** - 基于球队等级
4. **Glicko评级系统** - 更精确的评级
5. **逻辑回归模型** - 多特征预测
6. **随机森林模型** - 集成学习
7. **xG模型** - 预期进球
8. **贝叶斯模型** - 概率更新
9. **专家系统** - 规则引擎
10. **集成模型** - 多模型融合

---

## 📈 准确率统计维度

- **整体准确率** - 所有预测
- **按联赛准确率** - 英超/意甲/德甲/法甲/西甲
- **按模型准确率** - 各模型单独统计
- **比分准确率** - 精确比分预测
- **趋势** - 最近7天准确率变化

---

## 💡 最佳实践建议

1. **定期更新结果**: 比赛结束后尽快录入结果
2. **关注高信心预测**: 信心 > 70% 的预测更可靠
3. **注意爆冷警告**: 🟡中风险/🔴高风险需要谨慎
4. **参考多模型**: 结合多个模型的预测进行决策
5. **定期重训**: 有足够历史数据后优化模型权重

---

## 🎉 系统亮点

✅ 完全自动化预测流程
✅ 多维度准确率统计
✅ 智能缓存提升效率
✅ 美观的Markdown报告
✅ 完整的历史数据管理
✅ 交互式操作界面
✅ 支持命令行和菜单两种模式

---

如有问题，请查看代码注释或联系维护者。
