# 爆冷预警功能使用指南

本指南描述当前项目里“爆冷预警”能力如何接入正式预测主链，默认采用 CLI-first 方式。

## 入口原则

当前正式入口分两层：

- `prediction_system.py`：兼容 / 发现入口
- `app/cli.py`：真实 CLI 路由与 JSON 输出实现

因此，爆冷预警的默认使用方式应附着在正式预测链上，而不是把底层 Python import 作为主路径。

## 预警能力在主链中的位置

爆冷预警不是独立脚本产品，而是正式预测链中的一个分析结果。典型链路为：

1. `collect-data` 获取赛程、`match_id` 与上下文
2. `predict-match` / `predict-schedule` 进入正式预测主链
3. `enhanced_prediction_workflow.py`、`domain/inference.py`、`domain/postprocess.py` 等模块协同生成预测结果
4. 输出中携带爆冷相关分析、风险提示与建议

## 当前推荐用法

### 1. 先采集上下文

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league la_liga --date 2026-05-11 --json
```

### 2. 走正式单场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match \
  --league la_liga \
  --home-team 巴塞罗那 \
  --away-team 西班牙人 \
  --date 2026-05-11 \
  --json
```

### 3. 需要阶段化审计时使用 Harness

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py harness-run \
  --pipeline match_prediction \
  --league la_liga \
  --home-team 巴塞罗那 \
  --away-team 西班牙人 \
  --date 2026-05-11 \
  --json
```

## 输出中应该看什么

执行预测后，优先检查这些字段：

- `prediction`
- `confidence`
- `upset_potential`
- `retrieved_memory_explanation`
- `live_betting_advice`
- `runtime_profile`

如果存在爆冷预警，重点查看：

- 风险等级
- 触发因素
- 是否与盘口 / 大小球 / 历史盘路一致
- 最终建议是否只是“防冷提示”，还是已经影响主结论方向

## 预警的实际含义

爆冷预警通常意味着：

- 模型结论与盘口信号存在张力
- 强弱预期与让步深度不完全匹配
- 平局 / 冷门方向需要防范
- 需要把“风险提示”与“主方向建议”拆开表达

因此，正式输出时应明确区分：

- `模型结论`
- `盘口结论`
- `综合结论`

## 与 SoT / runtime 的关系

爆冷预警本身是预测输出的一部分，不单独定义新的持久化路径。

其落盘与后续闭环仍遵循正式主链：

- 五大联赛 / 世界杯：写 SoT-backed markdown
- 欧战 / 杯赛：写 `MEMORY.md` 与 runtime-only 归档
- 赛后结果通过 `save-result` / `auto-sync-results` / `result-sync-daemon` / `sync-pending-results-review` 进入结果闭环

## 什么时候才用底层 Python 调试

默认不要把下面方式当作正式使用说明：

- 直接 `from enhanced_prediction_workflow import EnhancedPredictor`
- 直接 `from enhanced_prediction_workflow import UpsetAnalyzer`

只有在这些场景，才建议做底层调试：

- CLI 输出异常，需要比对底层对象
- 要排查某个具体字段是 inference 生成还是 postprocess 补充
- 需要验证尚未暴露成 CLI 参数的内部行为

即便如此，也应把 CLI 输出当作正式契约，把底层 import 视为开发排查手段。

## 调试示例（仅开发排查）

```bash
cd /Users/bytedance/trae_projects
python3 - <<'PY'
import sys
sys.path.insert(0,'/Users/bytedance/trae_projects/europe_leagues')
from domain.predictor import DomainPredictor

predictor = DomainPredictor()
result = predictor.predict_match(
    home_team='巴塞罗那',
    away_team='西班牙人',
    league_code='la_liga',
    match_date='2026-05-11',
)
print(result.get('upset_potential'))
PY
```

这类方式只用于调试，不作为仓库默认工作流。

## 核心结论

如果你只是想正确使用爆冷预警，请始终优先：

- `prediction_system.py collect-data`
- `prediction_system.py predict-match`
- `prediction_system.py predict-schedule`
- `prediction_system.py harness-run`

如文档与代码冲突，以 `app/cli.py`、`enhanced_prediction_workflow.py`、`domain/persistence.py`、`runtime/result_sync.py` 的当前实现为准。
