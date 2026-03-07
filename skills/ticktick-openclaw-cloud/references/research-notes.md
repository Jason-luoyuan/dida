# Research Notes (GitHub + X)

## GitHub Samples Reviewed

- OpenClaw community skill repository: https://github.com/openclaw/skills
- TickTick minimal skill: https://github.com/openclaw/skills/tree/main/skills/kaiofreitas/ticktick-api
- TickTick CLI skill (manual/headless OAuth): https://github.com/openclaw/skills/tree/main/skills/manuelhettich/ticktick
- TickTick runtime + OpenClaw wrappers: https://github.com/openclaw/skills/tree/main/skills/one0x393/ticktick-calendar
- TickTick CLI variant: https://github.com/openclaw/skills/tree/main/skills/norickkevorkov/ticktick-cli
- jen6/ticktick-mcp: https://github.com/jen6/ticktick-mcp
- jacepark12/ticktick-mcp: https://github.com/jacepark12/ticktick-mcp

## X (Twitter) Scan

- OpenClaw skill discussions/search: https://x.com/search?q=OpenClaw%20skills
- TickTick API/community search: https://x.com/search?q=TickTick%20API%20OpenClaw

## What Those Repos Did Better

### jen6/ticktick-mcp

- Clearer agent-facing tool semantics and field-by-field task docs.
- Better filtering/search framing, especially content/date guidance.
- Stronger emphasis on batch-safe task flows.

### jacepark12/ticktick-mcp

- Practical due-date presets like today, tomorrow, overdue, and this week.
- Broader search that looks beyond task title into content and subtasks.
- GTD-style grouped views such as engaged and next.
- Simpler natural-language operation surface for MCP clients.

## Patterns Adopted

1. Keep headless OAuth flow (`auth-url` + callback URL exchange) as the primary auth model because OpenClaw is cloud-hosted.
2. Keep commands JSON-first for reliable agent parsing.
3. Add `task-search` so the agent can resolve tasks by title, content, desc, subtask text, tags, or project names.
4. Add name-based smart commands: `task-smart-update`, `task-smart-complete`, `task-smart-delete`, `subtask-find`, and `subtask-smart-*`.
5. Add due-window wrappers: `tasks-due --when today|tomorrow|this-week|overdue` and `tasks-due --days N`.
6. Add GTD-style review helpers via `tasks-focus --mode engaged|next`.
7. Add `tasks-batch-create` for multi-task creation from a single JSON array.
8. Add `--project-name` support on task list/create/filter flows so the agent can act directly from user language without a manual ID lookup step.

## Explicit Non-Adoptions

- Did not switch to a local callback web server flow because the target deployment is cloud-based rather than local desktop.
- Did not depend on unofficial-only TickTick features outside the official OpenAPI surface for core task/project operations.
- Did not add separate tag/habit APIs because they are not part of the official Dida365/TickTick OpenAPI flow used by this skill.
