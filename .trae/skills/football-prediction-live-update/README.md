# 足球预测临场数据更新技能

## 简介

本技能定义了如何将临场数据（首发阵容、伤停更新、赔率变化）整合到当前正式预测链路中，并在需要时刷新滚动记忆与预测备注。

当前主路径已经统一为 CLI-first：先 `collect-data`，必要时显式刷新澳客快照，再走 `predict-match` / `harness-run`，而不是优先手动改文档或调用旧脚本。

## 核心原则

1. **覆盖更新**：临场分析后，用新预测**覆盖**原预测行，而非重复添加
2. **标记区分**：使用【临场更新】标记区分初始预测 vs 临场更新
3. **变化说明**：必须包含"调整说明"解释预测变化的逻辑
4. **正式边界**：临场更新属于正式链路补充，SoT 联赛（五大联赛 + 世界杯）完整写回 `teams_2025-26.md` + MEMORY + RAG + 归档；非 SoT 赛事走 `archive_only`，仅归档 + 赛果同步，不写 MEMORY/RAG/teams md

## 快速使用

### 方法1: 正式 CLI 流程

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league premier_league --date 2026-05-24 --json
python3 prediction_system.py predict-match --league premier_league --home-team 伯恩利 --away-team 狼队 --date 2026-05-24 --time 23:00 --json
```

### 方法2: 快照未就绪时显式刷新

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_save_snapshot.py \
    --driver local-chrome \
    --league 英超 \
    --team1 伯恩利 \
    --team2 狼队 \
    --date 2026-05-24 \
    --time 23:00 \
    --overwrite
```

### 方法3: 手动更新

参考 `SKILL.md` 中的标准格式，按比赛类型更新正式写回目标；不要把手动编辑 `MEMORY.md` 视为所有比赛的默认主链。

## 文件结构

```
football-prediction-live-update/
├── SKILL.md              # 技能主文档（详细规范）
├── README.md             # 本文件（快速入门）
└── scripts/
    └── update_memory_live.py  # 兼容/历史辅助脚本，不是当前正式主入口
```

## 触发关键词

当对话中出现以下关键词时，Agent应加载本技能：
- 临场更新
- 首发阵容
- 赔率变化
- 更新滚动记忆
- live-update
- 临场分析

## 相关文档

- `../../docs/standards/workflow.md` - 正式预测、写回与回填流程
- `../../docs/standards/skill_lifecycle.md` - Skill 正文与维护治理边界

## 更新记录

- 2026-05-15: 初始版本，定义临场数据更新标准流程
- 2026-05-24: 同步 CLI-first 临场链路，补充真实盘口快照回流与正式预测重跑示例
