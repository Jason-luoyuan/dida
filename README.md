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

## OpenClaw 官方技能路径结论

按 OpenClaw 官方技能文档：

- 工作区技能目录：`<workspace>/skills`
- 全局技能目录：`~/.openclaw/skills`
- 优先级：工作区技能高于全局技能

对你当前这台云服务器，工作区根目录已经明确是：

- `/home/ubuntu/.openclaw/workspace/`

所以工作区技能目录就是：

- `/home/ubuntu/.openclaw/workspace/skills/`

全局技能目录就是：

- `/home/ubuntu/.openclaw/skills/`

## 推荐部署位置

对这个 skill，我建议你优先部署到工作区目录：

- `/home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud`

原因：

- 这是你当前 OpenClaw 实例直接使用的工作区
- 工作区技能优先级更高，覆盖更明确
- 这个 skill 还在迭代，放工作区更容易更新和回滚
- 不会影响同一台机器上其他工作区或其他 agent

如果你以后希望这台服务器上的所有 OpenClaw 工作区都共用这个 skill，再考虑放到：

- `/home/ubuntu/.openclaw/skills/ticktick-openclaw-cloud`

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
TICKTICK_TOKEN_PATH=/home/ubuntu/.openclaw/data/ticktick-openclaw-cloud/token.json
TICKTICK_STATE_PATH=/home/ubuntu/.openclaw/data/ticktick-openclaw-cloud/oauth_state.json
```

## 正确的部署方式

不要只把仓库 clone 到：

- `/home/ubuntu/.openclaw/workspace/dida`

因为官方文档约定的工作区技能目录是：

- `/home/ubuntu/.openclaw/workspace/skills`

也就是说，真正应该被 OpenClaw 直接加载的位置是：

- `/home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud`

### 工作区部署

```bash
cd /home/ubuntu/.openclaw/workspace/
git clone https://github.com/Jason-luoyuan/dida.git
mkdir -p /home/ubuntu/.openclaw/workspace/skills
cp -R /home/ubuntu/.openclaw/workspace/dida/skills/ticktick-openclaw-cloud /home/ubuntu/.openclaw/workspace/skills/
```

### 全局部署

```bash
cd /home/ubuntu/.openclaw/workspace/
git clone https://github.com/Jason-luoyuan/dida.git
mkdir -p /home/ubuntu/.openclaw/skills
cp -R /home/ubuntu/.openclaw/workspace/dida/skills/ticktick-openclaw-cloud /home/ubuntu/.openclaw/skills/
```

## 首次上线检查

如果你按推荐部署到工作区：

```bash
python /home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py doctor
```

如需连 API 一起检查：

```bash
python /home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py doctor --check-api --auto-refresh
```

## 首次授权

1. 生成授权链接：

```bash
python /home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py auth-url
```

2. 在本地浏览器打开返回的 `authorization_url`
3. 授权后复制完整回调 URL
4. 交换 token：

```bash
python /home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py auth-exchange --callback-url "https://your-domain.example.com/ticktick/callback?code=...&state=..."
```

5. 检查 token：

```bash
python /home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py token-status --auto-refresh
```

## 常用命令

```bash
python /home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py projects
python /home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py tasks --project-name "Work"
python /home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py task-create --title "准备周报" --project-name "Work" --due-date "明天下午3点"
python /home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py schedule-analyze --days 7
python /home/ubuntu/.openclaw/workspace/skills/ticktick-openclaw-cloud/scripts/ticktick_openclaw.py schedule-rebalance --current-task-title "线上故障" --current-task-until "今天18:00"
```

## 说明

- 默认 token 路径是运行用户 home 目录下的 `~/.openclaw/credentials/ticktick-openclaw-cloud/token.json`
- 在云端部署里，仍然建议显式设置 `TICKTICK_TOKEN_PATH` 和 `TICKTICK_STATE_PATH`
- 如果你的机器是持久化磁盘，重启后 token 文件会保留
- 如果以后迁移到新的容器或新的实例，记得一起迁移持久化目录
