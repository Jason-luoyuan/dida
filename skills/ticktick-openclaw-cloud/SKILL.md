---
name: ticktick-openclaw-cloud
description: Manage Dida365/TickTick tasks and projects from cloud-hosted OpenClaw using a headless OAuth flow, token auto-refresh, and JSON CLI operations. Use when users ask to connect Dida/TickTick accounts, create/update/complete/delete tasks, manage parent-task and subtask structures, list projects/tasks, or troubleshoot auth/token issues in environments without local callback servers.
---

# TickTick OpenClaw Cloud

## Overview

Run Dida365/TickTick task management from a cloud OpenClaw deployment. Use the bundled script for OAuth authorization URL generation, callback exchange, token refresh, task/project CRUD, and parent-task/subtask operations.

## Smart Execution Rules

1. For create requests, prefer direct execution. If the user names a project, resolve it with `project-find`; if no project is given, default to `inbox`.
2. For update, complete, delete, or move requests, first resolve identity. Use `task-get` when the user provides IDs; otherwise use `task-find` and narrow by project when possible.
3. If `task-find` returns one strong match (`exact` or a clearly dominant `prefix` match), proceed without asking. If multiple plausible matches remain, ask one short clarification.
4. Before `task-update`, fetch the current task and preserve unspecified fields. Only change the fields the user asked to change.
5. For checklist-style requests, treat the parent task as the main task and subtasks as `items`. Use `task-create --subtask ...` on creation and `subtask-*` commands for later edits.
6. For reporting or smart selection requests like "show overdue/high priority/completed last week", prefer `tasks-filter` or `tasks-completed` before falling back to full project scans.
7. Never invent due dates, priorities, or project names unless the user implied them clearly. If urgency is explicit, map priority as low=`1`, medium=`3`, high=`5`.
8. When a task title is ambiguous across projects, prefer the project mentioned by the user. If none is mentioned, return the smallest matching set and ask for disambiguation only if needed.

## Common Intent Mapping

- "Create a task": resolve project, then `task-create`
- "Update/rename a task": `task-find` -> `task-get` -> `task-update`
- "Mark a task done": `task-find` -> `task-complete`
- "Delete a task": `task-find` -> `task-delete`
- "Add or edit subtasks": `task-find` -> `task-get` -> `subtask-*`
- "Move tasks between projects": resolve both projects, resolve tasks, then `task-move`

## Quick Start (Cloud OAuth)

1. Set environment variables in your cloud runtime:
   - `TICKTICK_CLIENT_ID`
   - `TICKTICK_CLIENT_SECRET`
   - `TICKTICK_REDIRECT_URI`
   - Optional: `TICKTICK_REGION` (`dida` default, or `ticktick`)
2. Generate an authorization URL:
   ```bash
   python {baseDir}/scripts/ticktick_openclaw.py auth-url
   ```
3. Open that URL locally, approve access, and copy the full callback URL.
4. Exchange callback URL for token:
   ```bash
   python {baseDir}/scripts/ticktick_openclaw.py auth-exchange --callback-url "https://your.redirect/callback?code=...&state=..."
   ```
5. Verify token status:
   ```bash
   python {baseDir}/scripts/ticktick_openclaw.py token-status --auto-refresh
   ```
6. Start task management:
   ```bash
   python {baseDir}/scripts/ticktick_openclaw.py projects
   python {baseDir}/scripts/ticktick_openclaw.py task-create --title "Prepare weekly report" --project-id inbox --priority 3
   ```

## Command Workflow

1. Use `project-find` when the user gives a project name rather than an ID.
2. Use `task-find` when the user gives a task title rather than an ID.
3. Use `project-get` and `project-update` when you need exact project metadata edits.
4. Use `task-create`, `task-update`, `task-complete`, `task-delete` for lifecycle actions.
5. Use `task-get` and `subtask-*` commands to manage parent-task/subtask structures.
6. Use `task-move`, `tasks-filter`, and `tasks-completed` for bulk or reporting workflows.
7. Prefer passing `projectId + taskId` directly when already known.
8. Use `token-status --auto-refresh` before long task batches.

## Core Commands

```bash
# Auth
python {baseDir}/scripts/ticktick_openclaw.py auth-url
python {baseDir}/scripts/ticktick_openclaw.py auth-exchange --callback-url "<callback_url>"
python {baseDir}/scripts/ticktick_openclaw.py token-status --auto-refresh

# Projects
python {baseDir}/scripts/ticktick_openclaw.py projects
python {baseDir}/scripts/ticktick_openclaw.py project-find --name "Work"
python {baseDir}/scripts/ticktick_openclaw.py project-create --name "Work"
python {baseDir}/scripts/ticktick_openclaw.py project-get --project-id "<project_id>"
python {baseDir}/scripts/ticktick_openclaw.py project-update --project-id "<project_id>" --name "Work Ops" --view-mode kanban
python {baseDir}/scripts/ticktick_openclaw.py project-delete --project-id "<project_id>"

# Tasks
python {baseDir}/scripts/ticktick_openclaw.py tasks --project-id "<project_id>"
python {baseDir}/scripts/ticktick_openclaw.py task-find --title "Prepare proposal" --project-id "<project_id>"
python {baseDir}/scripts/ticktick_openclaw.py task-create --title "Prepare proposal" --project-id "<project_id>" --priority 5 --subtask "Draft" --subtask "Review"
python {baseDir}/scripts/ticktick_openclaw.py task-get --task-id "<task_id>" --project-id "<project_id>"
python {baseDir}/scripts/ticktick_openclaw.py task-update --task-id "<task_id>" --project-id "<project_id>" --title "Updated title"
python {baseDir}/scripts/ticktick_openclaw.py subtask-add --task-id "<task_id>" --project-id "<project_id>" --title "Collect feedback"
python {baseDir}/scripts/ticktick_openclaw.py subtask-update --task-id "<task_id>" --project-id "<project_id>" --subtask-id "<subtask_id>" --title "Feedback complete"
python {baseDir}/scripts/ticktick_openclaw.py subtask-complete --task-id "<task_id>" --project-id "<project_id>" --subtask-id "<subtask_id>"
python {baseDir}/scripts/ticktick_openclaw.py subtask-delete --task-id "<task_id>" --project-id "<project_id>" --subtask-id "<subtask_id>"
python {baseDir}/scripts/ticktick_openclaw.py task-move --from-project-id "<project_id>" --to-project-id "<other_project_id>" --task-id "<task_id>"
python {baseDir}/scripts/ticktick_openclaw.py tasks-filter --project-id "<project_id>" --status 0 --priority 3,5
python {baseDir}/scripts/ticktick_openclaw.py tasks-completed --project-id "<project_id>" --start-date "2026-03-01T00:00:00+0000" --end-date "2026-03-08T23:59:59+0000"
python {baseDir}/scripts/ticktick_openclaw.py task-complete --task-id "<task_id>" --project-id "<project_id>"
python {baseDir}/scripts/ticktick_openclaw.py task-delete --task-id "<task_id>" --project-id "<project_id>"
```

## Input Conventions

- Date-time fields use `"yyyy-MM-dd'T'HH:mm:ssZ"` format, for example `2026-03-08T10:00:00+0800`.
- Priority values: `0` (none), `1` (low), `3` (medium), `5` (high).
- `project-id` can be regular project ID or `inbox`.
- Subtasks map to the official `items` field in task objects.
- Script outputs JSON by default for agent-safe parsing.

## Reliability Rules

1. Never log `client_secret`, `access_token`, or `refresh_token`.
2. Use `dida` region for Dida365 accounts and `ticktick` for TickTick accounts.
3. If callback exchange fails with state mismatch, regenerate URL via `auth-url`.
4. If refresh fails, re-run `auth-url` + `auth-exchange`.

## References

- API fields and endpoint quick map: `references/openapi-cheatsheet.md`
- Research takeaways from GitHub/X examples: `references/research-notes.md`
