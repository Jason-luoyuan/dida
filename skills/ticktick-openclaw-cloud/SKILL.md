---
name: ticktick-openclaw-cloud
description: Manage Dida365/TickTick tasks and projects from cloud-hosted OpenClaw using a headless OAuth flow, token auto-refresh, and JSON CLI operations. Use when users ask to connect Dida/TickTick accounts, create/update/complete/delete tasks, manage parent-task and subtask structures, list projects/tasks, or troubleshoot auth/token issues in environments without local callback servers.
---

# TickTick OpenClaw Cloud

## Overview

Run Dida365/TickTick task management from a cloud OpenClaw deployment. Use the bundled script for OAuth authorization URL generation, callback exchange, token refresh, task/project CRUD, and parent-task/subtask operations.

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

1. Use `projects` first to obtain reliable `projectId`.
2. Use `tasks --project-id <id>` to fetch current tasks.
3. Use `task-create`, `task-update`, `task-complete`, `task-delete` for lifecycle actions.
4. Use `task-get` and `subtask-*` commands to manage parent-task/subtask structures.
5. Prefer passing `projectId + taskId` directly for deterministic updates.
6. Use `token-status --auto-refresh` before long task batches.

## Core Commands

```bash
# Auth
python {baseDir}/scripts/ticktick_openclaw.py auth-url
python {baseDir}/scripts/ticktick_openclaw.py auth-exchange --callback-url "<callback_url>"
python {baseDir}/scripts/ticktick_openclaw.py token-status --auto-refresh

# Projects
python {baseDir}/scripts/ticktick_openclaw.py projects
python {baseDir}/scripts/ticktick_openclaw.py project-create --name "Work"
python {baseDir}/scripts/ticktick_openclaw.py project-delete --project-id "<project_id>"

# Tasks
python {baseDir}/scripts/ticktick_openclaw.py tasks --project-id "<project_id>"
python {baseDir}/scripts/ticktick_openclaw.py task-create --title "Prepare proposal" --project-id "<project_id>" --priority 5 --subtask "Draft" --subtask "Review"
python {baseDir}/scripts/ticktick_openclaw.py task-get --task-id "<task_id>" --project-id "<project_id>"
python {baseDir}/scripts/ticktick_openclaw.py task-update --task-id "<task_id>" --project-id "<project_id>" --title "Updated title"
python {baseDir}/scripts/ticktick_openclaw.py subtask-add --task-id "<task_id>" --project-id "<project_id>" --title "Collect feedback"
python {baseDir}/scripts/ticktick_openclaw.py subtask-update --task-id "<task_id>" --project-id "<project_id>" --subtask-id "<subtask_id>" --title "Feedback complete"
python {baseDir}/scripts/ticktick_openclaw.py subtask-complete --task-id "<task_id>" --project-id "<project_id>" --subtask-id "<subtask_id>"
python {baseDir}/scripts/ticktick_openclaw.py subtask-delete --task-id "<task_id>" --project-id "<project_id>" --subtask-id "<subtask_id>"
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
