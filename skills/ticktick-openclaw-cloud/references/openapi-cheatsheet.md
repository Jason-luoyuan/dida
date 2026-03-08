# Dida365 / TickTick OpenAPI Cheatsheet

## Region Mapping

| Region | Auth Base | API Base |
|---|---|---|
| `dida` | `https://dida365.com` | `https://api.dida365.com/open/v1` |
| `ticktick` | `https://ticktick.com` | `https://api.ticktick.com/open/v1` |

## OAuth Parameters

Authorization URL query params:
- `client_id`
- `scope` (`tasks:read tasks:write`)
- `state`
- `redirect_uri`
- `response_type=code`

Token exchange endpoint:
- `POST /oauth/token`
- `grant_type=authorization_code`
- `code`
- `redirect_uri`
- `scope`

Token refresh endpoint:
- `POST /oauth/token`
- `grant_type=refresh_token`
- `refresh_token`

## Project Endpoints

- `GET /project`
- `GET /project/{projectId}`
- `GET /project/{projectId}/data`
- `POST /project`
- `POST /project/{projectId}`
- `DELETE /project/{projectId}`

Current script coverage:
- `projects`
- `project-find`
- `project-get`
- `project-create`
- `project-update`
- `project-delete`

## Task Endpoints

- `GET /project/{projectId}/task/{taskId}`
- `POST /task`
- `POST /task/{taskId}`
- `POST /project/{projectId}/task/{taskId}/complete`
- `DELETE /project/{projectId}/task/{taskId}`
- `POST /task/move`
- `POST /task/completed`
- `POST /task/filter`

Current script coverage:
- `tasks`
- `task-find`
- `task-search`
- `task-get`
- `task-create`
- `task-update`
- `task-smart-update`
- `task-complete`
- `task-smart-complete`
- `task-delete`
- `task-smart-delete`
- `task-move`
- `tasks-completed`
- `tasks-filter`
- `tasks-due`
- `tasks-focus`
- `schedule-analyze`
- `schedule-rebalance`
- `tasks-batch-create`
- `subtask-add`
- `subtask-find`
- `subtask-smart-add`
- `subtask-update`
- `subtask-smart-update`
- `subtask-complete`
- `subtask-smart-complete`
- `subtask-delete`
- `subtask-smart-delete`

## Common Task Fields

- `id`
- `projectId`
- `title`
- `content`
- `desc`
- `priority` (`0`, `1`, `3`, `5`)
- `status` (`0` active, `2` completed)
- `startDate`, `dueDate` in `yyyy-MM-dd'T'HH:mm:ssZ`
- `timeZone`
- `isAllDay`
- `tags`
- `items` (subtasks/checklist items under a parent task)

## Higher-Level Wrappers

These commands are wrappers over the official endpoints above, not additional vendor APIs:
- `task-search`: searches locally across task title, content, desc, subtasks, tags, and project names after pulling official task data.
- `task-smart-*`: resolves task titles to stable IDs, then calls the official update/complete/delete endpoints.
- `tasks-due`: filters official task data into `today`, `tomorrow`, `this-week`, `overdue`, or `--days N` views.
- `normalize_user_datetime_value` inside the script converts common ISO and natural phrases like `明天下午3点`, `下周一上午9点`, and `tomorrow 3pm` into TickTick-compatible date strings before API calls.
- `tasks-focus`: returns `engaged` and `next` task sets inspired by GTD-style review workflows.
- `schedule-analyze`: pulls official task/project data, normalizes timing, and returns a schedule-oriented JSON view with conflicts and risks.
- `schedule-rebalance`: proposes or applies rescheduling by combining official task data with local conflict-detection and blocked-time heuristics.
- `tasks-batch-create`: loops official `POST /task` calls from a JSON array.
- `subtask-find` and `subtask-smart-*`: resolve parent task/subtask titles, then update the official `items` field on the parent task.

## Command Design Notes

- Always return JSON for agent parsing.
- Accept `projectId` + `taskId` for low-level update/complete/delete to avoid ambiguous lookup.
- Accept `project-name` on list/create/filter flows to reduce agent-side lookup overhead.
- Default project target can be `inbox` when creating quick tasks.
- Refresh access token before API calls when near expiry.
- Parent/subtask management is done by reading/updating task `items` (there is no separate parentTaskId endpoint in OpenAPI).
