#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

DEFAULT_SCOPE = "tasks:read tasks:write"


@dataclass(frozen=True)
class RegionConfig:
    name: str
    auth_base: str
    api_base: str


REGIONS = {
    "dida": RegionConfig(
        name="dida",
        auth_base="https://dida365.com",
        api_base="https://api.dida365.com/open/v1",
    ),
    "ticktick": RegionConfig(
        name="ticktick",
        auth_base="https://ticktick.com",
        api_base="https://api.ticktick.com/open/v1",
    ),
}


class CliError(RuntimeError):
    pass


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc() -> str:
    return now_utc().isoformat()


def ticktick_time_now() -> str:
    return now_utc().strftime("%Y-%m-%dT%H:%M:%S+0000")


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def resolve_region(region_arg: str | None) -> RegionConfig:
    raw = (region_arg or os.getenv("TICKTICK_REGION") or "dida").strip().lower()
    if raw not in REGIONS:
        choices = ", ".join(sorted(REGIONS.keys()))
        raise CliError(f"Invalid region '{raw}'. Use one of: {choices}")
    return REGIONS[raw]


def resolve_path(flag_value: str | None, env_name: str, fallback: Path) -> Path:
    raw = flag_value or os.getenv(env_name)
    if raw:
        return Path(raw).expanduser().resolve()
    return fallback.expanduser().resolve()


def default_token_path() -> Path:
    return Path.home() / ".openclaw" / "credentials" / "ticktick-openclaw-cloud" / "token.json"


def default_state_path(token_path: Path) -> Path:
    return token_path.parent / "oauth_state.json"


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise CliError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise CliError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError(f"Expected JSON object in {path}.")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        raise CliError(f"Cannot write {path}: {exc}") from exc


def required_value(args: argparse.Namespace, attr_name: str, env_name: str, display_name: str) -> str:
    value = getattr(args, attr_name, None) or os.getenv(env_name)
    if not value:
        raise CliError(f"Missing {display_name}. Pass --{attr_name.replace('_', '-')} or set {env_name}.")
    return str(value).strip()


def parse_json_bytes(raw: bytes) -> Any:
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def error_message_from_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error_description", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(payload, ensure_ascii=False)
    if isinstance(payload, str):
        return payload
    return str(payload)


def send_request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    form_body: dict[str, Any] | None = None,
    expected_statuses: tuple[int, ...] = (200, 201),
    timeout: int = 30,
) -> Any:
    final_headers = {"Accept": "application/json"}
    if headers:
        final_headers.update(headers)

    if json_body is not None and form_body is not None:
        raise CliError("Cannot send JSON and form body in the same request.")

    payload: bytes | None = None
    if json_body is not None:
        payload = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        final_headers["Content-Type"] = "application/json"
    elif form_body is not None:
        encoded = urlencode({k: v for k, v in form_body.items() if v is not None})
        payload = encoded.encode("utf-8")
        final_headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = Request(url=url, data=payload, method=method.upper())
    for key, value in final_headers.items():
        request.add_header(key, value)

    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.getcode())
            raw = response.read()
    except HTTPError as exc:
        raw = exc.read()
        payload_obj = parse_json_bytes(raw)
        message = error_message_from_payload(payload_obj)
        raise CliError(f"HTTP {exc.code} {method.upper()} {url}: {message}") from exc
    except URLError as exc:
        raise CliError(f"Network error {method.upper()} {url}: {exc.reason}") from exc

    if status not in expected_statuses:
        payload_obj = parse_json_bytes(raw)
        message = error_message_from_payload(payload_obj)
        raise CliError(f"Unexpected HTTP {status} {method.upper()} {url}: {message}")

    return parse_json_bytes(raw)


def basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def token_expiry_epoch(token_data: dict[str, Any]) -> float | None:
    expires_at = token_data.get("expires_at")
    if isinstance(expires_at, (int, float)):
        return float(expires_at)

    created_at = token_data.get("obtained_at")
    expires_in = token_data.get("expires_in")
    if isinstance(created_at, str) and isinstance(expires_in, (int, float)):
        try:
            created_time = datetime.fromisoformat(created_at)
        except ValueError:
            return None
        return created_time.timestamp() + float(expires_in)
    return None


def ensure_token_file(token_path: Path) -> dict[str, Any]:
    token = read_json(token_path)
    if token is None:
        raise CliError(
            f"Token file not found at {token_path}. Run auth-url and auth-exchange first."
        )
    if not token.get("access_token"):
        raise CliError(f"Token file at {token_path} is missing access_token.")
    return token


def should_refresh_token(token_data: dict[str, Any], skew_seconds: int = 120) -> bool:
    expires_at = token_expiry_epoch(token_data)
    if expires_at is None:
        return False
    return time.time() + skew_seconds >= expires_at


def refresh_access_token(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    token_data: dict[str, Any],
) -> dict[str, Any]:
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise CliError("Token is expired and refresh_token is missing. Re-run auth-url and auth-exchange.")

    client_id = required_value(args, "client_id", "TICKTICK_CLIENT_ID", "client id")
    client_secret = required_value(args, "client_secret", "TICKTICK_CLIENT_SECRET", "client secret")

    token_endpoint = f"{region.auth_base}/oauth/token"
    response = send_request(
        "POST",
        token_endpoint,
        headers={"Authorization": basic_auth_header(client_id, client_secret)},
        form_body={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": token_data.get("scope") or DEFAULT_SCOPE,
        },
    )

    if not isinstance(response, dict) or not response.get("access_token"):
        raise CliError("Refresh response does not include access_token.")

    now_ts = time.time()
    expires_in = response.get("expires_in")
    updated = {
        "access_token": response.get("access_token"),
        "refresh_token": response.get("refresh_token") or refresh_token,
        "scope": response.get("scope") or token_data.get("scope") or DEFAULT_SCOPE,
        "token_type": response.get("token_type") or token_data.get("token_type") or "bearer",
        "expires_in": expires_in,
        "expires_at": now_ts + float(expires_in) if isinstance(expires_in, (int, float)) else None,
        "obtained_at": iso_utc(),
        "region": region.name,
    }
    write_json(token_path, updated)
    return updated


def get_access_token(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    allow_refresh: bool = True,
) -> dict[str, Any]:
    token_data = ensure_token_file(token_path)

    token_region = token_data.get("region")
    if isinstance(token_region, str) and token_region and token_region != region.name:
        raise CliError(
            f"Token region is '{token_region}' but command region is '{region.name}'. "
            "Use matching --region or re-authorize for this region."
        )

    if allow_refresh and should_refresh_token(token_data):
        token_data = refresh_access_token(args, region, token_path, token_data)

    return token_data


def api_request(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    method: str,
    route: str,
    json_body: Any | None = None,
    expected_statuses: tuple[int, ...] = (200, 201),
) -> Any:
    token_data = get_access_token(args, region, token_path, allow_refresh=True)
    token = str(token_data["access_token"]).strip()
    url = f"{region.api_base}{route}"
    return send_request(
        method=method,
        url=url,
        headers={"Authorization": f"Bearer {token}"},
        json_body=json_body,
        expected_statuses=expected_statuses,
    )


def clean_subtask_item(item: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "id",
        "title",
        "status",
        "completedTime",
        "isAllDay",
        "sortOrder",
        "startDate",
        "timeZone",
    }
    return {key: value for key, value in item.items() if key in allowed_keys}


def parse_csv_strings(value: str | None) -> list[str]:
    if not value:
        return []
    return [chunk.strip() for chunk in value.split(",") if chunk.strip()]


def parse_csv_ints(value: str | None) -> list[int]:
    values = parse_csv_strings(value)
    parsed: list[int] = []
    for value in values:
        try:
            parsed.append(int(value))
        except ValueError as exc:
            raise CliError(f"Invalid integer value '{value}'.") from exc
    return parsed


def fetch_task(args: argparse.Namespace, region: RegionConfig, token_path: Path, project_id: str, task_id: str) -> dict[str, Any]:
    response = api_request(args, region, token_path, "GET", f"/project/{project_id}/task/{task_id}")
    if not isinstance(response, dict):
        raise CliError("Unexpected task response format.")
    return response


def update_task_items(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    project_id: str,
    task_id: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "id": task_id,
        "projectId": project_id,
        "items": [clean_subtask_item(item) for item in items],
    }
    response = api_request(args, region, token_path, "POST", f"/task/{task_id}", json_body=payload)
    if not isinstance(response, dict):
        raise CliError("Unexpected task update response format.")
    return response


def command_auth_url(args: argparse.Namespace, region: RegionConfig, state_path: Path) -> None:
    client_id = required_value(args, "client_id", "TICKTICK_CLIENT_ID", "client id")
    redirect_uri = required_value(args, "redirect_uri", "TICKTICK_REDIRECT_URI", "redirect uri")
    scope = (args.scope or os.getenv("TICKTICK_SCOPE") or DEFAULT_SCOPE).strip()
    state = (args.state or secrets.token_urlsafe(24)).strip()

    params = {
        "client_id": client_id,
        "scope": scope,
        "state": state,
        "redirect_uri": redirect_uri,
        "response_type": "code",
    }

    auth_url = f"{region.auth_base}/oauth/authorize?{urlencode(params)}"
    state_payload = {
        "state": state,
        "region": region.name,
        "redirect_uri": redirect_uri,
        "created_at": iso_utc(),
    }
    write_json(state_path, state_payload)

    emit(
        {
            "ok": True,
            "region": region.name,
            "scope": scope,
            "authorization_url": auth_url,
            "state": state,
            "state_file": str(state_path),
        }
    )


def extract_callback_values(callback_url: str | None, auth_code: str | None, state: str | None) -> tuple[str, str | None]:
    if auth_code:
        return auth_code.strip(), state.strip() if state else None

    if not callback_url:
        raise CliError("Provide --callback-url or --auth-code.")

    parsed = urlparse(callback_url)
    query = parse_qs(parsed.query)
    if "error" in query:
        value = query.get("error", [""])[0]
        raise CliError(f"Authorization rejected: {value}")

    code = query.get("code", [None])[0]
    query_state = query.get("state", [None])[0]

    if not code:
        raise CliError("Could not find code in callback URL.")

    return str(code).strip(), str(query_state).strip() if query_state else None


def command_auth_exchange(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    state_path: Path,
) -> None:
    client_id = required_value(args, "client_id", "TICKTICK_CLIENT_ID", "client id")
    client_secret = required_value(args, "client_secret", "TICKTICK_CLIENT_SECRET", "client secret")
    redirect_uri = required_value(args, "redirect_uri", "TICKTICK_REDIRECT_URI", "redirect uri")
    scope = (args.scope or os.getenv("TICKTICK_SCOPE") or DEFAULT_SCOPE).strip()

    auth_code, callback_state = extract_callback_values(args.callback_url, args.auth_code, args.state)

    if not args.skip_state_check:
        state_doc = read_json(state_path)
        if state_doc and "state" in state_doc:
            expected_state = str(state_doc["state"])
            if callback_state and callback_state != expected_state:
                raise CliError("State mismatch. Run auth-url again and retry auth-exchange.")
            if not callback_state:
                raise CliError("Callback URL missing state parameter. Re-run auth-url and authorize again.")

    token_endpoint = f"{region.auth_base}/oauth/token"
    response = send_request(
        "POST",
        token_endpoint,
        headers={"Authorization": basic_auth_header(client_id, client_secret)},
        form_body={
            "grant_type": "authorization_code",
            "code": auth_code,
            "scope": scope,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )

    if not isinstance(response, dict) or not response.get("access_token"):
        raise CliError("Token exchange response does not include access_token.")

    now_ts = time.time()
    expires_in = response.get("expires_in")
    token_payload = {
        "access_token": response.get("access_token"),
        "refresh_token": response.get("refresh_token"),
        "scope": response.get("scope") or scope,
        "token_type": response.get("token_type") or "bearer",
        "expires_in": expires_in,
        "expires_at": now_ts + float(expires_in) if isinstance(expires_in, (int, float)) else None,
        "obtained_at": iso_utc(),
        "region": region.name,
    }
    write_json(token_path, token_payload)

    expires_at = token_expiry_epoch(token_payload)
    emit(
        {
            "ok": True,
            "region": region.name,
            "token_path": str(token_path),
            "has_refresh_token": bool(token_payload.get("refresh_token")),
            "scope": token_payload.get("scope"),
            "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat() if expires_at else None,
        }
    )


def command_token_status(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    token_data = get_access_token(args, region, token_path, allow_refresh=args.auto_refresh)
    expires_at = token_expiry_epoch(token_data)
    seconds_remaining = int(expires_at - time.time()) if isinstance(expires_at, float) else None
    emit(
        {
            "ok": True,
            "region": region.name,
            "token_path": str(token_path),
            "has_refresh_token": bool(token_data.get("refresh_token")),
            "scope": token_data.get("scope"),
            "obtained_at": token_data.get("obtained_at"),
            "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat() if expires_at else None,
            "seconds_remaining": seconds_remaining,
        }
    )


def command_projects(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    response = api_request(args, region, token_path, "GET", "/project")
    projects = response if isinstance(response, list) else []
    emit({"ok": True, "count": len(projects), "projects": projects})


def command_project_get(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    project = api_request(args, region, token_path, "GET", f"/project/{args.project_id}")
    emit({"ok": True, "project": project})


def command_project_create(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    payload: dict[str, Any] = {"name": args.name}
    if args.color:
        payload["color"] = args.color
    if args.view_mode:
        payload["viewMode"] = args.view_mode
    if args.kind:
        payload["kind"] = args.kind
    if args.sort_order is not None:
        payload["sortOrder"] = args.sort_order

    project = api_request(args, region, token_path, "POST", "/project", json_body=payload)
    emit({"ok": True, "project": project})


def command_project_update(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    payload: dict[str, Any] = {}
    if args.name:
        payload["name"] = args.name
    if args.color:
        payload["color"] = args.color
    if args.view_mode:
        payload["viewMode"] = args.view_mode
    if args.kind:
        payload["kind"] = args.kind
    if args.sort_order is not None:
        payload["sortOrder"] = args.sort_order
    if not payload:
        raise CliError("No project update fields provided.")

    project = api_request(args, region, token_path, "POST", f"/project/{args.project_id}", json_body=payload)
    emit({"ok": True, "project": project})


def command_project_delete(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    api_request(
        args,
        region,
        token_path,
        "DELETE",
        f"/project/{args.project_id}",
        expected_statuses=(200, 201),
    )
    emit({"ok": True, "deleted_project_id": args.project_id})


def command_tasks(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    if args.project_id:
        response = api_request(args, region, token_path, "GET", f"/project/{args.project_id}/data")
        project = response.get("project") if isinstance(response, dict) else None
        tasks = response.get("tasks", []) if isinstance(response, dict) else []
    else:
        projects_response = api_request(args, region, token_path, "GET", "/project")
        projects = projects_response if isinstance(projects_response, list) else []
        project = None
        tasks = []
        for item in projects:
            if not isinstance(item, dict):
                continue
            project_id = item.get("id")
            if not isinstance(project_id, str) or not project_id:
                continue
            if item.get("closed") and not args.include_closed_projects:
                continue
            data = api_request(args, region, token_path, "GET", f"/project/{project_id}/data")
            if isinstance(data, dict):
                for task in data.get("tasks", []):
                    if isinstance(task, dict):
                        tasks.append(task)

    filtered: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = task.get("status")
        if not args.include_completed and status == 2:
            continue
        filtered.append(task)

    if args.limit and args.limit > 0:
        filtered = filtered[: args.limit]

    emit(
        {
            "ok": True,
            "project": project,
            "count": len(filtered),
            "tasks": filtered,
        }
    )


def build_task_payload(args: argparse.Namespace, include_identity: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if include_identity:
        payload["id"] = args.task_id
        payload["projectId"] = args.project_id
    else:
        payload["title"] = args.title
        payload["projectId"] = args.project_id or "inbox"

    mapping = {
        "title": args.title,
        "content": args.content,
        "desc": args.desc,
        "dueDate": args.due_date,
        "startDate": args.start_date,
        "timeZone": args.time_zone,
        "repeatFlag": getattr(args, "repeat_flag", None),
    }

    for key, value in mapping.items():
        if value is not None:
            payload[key] = value

    if args.priority is not None:
        payload["priority"] = args.priority

    if args.all_day:
        payload["isAllDay"] = True

    if args.tags:
        payload["tags"] = [tag.strip() for tag in args.tags.split(",") if tag.strip()]

    reminders = getattr(args, "reminders", None)
    if reminders:
        payload["reminders"] = [item.strip() for item in reminders.split(",") if item.strip()]

    subtasks = getattr(args, "subtask", None)
    if subtasks:
        payload["items"] = [{"title": value} for value in subtasks if value and value.strip()]

    if include_identity:
        if args.clear_due_date:
            payload["dueDate"] = None
        if args.clear_start_date:
            payload["startDate"] = None

    return payload


def command_task_create(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    payload = build_task_payload(args, include_identity=False)
    task = api_request(args, region, token_path, "POST", "/task", json_body=payload)
    emit({"ok": True, "task": task})


def command_task_update(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    payload = build_task_payload(args, include_identity=True)
    if len(payload.keys()) <= 2:
        raise CliError("No update fields provided.")
    task = api_request(args, region, token_path, "POST", f"/task/{args.task_id}", json_body=payload)
    emit({"ok": True, "task": task})


def command_task_complete(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    api_request(
        args,
        region,
        token_path,
        "POST",
        f"/project/{args.project_id}/task/{args.task_id}/complete",
        expected_statuses=(200, 201),
    )
    emit({"ok": True, "completed_task_id": args.task_id, "project_id": args.project_id})


def command_task_delete(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    api_request(
        args,
        region,
        token_path,
        "DELETE",
        f"/project/{args.project_id}/task/{args.task_id}",
        expected_statuses=(200, 201),
    )
    emit({"ok": True, "deleted_task_id": args.task_id, "project_id": args.project_id})


def command_task_get(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    task = fetch_task(args, region, token_path, args.project_id, args.task_id)
    subtask_count = len(task.get("items", [])) if isinstance(task.get("items"), list) else 0
    emit({"ok": True, "task": task, "subtask_count": subtask_count})


def command_task_move(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    payload = [
        {
            "fromProjectId": args.from_project_id,
            "toProjectId": args.to_project_id,
            "taskId": task_id,
        }
        for task_id in args.task_id
    ]
    response = api_request(args, region, token_path, "POST", "/task/move", json_body=payload)
    moved = response if isinstance(response, list) else []
    emit({"ok": True, "count": len(moved), "moved": moved})


def command_tasks_completed(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    payload: dict[str, Any] = {}
    if args.project_id:
        payload["projectIds"] = args.project_id
    if args.start_date:
        payload["startDate"] = args.start_date
    if args.end_date:
        payload["endDate"] = args.end_date

    response = api_request(args, region, token_path, "POST", "/task/completed", json_body=payload)
    tasks = response if isinstance(response, list) else []
    emit({"ok": True, "count": len(tasks), "tasks": tasks})


def command_tasks_filter(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    payload: dict[str, Any] = {}
    if args.project_id:
        payload["projectIds"] = args.project_id
    if args.start_date:
        payload["startDate"] = args.start_date
    if args.end_date:
        payload["endDate"] = args.end_date
    if args.priority:
        payload["priority"] = parse_csv_ints(args.priority)
    if args.tag:
        payload["tag"] = parse_csv_strings(args.tag)
    if args.status:
        payload["status"] = parse_csv_ints(args.status)

    response = api_request(args, region, token_path, "POST", "/task/filter", json_body=payload)
    tasks = response if isinstance(response, list) else []
    if args.limit and args.limit > 0:
        tasks = tasks[: args.limit]
    emit({"ok": True, "count": len(tasks), "tasks": tasks})


def command_subtask_add(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    task = fetch_task(args, region, token_path, args.project_id, args.task_id)
    existing_items = task.get("items") if isinstance(task.get("items"), list) else []
    items = [clean_subtask_item(item) for item in existing_items if isinstance(item, dict)]

    new_item: dict[str, Any] = {"title": args.title}
    if args.start_date:
        new_item["startDate"] = args.start_date
    if args.time_zone:
        new_item["timeZone"] = args.time_zone
    if args.sort_order is not None:
        new_item["sortOrder"] = args.sort_order
    if args.all_day:
        new_item["isAllDay"] = True

    items.append(new_item)
    updated = update_task_items(args, region, token_path, args.project_id, args.task_id, items)
    updated_items = updated.get("items") if isinstance(updated.get("items"), list) else []
    emit({"ok": True, "task": updated, "subtask_count": len(updated_items)})


def command_subtask_update(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    task = fetch_task(args, region, token_path, args.project_id, args.task_id)
    existing_items = task.get("items") if isinstance(task.get("items"), list) else []
    items = [clean_subtask_item(item) for item in existing_items if isinstance(item, dict)]

    target = None
    for item in items:
        if str(item.get("id", "")) == args.subtask_id:
            target = item
            break

    if target is None:
        raise CliError(f"Subtask {args.subtask_id} not found in task {args.task_id}.")

    changed = False
    if args.title:
        target["title"] = args.title
        changed = True
    if args.start_date is not None:
        target["startDate"] = args.start_date
        changed = True
    if args.time_zone is not None:
        target["timeZone"] = args.time_zone
        changed = True
    if args.sort_order is not None:
        target["sortOrder"] = args.sort_order
        changed = True
    if args.all_day:
        target["isAllDay"] = True
        changed = True

    if not changed:
        raise CliError("No subtask update fields provided.")

    updated = update_task_items(args, region, token_path, args.project_id, args.task_id, items)
    emit({"ok": True, "task": updated})


def command_subtask_complete(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    task = fetch_task(args, region, token_path, args.project_id, args.task_id)
    existing_items = task.get("items") if isinstance(task.get("items"), list) else []
    items = [clean_subtask_item(item) for item in existing_items if isinstance(item, dict)]

    found = False
    for item in items:
        if str(item.get("id", "")) == args.subtask_id:
            item["status"] = 1
            item["completedTime"] = args.completed_time or ticktick_time_now()
            found = True
            break

    if not found:
        raise CliError(f"Subtask {args.subtask_id} not found in task {args.task_id}.")

    updated = update_task_items(args, region, token_path, args.project_id, args.task_id, items)
    emit({"ok": True, "task": updated, "completed_subtask_id": args.subtask_id})


def command_subtask_delete(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    task = fetch_task(args, region, token_path, args.project_id, args.task_id)
    existing_items = task.get("items") if isinstance(task.get("items"), list) else []
    items = [clean_subtask_item(item) for item in existing_items if isinstance(item, dict)]

    remaining = [item for item in items if str(item.get("id", "")) != args.subtask_id]
    if len(remaining) == len(items):
        raise CliError(f"Subtask {args.subtask_id} not found in task {args.task_id}.")

    updated = update_task_items(args, region, token_path, args.project_id, args.task_id, remaining)
    emit({"ok": True, "task": updated, "deleted_subtask_id": args.subtask_id})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ticktick_openclaw.py",
        description="Cloud-friendly Dida/TickTick CLI for OpenClaw skill workflows.",
    )

    parser.add_argument("--region", choices=sorted(REGIONS.keys()), help="API region: dida or ticktick")
    parser.add_argument("--token-path", help="Path to token JSON file")
    parser.add_argument("--state-path", help="Path to oauth state JSON file")
    parser.add_argument("--client-id", help="OAuth client id")
    parser.add_argument("--client-secret", help="OAuth client secret")
    parser.add_argument("--redirect-uri", help="OAuth redirect URI")

    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_url = subparsers.add_parser("auth-url", help="Generate OAuth authorization URL")
    auth_url.add_argument("--scope", default=DEFAULT_SCOPE)
    auth_url.add_argument("--state")

    auth_exchange = subparsers.add_parser("auth-exchange", help="Exchange callback URL for access token")
    auth_exchange.add_argument("--callback-url")
    auth_exchange.add_argument("--auth-code")
    auth_exchange.add_argument("--state")
    auth_exchange.add_argument("--scope", default=DEFAULT_SCOPE)
    auth_exchange.add_argument("--skip-state-check", action="store_true")

    token_status = subparsers.add_parser("token-status", help="Inspect token and optionally refresh")
    token_status.add_argument("--auto-refresh", action="store_true")

    subparsers.add_parser("projects", help="List projects")

    project_get = subparsers.add_parser("project-get", help="Get project by id")
    project_get.add_argument("--project-id", required=True)

    project_create = subparsers.add_parser("project-create", help="Create project")
    project_create.add_argument("--name", required=True)
    project_create.add_argument("--color")
    project_create.add_argument("--view-mode", choices=["list", "kanban", "timeline"])
    project_create.add_argument("--kind", choices=["TASK", "NOTE"])
    project_create.add_argument("--sort-order", type=int)

    project_update = subparsers.add_parser("project-update", help="Update project")
    project_update.add_argument("--project-id", required=True)
    project_update.add_argument("--name")
    project_update.add_argument("--color")
    project_update.add_argument("--view-mode", choices=["list", "kanban", "timeline"])
    project_update.add_argument("--kind", choices=["TASK", "NOTE"])
    project_update.add_argument("--sort-order", type=int)

    project_delete = subparsers.add_parser("project-delete", help="Delete project")
    project_delete.add_argument("--project-id", required=True)

    tasks = subparsers.add_parser("tasks", help="List tasks")
    tasks.add_argument("--project-id")
    tasks.add_argument("--include-completed", action="store_true")
    tasks.add_argument("--include-closed-projects", action="store_true")
    tasks.add_argument("--limit", type=int, default=0)

    task_get = subparsers.add_parser("task-get", help="Get task by project and task id")
    task_get.add_argument("--project-id", required=True)
    task_get.add_argument("--task-id", required=True)

    task_create = subparsers.add_parser("task-create", help="Create task")
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--project-id")
    task_create.add_argument("--content")
    task_create.add_argument("--desc")
    task_create.add_argument("--priority", type=int, choices=[0, 1, 3, 5])
    task_create.add_argument("--due-date")
    task_create.add_argument("--start-date")
    task_create.add_argument("--time-zone")
    task_create.add_argument("--all-day", action="store_true")
    task_create.add_argument("--tags", help="Comma-separated tags")
    task_create.add_argument("--repeat-flag")
    task_create.add_argument("--reminders", help="Comma-separated reminder triggers")
    task_create.add_argument("--subtask", action="append", help="Subtask title, repeatable")

    task_update = subparsers.add_parser("task-update", help="Update task")
    task_update.add_argument("--task-id", required=True)
    task_update.add_argument("--project-id", required=True)
    task_update.add_argument("--title")
    task_update.add_argument("--content")
    task_update.add_argument("--desc")
    task_update.add_argument("--priority", type=int, choices=[0, 1, 3, 5])
    task_update.add_argument("--due-date")
    task_update.add_argument("--start-date")
    task_update.add_argument("--time-zone")
    task_update.add_argument("--all-day", action="store_true")
    task_update.add_argument("--tags", help="Comma-separated tags")
    task_update.add_argument("--repeat-flag")
    task_update.add_argument("--reminders", help="Comma-separated reminder triggers")
    task_update.add_argument("--clear-due-date", action="store_true")
    task_update.add_argument("--clear-start-date", action="store_true")

    task_complete = subparsers.add_parser("task-complete", help="Complete task")
    task_complete.add_argument("--task-id", required=True)
    task_complete.add_argument("--project-id", required=True)

    task_delete = subparsers.add_parser("task-delete", help="Delete task")
    task_delete.add_argument("--task-id", required=True)
    task_delete.add_argument("--project-id", required=True)

    task_move = subparsers.add_parser("task-move", help="Move one or more tasks between projects")
    task_move.add_argument("--from-project-id", required=True)
    task_move.add_argument("--to-project-id", required=True)
    task_move.add_argument("--task-id", action="append", required=True, help="Repeat for multiple task ids")

    tasks_completed = subparsers.add_parser("tasks-completed", help="List completed tasks")
    tasks_completed.add_argument("--project-id", action="append", help="Repeat for multiple project ids")
    tasks_completed.add_argument("--start-date")
    tasks_completed.add_argument("--end-date")

    tasks_filter = subparsers.add_parser("tasks-filter", help="Filter tasks with server-side criteria")
    tasks_filter.add_argument("--project-id", action="append", help="Repeat for multiple project ids")
    tasks_filter.add_argument("--start-date")
    tasks_filter.add_argument("--end-date")
    tasks_filter.add_argument("--priority", help="Comma-separated values, e.g. 0,3,5")
    tasks_filter.add_argument("--tag", help="Comma-separated tags")
    tasks_filter.add_argument("--status", help="Comma-separated values, e.g. 0,2")
    tasks_filter.add_argument("--limit", type=int, default=0)

    subtask_add = subparsers.add_parser("subtask-add", help="Add subtask to a parent task")
    subtask_add.add_argument("--project-id", required=True)
    subtask_add.add_argument("--task-id", required=True)
    subtask_add.add_argument("--title", required=True)
    subtask_add.add_argument("--start-date")
    subtask_add.add_argument("--time-zone")
    subtask_add.add_argument("--sort-order", type=int)
    subtask_add.add_argument("--all-day", action="store_true")

    subtask_update = subparsers.add_parser("subtask-update", help="Update a subtask")
    subtask_update.add_argument("--project-id", required=True)
    subtask_update.add_argument("--task-id", required=True)
    subtask_update.add_argument("--subtask-id", required=True)
    subtask_update.add_argument("--title")
    subtask_update.add_argument("--start-date")
    subtask_update.add_argument("--time-zone")
    subtask_update.add_argument("--sort-order", type=int)
    subtask_update.add_argument("--all-day", action="store_true")

    subtask_complete = subparsers.add_parser("subtask-complete", help="Complete a subtask")
    subtask_complete.add_argument("--project-id", required=True)
    subtask_complete.add_argument("--task-id", required=True)
    subtask_complete.add_argument("--subtask-id", required=True)
    subtask_complete.add_argument("--completed-time", help="Time in yyyy-MM-dd'T'HH:mm:ssZ")

    subtask_delete = subparsers.add_parser("subtask-delete", help="Delete a subtask")
    subtask_delete.add_argument("--project-id", required=True)
    subtask_delete.add_argument("--task-id", required=True)
    subtask_delete.add_argument("--subtask-id", required=True)

    return parser


def run(args: argparse.Namespace) -> None:
    region = resolve_region(args.region)
    token_path = resolve_path(args.token_path, "TICKTICK_TOKEN_PATH", default_token_path())
    state_path = resolve_path(args.state_path, "TICKTICK_STATE_PATH", default_state_path(token_path))

    if args.command == "auth-url":
        command_auth_url(args, region, state_path)
    elif args.command == "auth-exchange":
        command_auth_exchange(args, region, token_path, state_path)
    elif args.command == "token-status":
        command_token_status(args, region, token_path)
    elif args.command == "projects":
        command_projects(args, region, token_path)
    elif args.command == "project-get":
        command_project_get(args, region, token_path)
    elif args.command == "project-create":
        command_project_create(args, region, token_path)
    elif args.command == "project-update":
        command_project_update(args, region, token_path)
    elif args.command == "project-delete":
        command_project_delete(args, region, token_path)
    elif args.command == "tasks":
        command_tasks(args, region, token_path)
    elif args.command == "task-get":
        command_task_get(args, region, token_path)
    elif args.command == "task-create":
        command_task_create(args, region, token_path)
    elif args.command == "task-update":
        command_task_update(args, region, token_path)
    elif args.command == "task-complete":
        command_task_complete(args, region, token_path)
    elif args.command == "task-delete":
        command_task_delete(args, region, token_path)
    elif args.command == "task-move":
        command_task_move(args, region, token_path)
    elif args.command == "tasks-completed":
        command_tasks_completed(args, region, token_path)
    elif args.command == "tasks-filter":
        command_tasks_filter(args, region, token_path)
    elif args.command == "subtask-add":
        command_subtask_add(args, region, token_path)
    elif args.command == "subtask-update":
        command_subtask_update(args, region, token_path)
    elif args.command == "subtask-complete":
        command_subtask_complete(args, region, token_path)
    elif args.command == "subtask-delete":
        command_subtask_delete(args, region, token_path)
    else:
        raise CliError(f"Unsupported command: {args.command}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        run(args)
        return 0
    except CliError as exc:
        emit({"ok": False, "error": str(exc)})
        return 1
    except KeyboardInterrupt:
        emit({"ok": False, "error": "Interrupted"})
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
