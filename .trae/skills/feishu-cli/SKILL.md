---
name: "feishu-cli"
description: "飞书开放平台命令行工具，支持 Markdown ↔ 飞书文档双向转换，以及文档、知识库、消息等操作。Invoke when user needs to interact with Feishu (Lark) platform, such as creating documents, sending messages, or managing permissions."
---

# feishu-cli

飞书开放平台命令行工具，将飞书文档、知识库、电子表格、消息、日历、任务等操作封装为简洁的命令行接口。

## 核心功能

- **Markdown ↔ 飞书文档双向无损转换**
- **文档管理**：创建、编辑、删除、复制、导出文档
- **知识库管理**：管理知识库节点和空间
- **消息发送**：发送群聊和私聊消息
- **权限管理**：设置文档和知识库权限
- **表格操作**：操作电子表格和多维表格
- **日历和任务**：管理日历事件和任务

## 安装和配置

1. **安装 feishu-cli**
   ```bash
   npm install -g feishu-cli
   ```

2. **配置飞书应用**
   - 在飞书开放平台创建应用
   - 为应用开通所需权限（可导入提供的权限 JSON）
   - 配置环境变量：
     ```bash
     export FEISHU_APP_ID="your_app_id"
     export FEISHU_APP_SECRET="your_app_secret"
     ```

## 基本用法

### 文档操作
- 创建文档：`feishu doc create --title "文档标题" --content "内容"`
- 上传 Markdown：`feishu doc upload --file README.md --title "README"`
- 下载文档：`feishu doc download --doc-id "doc_id" --output output.md`

### 消息操作
- 发送群聊消息：`feishu msg send --chat-id "chat_id" --content "Hello"`
- 发送私聊消息：`feishu msg send --user-id "user_id" --content "Hello"`

### 知识库操作
- 创建知识库节点：`feishu wiki create --space-id "space_id" --title "节点标题" --content "内容"`
- 列出知识库空间：`feishu wiki spaces`

## 权限管理
- 设置文档权限：`feishu doc permission set --doc-id "doc_id" --member "user_id" --role "writer"`
- 查看文档权限：`feishu doc permission get --doc-id "doc_id"`

## 示例

### 示例 1：创建飞书文档
```bash
feishu doc create --title "项目计划" --content "# 项目计划\n\n## 目标\n- 完成产品开发\n- 上线测试版本\n\n## 时间线\n- 2023-06-01: 需求分析\n- 2023-07-01: 开发完成\n- 2023-08-01: 上线测试"
```

### 示例 2：Markdown 转换为飞书文档
```bash
feishu doc upload --file project-plan.md --title "项目计划"
```

### 示例 3：发送群聊消息
```bash
feishu msg send --chat-id "oc_1234567890" --content "@all 项目计划已更新，请查看！"
```

## 注意事项

- 使用前需确保已正确配置飞书应用权限
- 部分操作可能需要管理员权限
- 详细文档请参考：https://github.com/riba2534/feishu-cli

## 故障排除

- **权限错误**：检查应用权限是否正确配置
- **认证失败**：检查环境变量是否设置正确
- **网络问题**：检查网络连接和飞书 API 访问情况