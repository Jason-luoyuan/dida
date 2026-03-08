# 滴答清单 OpenClaw 云端 Skill

这个仓库提供一个适合云端 OpenClaw 的 Dida365 / TickTick skill，核心目录是 `skills/ticktick-openclaw-cloud`。

## 主要能力

- 云端可用的 headless OAuth 授权
- Token 自动刷新
- 项目与任务 CRUD
- 智能任务查找、更新、完成、删除
- 父任务 / 子任务管理
- 自然语言时间解析
- 排期分析与冲突重排
- 部署自检命令 `doctor`

## 推荐的云端环境变量

请在你的 OpenClaw 运行环境中配置：

- `TICKTICK_CLIENT_ID`
- `TICKTICK_CLIENT_SECRET`
- `TICKTICK_REDIRECT_URI`
- `TICKTICK_REGION`（可选，`dida` 或 `ticktick`，默认 `dida`）
- `TICKTICK_TOKEN_PATH`（强烈建议，指向持久化目录）
- `TICKTICK_STATE_PATH`（强烈建议，指向持久化目录）

示例：

```env
TICKTICK_REGION=dida
TICKTICK_CLIENT_ID=your_client_id
TICKTICK_CLIENT_SECRET=your_client_secret
TICKTICK_REDIRECT_URI=https://your-domain.example.com/ticktick/callback
TICKTICK_TOKEN_PATH=/data/openclaw/ticktick-openclaw-cloud/token.json
TICKTICK_STATE_PATH=/data/openclaw/ticktick-openclaw-cloud/oauth_state.json
```

## 部署到 OpenClaw

### 方式一：标准技能目录部署

如果你的 OpenClaw / Codex 兼容实例从 `$CODEX_HOME/skills` 读取技能：

```bash
git clone https://github.com/Jason-luoyuan/dida.git
mkdir -p "$CODEX_HOME/skills"
cp -R dida/skills/ticktick-openclaw-cloud "$CODEX_HOME/skills/"
```

重启 OpenClaw，让它重新加载技能。

### 方式二：工作区内直接挂载

如果你的 OpenClaw 已经把这个仓库挂进工作区，并且会读取工作区内的 `skills/` 目录，那就不需要额外复制，只要保证：

- `skills/ticktick-openclaw-cloud` 存在
- Python 可执行
- 上面的环境变量已经注入到 OpenClaw 运行环境

然后重启 OpenClaw。

## 首次上线检查

在 OpenClaw 所在服务器上运行：

```bash
python skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py doctor
```

如果技能已经安装到 `$CODEX_HOME/skills`，则运行：

```bash
python "$CODEX_HOME/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py" doctor
```

如需连 API 一起检查：

```bash
python "$CODEX_HOME/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py" doctor --check-api --auto-refresh
```

## 首次授权

1. 生成授权链接：

```bash
python "$CODEX_HOME/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py" auth-url
```

2. 在本地浏览器打开返回的 `authorization_url`
3. 授权后复制完整回调 URL
4. 交换 token：

```bash
python "$CODEX_HOME/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py" auth-exchange --callback-url "https://your-domain.example.com/ticktick/callback?code=...&state=..."
```

5. 检查 token：

```bash
python "$CODEX_HOME/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py" token-status --auto-refresh
```

## 常用命令

```bash
python "$CODEX_HOME/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py" projects
python "$CODEX_HOME/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py" tasks --project-name "Work"
python "$CODEX_HOME/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py" task-create --title "准备周报" --project-name "Work" --due-date "明天下午3点"
python "$CODEX_HOME/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py" schedule-analyze --days 7
python "$CODEX_HOME/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py" schedule-rebalance --current-task-title "线上故障" --current-task-until "今天18:00"
```

## 说明

- 默认 token 路径是运行用户 home 目录下的 `~/.openclaw/credentials/ticktick-openclaw-cloud/token.json`
- 在云端部署里，仍然建议显式设置 `TICKTICK_TOKEN_PATH` 和 `TICKTICK_STATE_PATH`，这样最清晰
- 如果你的机器是持久化磁盘，重启后 token 文件会保留
- 如果未来切换到新的容器或新的实例，记得一起迁移持久化目录
