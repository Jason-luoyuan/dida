---
name: ticktick-openclaw-cloud
description: Manage Dida365/TickTick tasks and projects from cloud-hosted OpenClaw using a headless OAuth flow, token auto-refresh, smart name resolution, JSON CLI operations, and parent-task/subtask workflows. Use when users ask to connect Dida/TickTick accounts, create/update/complete/delete tasks, search tasks by title or content, manage parent-task and subtask structures, list projects/tasks, or troubleshoot auth/token issues in environments without local callback servers.
---

# TickTick OpenClaw Cloud

## Overview

Run Dida365/TickTick task management from a cloud OpenClaw deployment. Use the bundled script for OAuth authorization URL generation, callback exchange, token refresh, task/project CRUD, smart name-based resolution, broader task search, due-date views, batch creation, and parent-task/subtask operations.

## Smart Execution Rules

1. For create requests, prefer direct execution. If the user names a project, pass `--project-name`; if no project is given, default to `inbox`.
2. For update, complete, or delete requests where the user gives a task title instead of IDs, prefer `task-smart-update`, `task-smart-complete`, or `task-smart-delete`.
3. Use `task-find` for title-only resolution. Use `task-search` when the user references task content, descriptions, subtasks, tags, or project text.
4. If a single exact match exists, proceed. If one match clearly outranks the rest, proceed. If multiple plausible matches remain, ask one short clarification.
5. For date-oriented requests like "today", "tomorrow", "this week", or "overdue", prefer `tasks-due`. For focus-oriented requests like "engaged" or "next", prefer `tasks-focus`.
6. For batch creation requests, use `tasks-batch-create` with a JSON array instead of looping one task at a time.
7. For checklist-style requests, treat the parent task as the main task and subtasks as `items`. Use `task-create --subtask ...` when creating a parent task. For existing parent tasks without IDs, use `subtask-find` and `subtask-smart-*`.
8. Before `task-update` or `task-smart-update`, change only the fields the user explicitly asked to change.
9. Never invent due dates, priorities, or project names unless the user implied them clearly. If urgency is explicit, map priority as low=`1`, medium=`3`, high=`5`.
10. When a task title is ambiguous across projects, prefer the project mentioned by the user. If none is mentioned, return the smallest matching set and ask only if needed.

## Common Intent Mapping

- "Create a task": `task-create --project-name ...`
- "Update or rename a task": `task-smart-update`
- "Mark a task done": `task-smart-complete`
- "Delete a task": `task-smart-delete`
- "Search tasks by note/content/subtask": `task-search`
- "Show due today / overdue / this week": `tasks-due`
- "Show engaged / next tasks": `tasks-focus`
- "Create many tasks": `tasks-batch-create`
- "Add or edit subtasks without IDs": `subtask-smart-add` / `subtask-smart-update` / `subtask-smart-complete` / `subtask-smart-delete`
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
   python {baseDir}/scripts/ticktick_openclaw.py task-create --title "Prepare weekly report" --project-name "Work" --priority 3
   ```

## Command Workflow

1. Use `project-find` when the user gives a project name rather than an ID.
2. Use `task-find` for strict title matching and `task-search` for broader semantic lookup.
3. Use `task-smart-update`, `task-smart-complete`, and `task-smart-delete` when only a task title is known.
4. Use `task-create --project-name` for direct creation without a prior project lookup step.
5. Use `task-get` and `subtask-*` commands when IDs are already known.
6. Use `subtask-find` and `subtask-smart-*` when the user references parent/subtask titles instead of IDs.
7. Use `tasks-due`, `tasks-focus`, `tasks-filter`, and `tasks-completed` for reporting or review workflows.
8. Use `tasks-batch-create` when the user provides multiple tasks in one turn.
9. Use `token-status --auto-refresh` before long task batches.

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
python {baseDir}/scripts/ticktick_openclaw.py tasks --project-name "Work"
python {baseDir}/scripts/ticktick_openclaw.py task-find --title "Prepare proposal" --project-name "Work"
python {baseDir}/scripts/ticktick_openclaw.py task-search --query "proposal draft" --field title --field content --field subtask
python {baseDir}/scripts/ticktick_openclaw.py task-create --title "Prepare proposal" --project-name "Work" --priority 5 --subtask "Draft" --subtask "Review"
python {baseDir}/scripts/ticktick_openclaw.py task-smart-update --task-title "Prepare proposal" --project-name "Work" --due-date "2026-03-10T18:00:00+0800"
python {baseDir}/scripts/ticktick_openclaw.py task-smart-complete --task-title "Prepare proposal" --project-name "Work"
python {baseDir}/scripts/ticktick_openclaw.py task-smart-delete --task-title "Prepare proposal" --project-name "Work"
python {baseDir}/scripts/ticktick_openclaw.py tasks-due --when overdue
python {baseDir}/scripts/ticktick_openclaw.py tasks-focus --mode engaged
python {baseDir}/scripts/ticktick_openclaw.py tasks-batch-create --json-file "tasks.json"
python {baseDir}/scripts/ticktick_openclaw.py task-move --from-project-id "<project_id>" --to-project-id "<other_project_id>" --task-id "<task_id>"
python {baseDir}/scripts/ticktick_openclaw.py tasks-filter --project-name "Work" --status 0 --priority 3,5
python {baseDir}/scripts/ticktick_openclaw.py tasks-completed --project-name "Work" --start-date "2026-03-01T00:00:00+0000" --end-date "2026-03-08T23:59:59+0000"

# Subtasks
python {baseDir}/scripts/ticktick_openclaw.py subtask-find --parent-task-title "Prepare proposal" --subtask-title "Draft"
python {baseDir}/scripts/ticktick_openclaw.py subtask-smart-add --parent-task-title "Prepare proposal" --title "Collect feedback"
python {baseDir}/scripts/ticktick_openclaw.py subtask-smart-update --parent-task-title "Prepare proposal" --subtask-title "Draft" --new-title "Draft v2"
python {baseDir}/scripts/ticktick_openclaw.py subtask-smart-complete --parent-task-title "Prepare proposal" --subtask-title "Draft v2"
python {baseDir}/scripts/ticktick_openclaw.py subtask-smart-delete --parent-task-title "Prepare proposal" --subtask-title "Draft v2"
```

## Input Conventions

- Date-time fields use `"yyyy-MM-dd'T'HH:mm:ssZ"` format, for example `2026-03-08T10:00:00+0800`.
- Priority values: `0` (none), `1` (low), `3` (medium), `5` (high).
- `project-id` can be a regular project ID or `inbox`.
- `project-name` is resolved by exact/prefix/contains matching.
- Subtasks map to the official `items` field in task objects.
- Script outputs JSON by default for agent-safe parsing.

## Reliability Rules

1. Never log `client_secret`, `access_token`, or `refresh_token`.
2. Use `dida` region for Dida365 accounts and `ticktick` for TickTick accounts.
3. If callback exchange fails with state mismatch, regenerate URL via `auth-url`.
4. If refresh fails, re-run `auth-url` + `auth-exchange`.
5. `task-search`, `tasks-due`, `tasks-focus`, `tasks-batch-create`, and all `*-smart-*` commands are convenience wrappers over the official endpoints already documented in `references/openapi-cheatsheet.md`.

## References

- API fields and endpoint quick map: `references/openapi-cheatsheet.md`
- Research takeaways from GitHub/X examples: `references/research-notes.md`
