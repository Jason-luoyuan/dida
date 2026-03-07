# 滴答清单 OpenClaw 云端 Skill

本仓库提供一个可在**云端 OpenClaw** 环境运行的滴答清单（Dida365 / TickTick）技能：

- 技能目录：`skills/ticktick-openclaw-cloud`
- 核心脚本：`skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py`
- 特点：云端无本地回调服务场景可用、支持 OAuth 手动回调、Token 自动刷新、任务与项目管理（JSON 输出）

## 目录结构

```text
skills/ticktick-openclaw-cloud/
├─ SKILL.md
├─ agents/openai.yaml
├─ scripts/ticktick_openclaw.py
└─ references/
   ├─ openapi-cheatsheet.md
   └─ research-notes.md
```

## 快速开始（云端部署）

### 1) 准备环境变量

在云端 OpenClaw 运行环境中配置：

- `TICKTICK_CLIENT_ID`
- `TICKTICK_CLIENT_SECRET`
- `TICKTICK_REDIRECT_URI`
- 可选：`TICKTICK_REGION`（`dida` 或 `ticktick`，默认 `dida`）

### 2) 生成授权链接

```bash
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py auth-url
```

### 3) 浏览器授权并回传 callback URL

在本地浏览器打开上一步返回的 `authorization_url`，授权后复制完整回调 URL。

### 4) 交换 Token

```bash
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py auth-exchange --callback-url "https://your.redirect/callback?code=...&state=..."
```

### 5) 检查 Token 状态（可自动刷新）

```bash
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py token-status --auto-refresh
```

## 常用命令

```bash
# 列项目
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py projects

# 创建项目
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py project-create --name "Work"

# 列某项目任务
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py tasks --project-id "<project_id>"

# 创建任务（默认可用 inbox）
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py task-create --title "Prepare proposal" --project-id "inbox" --priority 3

# 更新任务
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py task-update --task-id "<task_id>" --project-id "<project_id>" --title "Updated title"

# 完成任务
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py task-complete --task-id "<task_id>" --project-id "<project_id>"

# 删除任务
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py task-delete --task-id "<task_id>" --project-id "<project_id>"
```

## 云端运行建议

- 将 token 文件挂载到持久化存储（默认：`~/.openclaw/credentials/ticktick-openclaw-cloud/token.json`）。
- 建议总是先 `projects` 获取真实 `projectId`，再执行任务增删改。
- 定时任务/批处理前先执行 `token-status --auto-refresh`。

## 故障排查

- `Missing client id`：检查环境变量或在命令中传 `--client-id`。
- `Token file not found`：先执行 `auth-url` + `auth-exchange`。
- `State mismatch`：重新执行 `auth-url` 生成新 state 后再次授权。
- `Token region is ...`：切换 `--region` 或重新按对应区域授权。

## 安全说明

- 不要在日志中打印 `client_secret`、`access_token`、`refresh_token`。
- Token 文件应仅允许最小权限访问。
