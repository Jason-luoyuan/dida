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
- `task-get`
- `task-create`
- `task-update`
- `task-complete`
- `task-delete`
- `task-move`
- `tasks-completed`
- `tasks-filter`
- `subtask-add`
- `subtask-update`
- `subtask-complete`
- `subtask-delete`

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

## Command Design Notes

- Always return JSON for agent parsing.
- Accept `projectId` + `taskId` for update/complete/delete to avoid ambiguous lookup.
- Default project target can be `inbox` when creating quick tasks.
- Refresh access token before API calls when near expiry.
- Parent/subtask management is done by reading/updating task `items` (there is no separate parentTaskId endpoint in OpenAPI).
