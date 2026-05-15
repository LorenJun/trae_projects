# 足球预测临场数据更新技能

## 简介

本技能定义了如何将临场数据（首发阵容、伤停更新、赔率变化）整合到预测中，并正确更新 MEMORY.md 滚动记忆的标准流程。

## 核心原则

1. **覆盖更新**：临场分析后，用新预测**覆盖**原预测行，而非重复添加
2. **标记区分**：使用【临场更新】标记区分初始预测 vs 临场更新
3. **变化说明**：必须包含"调整说明"解释预测变化的逻辑
4. **单一来源**：每场比赛在滚动记忆中只有**一个最终预测**

## 快速使用

### 方法1: 使用自动化脚本

```bash
python3 .claude/skills/football-prediction-live-update/scripts/update_memory_live.py \
    --match-id la_liga_20260515_赫罗纳_皇家社会 \
    --odds-change "2.13->1.99" \
    --confidence-boost 12 \
    --reasoning "临场降强信号，亚盘升盘"
```

### 方法2: 手动更新

参考 SKILL.md 中的标准格式，手动编辑 MEMORY.md 文件。

## 文件结构

```
football-prediction-live-update/
├── SKILL.md              # 技能主文档（详细规范）
├── README.md             # 本文件（快速入门）
└── scripts/
    └── update_memory_live.py  # 自动化更新脚本
```

## 触发关键词

当对话中出现以下关键词时，Agent应加载本技能：
- 临场更新
- 首发阵容
- 赔率变化
- 更新滚动记忆
- live-update
- 临场分析

## 相关技能

- `football-prediction-sync-results` - 赛后结果同步与复盘
- `update-five-leagues-standings` - 积分榜更新

## 更新记录

- 2026-05-15: 初始版本，定义临场数据更新标准流程