#!/usr/bin/env markdown
# europe_leagues Docs Index

> 当前正式流程  
> 1. `prediction_system.py collect-data` 或赛程抓取定位 `match_id`  
> 2. `prediction_system.py predict-match / predict-schedule` 执行增强预测  
> 3. 结果写回 `europe_leagues/<league>/teams_2025-26.md`  
> 4. 赛后用 `prediction_system.py save-result` 或 `bulk_fetch_and_update.py` 回填  
> 5. 最后用 `prediction_system.py accuracy --refresh --json` 刷新胜负 / 比分 / 大小球统计  
> 可审计编排入口：`prediction_system.py harness-run --pipeline ... --json`  
> 关键检查项：`over_under.line`、`line_source`、`over_under.market.final`

## 统一职业身份

- 项目统一身份：`专业纬度足彩数据精算师`
- 身份定义入口：`../../agents/football_actuary_persona.md`
- 执行要求：任何 Agent、CLI 调用方或 Harness 编排都应同时兼顾足球业务分析、赔率与盘口交易分析、概率建模、统计验证与模型评估、风险控制与资金管理、数据工程、流程自动化与策略迭代
- 输出原则：明确区分 `模型结论`、`盘口结论`、`综合结论`
- 风险边界：跨联赛、杯赛、样本不足、快照缺失时必须显式标注风险，不允许单一维度直接输出强确定性结论

## 当前正式流程

- 采集入口：`prediction_system.py collect-data`
- 单场预测：`prediction_system.py predict-match`
- 批量预测：`prediction_system.py predict-schedule`
- 阶段化编排：`prediction_system.py harness-run`
- 单场回填：`prediction_system.py save-result`
- 批量回填：`bulk_fetch_and_update.py`
- 统计刷新：`prediction_system.py accuracy --refresh`
- 正式写回：`europe_leagues/<league>/teams_2025-26.md`

## 核心文档

- `../../agents/football_actuary_persona.md`：统一职业身份、职责边界、输出原则与角色映射
- `README_使用指南.md`：当前正式使用说明，适合直接按命令执行
- `ODDS_FETCH_GUIDE.md`：澳客赛程、快照、大小球与预测衔接说明
- `PRD_足球预测系统_2026.md`：产品视角 PRD 与设计背景
- `workflow.md`：标准工作流与执行约束
- `data_format.md`：主流程数据结构与字段规范

## 标准规范

- `workflow.md`：正式流程、任务分类、推荐入口
- `data_format.md`：`teams_2025-26.md`、赛程 JSON、快照 JSON、预测输出、统计文件格式

## PRD
- `PRD_足球预测系统_2026.md`：飞书文档排版版 PRD（含目录与表格规范）

## Skills
- `football-match-analysis`：基于 `collect-data / 实时快照 / EnhancedPredictor / Harness` 的正式比赛预测 skill
- `okooo-match-finder`：定位 `match_id`、赛程与快照入口的上游 skill
- `update-five-leagues-players`：五大联赛球员数据更新（名单→中文名/号码→统计/热区→审计）
- `update-five-leagues-schedules`：五大联赛赛程更新（500 轮次接口，未完赛比分为 `-`）

## 关键检查点

- 真实大小球优先来自澳客 `handicap.php` 页内 `大小球` tab
- 预测结果重点检查：`over_under.line`、`line_source`、`over_under.market.final`
- 若 `line_source=snapshot_final`，说明真实盘口线已成功进入预测流程
