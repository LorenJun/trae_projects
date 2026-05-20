---
title: 足球预测临场数据更新与滚动记忆管理
description: 将临场数据（首发、伤停、赔率变化）接入当前 `europe_leagues` 正式预测链，并在需要时更新 SoT / MEMORY / runtime 相关产物。默认采用 CLI-first 方式。 
triggers:
  - 临场更新
  - 首发阵容
  - 赔率变化
  - 更新滚动记忆
  - live-update
  - 临场分析
name: football-prediction-live-update
---

# 足球预测临场数据更新与滚动记忆管理

本 Skill 定义了如何把临场数据并入当前正式预测主链，而不是临时拼接一套独立流程。

## 入口规则

- 发现 / 兼容入口：`europe_leagues/prediction_system.py`
- 真实命令实现：`europe_leagues/app/cli.py`

默认使用 CLI-first；不要把直接改 `MEMORY.md` 或直接 import 底层类当成主路径。

## 核心目标

- 获取首发、伤停、赔率变化等临场信息
- 重新评估胜平负、比分、大小球与风险提示
- 在需要时用新结论覆盖原预测，而不是重复追加
- 让临场更新仍然落在正式持久化与结果闭环规则内

## 核心原则

1. 每场比赛只有一个最终有效预测视图
2. 临场更新应覆盖旧预测，而不是制造重复记录
3. 必须写清“调整说明”，解释为何临场结论变化
4. 若进入正式落盘，仍应遵守 SoT-backed / runtime-only 边界

## 当前访问与盘口口径

- 临场赔率刷新仍走正式快照链，不要临时拼裸请求
- 默认快照 driver：`local-chrome`
- 默认请求特征：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 当前公共设备池：`100` 组随机 `iPhone Safari` profile
- 欧赔优先解析 `multi_company_consensus`
- 大小球真实盘口优先来自 `handicap.php -> 大小球 tab`

## 推荐流程

### 1. 先采集上下文

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league la_liga --date 2026-05-15 --json
```

### 2. 重新执行正式预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match \
  --league la_liga \
  --home-team 赫罗纳 \
  --away-team 皇家社会 \
  --date 2026-05-15 \
  --json
```

### 3. 需要阶段化审计时用 Harness

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py harness-run \
  --pipeline match_prediction \
  --league la_liga \
  --home-team 赫罗纳 \
  --away-team 皇家社会 \
  --date 2026-05-15 \
  --json
```

## 临场更新时应重点检查

- 首发是否改变球队强弱结构
- 伤停变化是否影响关键位置
- 欧赔 / 亚盘 / 大小球是否出现同向强化或反向走弱
- `market_snapshot.欧赔.company_mode` 是否为 `multi_company_consensus`
- `market_snapshot.欧赔.companies` 是否为空
- `retrieved_memory_explanation` 与历史盘路是否支持临场修正
- `live_betting_advice` 是否因临场信息发生变化

## 更新后的正式落盘边界

### SoT-backed

以下 competition 更新后仍写 SoT：

- `premier_league`
- `la_liga`
- `serie_a`
- `bundesliga`
- `ligue_1`
- `world_cup`

### runtime-only

以下 competition 更新后仍写 `MEMORY.md` 与 runtime archive：

- `europa_league`
- `champions_league`
- `conference_league`
- 其他杯赛 / 欧战扩展比赛

## 调整说明建议

临场更新后，结论里至少要能回答：

- 原预测是什么
- 新预测是什么
- 哪些临场信息改变了判断
- 是方向改变、信心改变，还是比分排序改变

## 调试边界

默认不要：

- 直接正则替换 `MEMORY.md`
- 直接在文档里硬拼临场条目
- 假设存在某个固定的辅助脚本路径

这些方式容易绕开正式持久化与结果闭环。

## 什么时候才做底层调试

只有在 CLI 输出和预期不一致，需要排查内部行为时，才临时使用底层 Python 调试。

即便如此，也应把 CLI 输出视为正式契约，把底层 import 视为开发手段。

## 最终规则

如果 skill 说明与 `europe_leagues/README.md`、`README_使用指南.md`、`app/cli.py`、`domain/persistence.py` 的当前实现冲突，以当前代码实现为准。
