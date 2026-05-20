---
name: "update-five-leagues-schedules"
description: "用 500 轮次接口更新五大联赛 `teams_2025-26.md` 赛程段落，并与当前 SoT、CLI 主链和结果闭环边界保持一致。需要批量刷新五大联赛赛程/比分（并避免未完赛 0-0 误导）时调用。"
---

# Update Five Leagues Schedules

本 Skill 用于把“五大联赛赛程更新”封装为一套可重复执行的 SoT 维护流程。

## 作用边界

它服务于五大联赛 SoT：

- `premier_league/teams_2025-26.md`
- `la_liga/teams_2025-26.md`
- `bundesliga/teams_2025-26.md`
- `serie_a/teams_2025-26.md`
- `ligue_1/teams_2025-26.md`

它不会直接负责：

- 预测推理
- archive 迁移
- runtime-only 比赛写回
- `MEMORY.md` 维护

## 与当前架构的关系

- 对五大联赛而言，`teams_2025-26.md` 是赛程、比分与预测备注的 SoT
- 下游消费方包括：`collect-data`、`predict-match`、`predict-schedule`、`save-result`、`accuracy`
- 赛程 SoT 更新后，正式 CLI 和结果闭环会继续以这些文件为准
- 欧战 / 杯赛不属于本 Skill 的 SoT 写回目标

## 更新原则

- 数据源：`liansai.500.com` 轮次接口
- 修改范围：仅修改 `teams_2025-26.md` 的 `## 赛程信息` 段落
- 只有已结束比赛才写真实比分
- 未结束 / 未开赛一律写 `比分 = '-'`
- 不把更新结果写到旧模板目录或 runtime archive

## 适用场景

- 赛程或比分需要批量刷新
- 生成预测报告前希望先确保五大联赛赛程段落正确
- 某个联赛赛程出现错时、漏轮次或比分误写，需要重刷 SoT

## 一键执行

### 批量刷新五大联赛

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 update_five_leagues_schedule_times.py
```

### 单个联赛刷新

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 update_five_leagues_schedule_times.py --league la_liga
```

## 与正式主链的衔接

更新完成后，下游仍通过正式 CLI 消费这些 SoT：

- `prediction_system.py collect-data`
- `prediction_system.py predict-match`
- `prediction_system.py predict-schedule`
- `prediction_system.py save-result`
- `prediction_system.py accuracy --refresh`

如果用户真正要做的是“赛后批量回填”或“清理 runtime 污染”，应优先考虑：

- `bulk_fetch_and_update.py`
- `prediction_system.py auto-sync-results`
- `prediction_system.py sync-pending-results-review`
- `prediction_system.py purge-nonreal-data`

## 数据净化相关命令

如果发现 runtime 里存在历史演示数据、错日期赛程或污染缓存，可先预览：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py purge-nonreal-data --json
```

确认后正式执行：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py purge-nonreal-data --yes --json
```

## 输出文件

本 Skill 直接更新的目标文件只有五大联赛 SoT：

- `premier_league/teams_2025-26.md`
- `la_liga/teams_2025-26.md`
- `bundesliga/teams_2025-26.md`
- `serie_a/teams_2025-26.md`
- `ligue_1/teams_2025-26.md`

## 常见问题

- 若某个联赛更新失败，通常是 stageId 推导失败或 500 端数据异常
- 若希望补更早轮次，需要扩展脚本的起始轮次策略
- 若需求其实是“补录赛后比分”，不要误用本 Skill 去代替结果同步主链

## 最终规则

这个 Skill 的职责是维护五大联赛赛程 SoT；如果描述与 `europe_leagues/README.md`、`README_使用指南.md`、`app/cli.py` 的当前实现冲突，以当前代码实现为准。
