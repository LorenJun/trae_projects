---
title: "Skill 生命周期治理"
last_updated_date: "2026-05-14"
---

# Skill 生命周期治理

本文档用于约束当前仓库中的 Skill 如何被编写、安装、更新、同步与治理。

核心原则只有一条：

- 不要把运行时治理塞进 `SKILL.md` 正文里

换句话说：

- `SKILL.md` 只负责描述能力与执行流程
- `README.md` 负责给人类维护者解释安装、更新与使用方式
- `references/`、`examples/` 负责沉淀背景资料与范例
- `scripts/` 负责承载确定性格式转换、校验与辅助逻辑
- CLI / Hook / 启动脚本负责安装、更新、同步与版本治理
- Harness 负责在 Agent 读取 Skill 之前准备环境

## 为什么不能把更新逻辑写进 SKILL.md

如果把“检查新版”“访问远端 manifest”“提示用户更新”写进每个 `SKILL.md`，会带来以下问题：

- 每次触发 Skill 都要额外消耗上下文与 Token
- 更新检查会打断正在进行的真实任务
- 相同逻辑会在多个 Skill 中重复出现
- 失败处理、网络策略与权限策略会变得不一致
- 一旦安装器或发布方式调整，需要批量修改所有 Skill

因此，Skill 的自动更新不是 Prompt 问题，而是分发与运行时治理问题。

## 当前仓库的分层约定

当前仓库中的 Skill 目录位于：

- `.trae/skills/<skill-name>/SKILL.md`

当前项目统一约定如下：

| 关注点 | 应放位置 | 说明 |
| --- | --- | --- |
| 何时应选择某个 Skill | `description` | 这是 Agent 的选择入口 |
| 如何完成任务 | `SKILL.md` | 这是能力正文 |
| 补充背景、术语、规则 | `references/` 或项目文档 | 按需加载，避免污染主正文 |
| 确定性辅助逻辑 | `scripts/` | 避免把脚本逻辑写成自然语言 |
| 安装与更新 | CLI / Hook / 启动脚本 | 必须发生在 Context 外 |
| 会话开始前同步本地副本 | Harness / SessionStart Hook | Agent 读 Skill 之前完成 |
| 仓库内技能文档批量同步 | 仓库维护脚本 | 仅维护仓库，不直接参与运行时推理 |

## 对当前项目的明确要求

### 1. SKILL.md 保持轻量

当前仓库中所有业务 Skill 都应遵守：

- 不在 `SKILL.md` 中写“每次执行前先检查远端版本”
- 不在 `SKILL.md` 中写“如发现新版请先自我更新”
- 不在 `SKILL.md` 中内嵌安装器、发布器或回滚器逻辑
- 默认假设当前本地 Skill 已经由运行环境准备为可用版本

### 2. README 负责面向人类说明

如果某个 Skill 需要额外说明：

- 解决什么问题
- 什么时候应该使用
- 如何安装
- 如何更新
- 是否有自动同步策略

这些信息优先写在面向维护者或使用者的 README / 项目规范文档中，而不是堆进 `SKILL.md` 的执行正文。

### 3. 更新应发生在读取之前

推荐的更新时机不是“任务进行中”，而是：

- 新会话启动前
- 启动 Agent 之前
- 仓库文档刷新阶段
- CI / 维护流程阶段

这样可以保证：

- Agent 读取到的已经是最新副本
- 不污染当前任务的推理上下文
- 不在用户任务中途插入版本交互

## 推荐实践

### 1. 个人环境

如果未来引入统一的 Skill 安装器，可优先采用 SessionStart Hook：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "npx skills update -g -y 2>/dev/null"
      }
    ]
  }
}
```

仓库内已提供可直接复用的示例模板：

- [claude_sessionstart_hooks.json](file:///Users/bytedance/trae_projects/docs/examples/claude_sessionstart_hooks.json)

注意：

- 这属于 Harness / Hook 层行为，不属于 `SKILL.md`
- 如果 Hook 会在 `resume`、`clear`、`compact` 触发，应额外检查 source，避免无意义重复更新

对当前项目的推荐接法：

- Hermes / Claude 读取项目 Skill 前，先在运行器侧加载上述 Hook 配置
- Hook 仅负责同步本地 Skill 副本，不直接改项目代码或运行预测命令
- 如果后续要叠加 `health-check`、`refresh_repo_docs.py` 等动作，应拆成独立命令并控制超时，避免把会话启动变慢

### 2. 无 Hook 环境

如果运行环境没有 SessionStart Hook，可采用启动包装脚本：

```bash
#!/usr/bin/env bash
set -euo pipefail

npx skills update -g -y >/tmp/skills-update.log 2>&1 || true
exec claude "$@"
```

同样，这属于启动器治理，不属于 Skill 正文。

### 3. 团队环境

如果 Skill 目录中包含 `scripts/` 或可执行代码，必须按小型软件包治理，而不是按 Markdown 文档治理。至少应考虑：

- 只允许可信仓库来源
- 允许 pin 到 commit 或 tag
- 更新前执行最小测试
- 保留回滚路径
- 区分个人 Skill 与团队 Skill

## 当前项目里的对应关系

这套原则落到当前仓库后，应这样理解：

- `.trae/skills/*/SKILL.md` 只描述能力边界、入口、步骤与输出要求
- `README.md`、`agent.md`、`docs/standards/*` 承载维护规范
- `europe_leagues/scripts/refresh_repo_docs.py` 属于仓库维护脚本，只负责仓库内文档与 Skill 内容同步，不负责运行时自动更新本地安装副本
- Hermes 或其他接入方负责在读取 Skill 之前准备运行环境，而不是要求 Skill 自检生命周期

## 反模式

以下做法属于当前项目明确不推荐的反模式：

- 在每个 Skill 里重复写版本检查 prompt
- 在任务执行中途提示用户“是否要先更新 Skill”
- 把远端版本 manifest 比较逻辑塞进 `SKILL.md`
- 把安装、鉴权、回滚、重试写成 Skill 自己的责任

## 一句话原则

一个好的 Skill 应该假设自己已经是当前环境中可用的最佳版本，然后专心完成当前任务。
