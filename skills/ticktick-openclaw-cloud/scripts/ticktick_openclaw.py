#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_SCOPE = "tasks:read tasks:write"
DEFAULT_LOCALHOST_REDIRECT_URI = "http://localhost:8080/callback"


@dataclass(frozen=True)
class RegionConfig:
    name: str
    auth_base: str
    api_base: str


@dataclass(frozen=True)
class BusyWindow:
    label: str
    start: datetime
    end: datetime
    source: str


@dataclass
class ScheduleEntry:
    task: dict[str, Any]
    task_id: str
    project_id: str
    project_name: str
    title: str
    start: datetime | None
    end: datetime | None
    deadline: datetime | None
    time_zone: str
    schedule_type: str
    duration_minutes: int | None
    priority: int
    all_day: bool


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




def value_source(flag_value: str | None, env_name: str) -> str:
    if flag_value:
        return 'flag'
    if os.getenv(env_name):
        return 'env'
    return 'default'


def default_port_for_scheme(scheme: str) -> int | None:
    if scheme == 'http':
        return 80
    if scheme == 'https':
        return 443
    return None


def classify_redirect_uri(redirect_uri: str | None) -> dict[str, Any]:
    if not redirect_uri:
        return {
            'present': False,
            'mode': 'missing',
            'host': None,
            'scheme': None,
            'path': None,
            'port': None,
            'recommendedLocalhostUri': DEFAULT_LOCALHOST_REDIRECT_URI,
        }

    parsed = urlparse(redirect_uri)
    host = (parsed.hostname or '').strip().lower() or None
    scheme = (parsed.scheme or '').strip().lower() or None
    path = parsed.path or '/'
    port = parsed.port or default_port_for_scheme(scheme or '')

    if not scheme or not host:
        mode = 'invalid'
    elif host in {'localhost', '127.0.0.1', '::1'}:
        mode = 'localhost'
    else:
        mode = 'remote'

    return {
        'present': True,
        'mode': mode,
        'host': host,
        'scheme': scheme,
        'path': path,
        'port': port,
        'recommendedLocalhostUri': DEFAULT_LOCALHOST_REDIRECT_URI,
    }


def callback_url_matches_redirect_uri(callback_url: str, redirect_uri: str) -> bool:
    callback = urlparse(callback_url)
    redirect = urlparse(redirect_uri)
    callback_scheme = (callback.scheme or '').strip().lower()
    redirect_scheme = (redirect.scheme or '').strip().lower()
    callback_host = (callback.hostname or '').strip().lower()
    redirect_host = (redirect.hostname or '').strip().lower()
    callback_port = callback.port or default_port_for_scheme(callback_scheme)
    redirect_port = redirect.port or default_port_for_scheme(redirect_scheme)
    callback_path = callback.path or '/'
    redirect_path = redirect.path or '/'
    return (
        callback_scheme == redirect_scheme
        and callback_host == redirect_host
        and callback_port == redirect_port
        and callback_path == redirect_path
    )


def probe_writable_directory(path: Path) -> tuple[bool, str | None]:
    probe_file: Path | None = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe_file = path / f'.write-test-{secrets.token_hex(6)}'
        with probe_file.open('w', encoding='utf-8') as handle:
            handle.write('ok\n')
        probe_file.unlink()
        return True, None
    except OSError as exc:
        if probe_file and probe_file.exists():
            try:
                probe_file.unlink()
            except OSError:
                pass
        return False, str(exc)


def build_path_diagnostic(path: Path, source: str) -> dict[str, Any]:
    writable, write_error = probe_writable_directory(path.parent)
    return {
        'path': str(path),
        'source': source,
        'exists': path.exists(),
        'parentPath': str(path.parent),
        'parentExists': path.parent.exists(),
        'parentWritable': writable,
        'writeError': write_error,
    }


def inspect_token_file(token_path: Path, region: RegionConfig) -> dict[str, Any]:
    info: dict[str, Any] = {
        'path': str(token_path),
        'exists': token_path.exists(),
        'valid': False,
        'hasAccessToken': False,
        'hasRefreshToken': False,
        'regionMatchesCommand': None,
        'expiresAt': None,
        'secondsRemaining': None,
        'needsRefresh': None,
        'error': None,
    }
    if not token_path.exists():
        return info

    try:
        token_data = ensure_token_file(token_path)
    except CliError as exc:
        info['error'] = str(exc)
        return info

    info['valid'] = True
    info['hasAccessToken'] = bool(token_data.get('access_token'))
    info['hasRefreshToken'] = bool(token_data.get('refresh_token'))
    token_region = token_data.get('region')
    info['tokenRegion'] = token_region
    info['regionMatchesCommand'] = not token_region or token_region == region.name
    expires_at = token_expiry_epoch(token_data)
    if isinstance(expires_at, float):
        info['expiresAt'] = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
        info['secondsRemaining'] = int(expires_at - time.time())
    info['needsRefresh'] = should_refresh_token(token_data)
    info['scope'] = token_data.get('scope')
    info['obtainedAt'] = token_data.get('obtained_at')
    return info

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


def normalize_match_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.casefold().split())


def classify_match(query: str, candidate: str) -> str | None:
    normalized_query = normalize_match_text(query)
    normalized_candidate = normalize_match_text(candidate)
    if not normalized_query or not normalized_candidate:
        return None
    if normalized_query == normalized_candidate:
        return "exact"
    if normalized_candidate.startswith(normalized_query):
        return "prefix"
    if normalized_query in normalized_candidate:
        return "contains"
    return None


def match_rank(match_type: str) -> int:
    order = {"exact": 0, "prefix": 1, "contains": 2}
    return order.get(match_type, 99)


TASK_SEARCH_FIELDS = ("title", "content", "desc", "subtask", "tag", "project")


def parse_json_document(raw_text: str | None, file_path: str | None) -> Any:
    if raw_text and file_path:
        raise CliError("Pass only one of --json or --json-file.")
    if not raw_text and not file_path:
        raise CliError("Pass --json or --json-file.")
    if file_path:
        try:
            raw_text = Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(f"Cannot read {file_path}: {exc}") from exc
    try:
        return json.loads(raw_text or "")
    except json.JSONDecodeError as exc:
        raise CliError(f"Invalid JSON input: {exc}") from exc


def parse_ticktick_datetime(raw_value: str | None) -> datetime | None:
    if not raw_value or not isinstance(raw_value, str):
        return None
    text = raw_value.strip()
    if not text:
        return None

    formats = (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    )
    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text[:-1] + "+0000")
        candidates.append(text[:-1] + "+00:00")

    for candidate in candidates:
        for fmt in formats:
            try:
                parsed = datetime.strptime(candidate, fmt)
                if fmt == "%Y-%m-%d":
                    return parsed.replace(tzinfo=timezone.utc)
                return parsed
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    if len(text) >= 10:
        try:
            parsed_date = datetime.strptime(text[:10], "%Y-%m-%d")
            return parsed_date.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


CHINESE_WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
ENGLISH_WEEKDAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
FALLBACK_TIMEZONE_OFFSETS = {
    "UTC": 0,
    "Etc/UTC": 0,
    "Asia/Shanghai": 8,
    "Asia/Chongqing": 8,
    "Asia/Hong_Kong": 8,
    "Asia/Taipei": 8,
    "Asia/Seoul": 9,
    "Asia/Tokyo": 9,
    "Europe/London": 0,
    "America/New_York": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Los_Angeles": -8,
}


def default_time_zone_name(explicit_time_zone: str | None = None) -> str:
    return explicit_time_zone or os.getenv("TICKTICK_DEFAULT_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"


def resolve_time_zone(explicit_time_zone: str | None = None) -> ZoneInfo | timezone:
    time_zone_name = default_time_zone_name(explicit_time_zone)
    try:
        return ZoneInfo(time_zone_name)
    except ZoneInfoNotFoundError as exc:
        if time_zone_name in FALLBACK_TIMEZONE_OFFSETS:
            offset_hours = FALLBACK_TIMEZONE_OFFSETS[time_zone_name]
            return timezone(timedelta(hours=offset_hours), name=time_zone_name)
        raise CliError(f"Invalid time zone '{time_zone_name}'. Use an IANA zone like Asia/Shanghai.") from exc


def format_ticktick_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S%z")


def looks_like_date_only(value: str) -> bool:
    return bool(
        re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", value)
        or re.fullmatch(r"\d{1,2}月\d{1,2}日?", value)
        or re.fullmatch(r"\d{1,2}[-/]\d{1,2}", value)
    )


def normalize_explicit_datetime_input(raw_value: str, explicit_time_zone: str | None = None) -> str | None:
    candidate = raw_value.strip().replace("：", ":")
    parsed = parse_ticktick_datetime(candidate)
    if parsed is None:
        return None
    if looks_like_date_only(candidate):
        return parsed.strftime("%Y-%m-%d")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=resolve_time_zone(explicit_time_zone))
    return format_ticktick_datetime(parsed)


def parse_date_from_text(raw_value: str, explicit_time_zone: str | None = None) -> datetime.date | None:
    zone = resolve_time_zone(explicit_time_zone)
    now_local = datetime.now(zone)
    today = now_local.date()
    compact = raw_value.strip().replace(" ", "").replace("：", ":")
    lowered = raw_value.casefold()

    full_date_match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", compact)
    if full_date_match:
        year, month, day = (int(value) for value in full_date_match.groups())
        return datetime(year, month, day).date()

    month_day_match = re.search(r"(\d{1,2})月(\d{1,2})日?", compact)
    if month_day_match:
        month, day = (int(value) for value in month_day_match.groups())
        candidate = datetime(today.year, month, day).date()
        if candidate < today - timedelta(days=1):
            candidate = datetime(today.year + 1, month, day).date()
        return candidate

    short_date_match = re.search(r"(?<!\d)(\d{1,2})[-/](\d{1,2})(?!\d)", compact)
    if short_date_match:
        month, day = (int(value) for value in short_date_match.groups())
        candidate = datetime(today.year, month, day).date()
        if candidate < today - timedelta(days=1):
            candidate = datetime(today.year + 1, month, day).date()
        return candidate

    for phrase, offset in (("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0), ("今日", 0), ("昨天", -1), ("前天", -2)):
        if phrase in compact:
            return today + timedelta(days=offset)

    for phrase, offset in (("day after tomorrow", 2), ("tomorrow", 1), ("today", 0), ("yesterday", -1)):
        if phrase in lowered:
            return today + timedelta(days=offset)

    chinese_weekday_match = re.search(r"(下下|下|本|这)?(?:周|星期)([一二三四五六日天])", compact)
    if chinese_weekday_match:
        prefix = chinese_weekday_match.group(1) or ""
        target_weekday = CHINESE_WEEKDAY_MAP[chinese_weekday_match.group(2)]
        current_weekday = today.weekday()
        if prefix in ("本", "这"):
            delta = target_weekday - current_weekday
        elif prefix == "下":
            delta = target_weekday - current_weekday + 7
        elif prefix == "下下":
            delta = target_weekday - current_weekday + 14
        else:
            delta = target_weekday - current_weekday
            if delta < 0:
                delta += 7
        return today + timedelta(days=delta)

    english_weekday_match = re.search(r"(next|this)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", lowered)
    if english_weekday_match:
        prefix = english_weekday_match.group(1) or ""
        target_weekday = ENGLISH_WEEKDAY_MAP[english_weekday_match.group(2)]
        current_weekday = today.weekday()
        if prefix == "this":
            delta = target_weekday - current_weekday
        elif prefix == "next":
            delta = target_weekday - current_weekday + 7
        else:
            delta = target_weekday - current_weekday
            if delta < 0:
                delta += 7
        return today + timedelta(days=delta)

    return None


def parse_time_from_text(raw_value: str) -> tuple[int, int] | None:
    compact = raw_value.strip().replace(" ", "").replace("：", ":").casefold()

    ampm_match = re.search(r"(?<!\d)(\d{1,2})(?::(\d{1,2}))?\s*(am|pm)(?![a-z])", compact)
    if ampm_match:
        hour = int(ampm_match.group(1))
        minute = int(ampm_match.group(2) or 0)
        ampm = ampm_match.group(3)
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = hour if hour == 12 else hour + 12
        return hour, minute

    period = None
    for marker in ("凌晨", "早上", "早晨", "上午", "中午", "下午", "晚上", "傍晚", "今晚"):
        if marker in compact:
            period = marker
            break

    match = re.search(r"(?<!\d)(\d{1,2})点半", compact)
    if match:
        hour, minute = int(match.group(1)), 30
    else:
        match = re.search(r"(?<!\d)(\d{1,2})点一刻", compact)
        if match:
            hour, minute = int(match.group(1)), 15
        else:
            match = re.search(r"(?<!\d)(\d{1,2})点三刻", compact)
            if match:
                hour, minute = int(match.group(1)), 45
            else:
                match = re.search(r"(?<!\d)(\d{1,2})点(\d{1,2})分?", compact)
                if match:
                    hour, minute = int(match.group(1)), int(match.group(2))
                else:
                    match = re.search(r"(?<!\d)(\d{1,2})点(?!\d)", compact)
                    if match:
                        hour, minute = int(match.group(1)), 0
                    else:
                        match = re.search(r"(?<!\d)(\d{1,2}):(\d{1,2})(?!\d)", compact)
                        if match:
                            hour, minute = int(match.group(1)), int(match.group(2))
                        else:
                            return None

    if period in ("下午", "晚上", "傍晚", "今晚") and 1 <= hour <= 11:
        hour += 12
    elif period == "中午" and 1 <= hour <= 10:
        hour += 12
    elif period in ("凌晨",) and hour == 12:
        hour = 0
    elif period in ("早上", "早晨", "上午") and hour == 12:
        hour = 0

    return hour, minute


def parse_natural_datetime_input(raw_value: str, explicit_time_zone: str | None = None) -> str | None:
    date_value = parse_date_from_text(raw_value, explicit_time_zone)
    time_value = parse_time_from_text(raw_value)
    if date_value is None and time_value is None:
        return None
    if date_value is None:
        zone = resolve_time_zone(explicit_time_zone)
        date_value = datetime.now(zone).date()
    if time_value is None:
        return date_value.strftime("%Y-%m-%d")
    zone = resolve_time_zone(explicit_time_zone)
    target = datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        time_value[0],
        time_value[1],
        tzinfo=zone,
    )
    return format_ticktick_datetime(target)


def normalize_user_datetime_value(raw_value: str | None, explicit_time_zone: str | None = None) -> str | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    explicit = normalize_explicit_datetime_input(text, explicit_time_zone)
    if explicit is not None:
        return explicit
    natural = parse_natural_datetime_input(text, explicit_time_zone)
    if natural is not None:
        return natural
    return raw_value




def parse_schedule_datetime(
    raw_value: str | None,
    explicit_time_zone: str | None = None,
    treat_date_only_as_end: bool = False,
) -> datetime | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    parsed = parse_ticktick_datetime(text)
    if parsed is None:
        return None

    zone = resolve_time_zone(explicit_time_zone)
    if len(text) <= 10:
        local_value = datetime(parsed.year, parsed.month, parsed.day, tzinfo=zone)
        if treat_date_only_as_end:
            local_value += timedelta(days=1)
        return local_value.astimezone(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc)


def format_schedule_datetime(value: datetime | None, explicit_time_zone: str | None = None) -> str | None:
    if value is None:
        return None
    zone = resolve_time_zone(explicit_time_zone)
    return format_ticktick_datetime(value.astimezone(zone))


def serialize_task_datetime(
    raw_value: str | None,
    parsed_value: datetime | None,
    explicit_time_zone: str | None = None,
    all_day: bool = False,
) -> str | None:
    text = str(raw_value or '').strip()
    if not text:
        return None
    if all_day or len(text) <= 10:
        return text
    if parsed_value is None:
        return text
    return format_schedule_datetime(parsed_value, explicit_time_zone)


def resolve_reference_time(raw_value: str | None, explicit_time_zone: str | None = None) -> datetime:
    if raw_value is None or not str(raw_value).strip():
        return now_utc()

    normalized = str(raw_value).strip().casefold()
    if normalized in {'now', '现在', '此刻'}:
        return now_utc()

    normalized_value = normalize_user_datetime_value(str(raw_value), explicit_time_zone)
    parsed = parse_schedule_datetime(normalized_value, explicit_time_zone)
    if parsed is None:
        raise CliError(f"Invalid reference time '{raw_value}'.")
    return parsed


def parse_busy_window(raw_value: str, explicit_time_zone: str | None, label: str) -> BusyWindow:
    if '/' not in raw_value:
        raise CliError("Busy windows must use 'start/end'.")
    start_raw, end_raw = raw_value.split('/', 1)
    start_normalized = normalize_user_datetime_value(start_raw, explicit_time_zone)
    end_normalized = normalize_user_datetime_value(end_raw, explicit_time_zone)
    start = parse_schedule_datetime(start_normalized, explicit_time_zone)
    end = parse_schedule_datetime(
        end_normalized,
        explicit_time_zone,
        treat_date_only_as_end=bool(isinstance(end_normalized, str) and len(end_normalized) <= 10),
    )
    if start is None or end is None:
        raise CliError(f"Invalid busy window '{raw_value}'.")
    if end <= start:
        raise CliError(f"Busy window end must be after start in '{raw_value}'.")
    return BusyWindow(label=label, start=start, end=end, source='busy-window')


def serialize_busy_window(window: BusyWindow, explicit_time_zone: str | None = None) -> dict[str, Any]:
    return {
        'label': window.label,
        'source': window.source,
        'startAt': format_schedule_datetime(window.start, explicit_time_zone),
        'endAt': format_schedule_datetime(window.end, explicit_time_zone),
    }


def build_busy_windows(args: argparse.Namespace, reference_time: datetime) -> list[BusyWindow]:
    windows: list[BusyWindow] = []
    for index, raw_value in enumerate(getattr(args, 'busy_window', []) or [], start=1):
        windows.append(parse_busy_window(raw_value, getattr(args, 'time_zone', None), f'busy-window-{index}'))

    raw_current_task_title = getattr(args, 'current_task_title', None)
    current_task_until = getattr(args, 'current_task_until', None)
    if raw_current_task_title and not current_task_until:
        raise CliError('Provide --current-task-until when using --current-task-title.')
    current_task_title = raw_current_task_title or 'current-task'
    if current_task_until:
        end = parse_schedule_datetime(
            normalize_user_datetime_value(current_task_until, getattr(args, 'time_zone', None)),
            getattr(args, 'time_zone', None),
        )
        if end is None:
            raise CliError(f"Invalid --current-task-until value '{current_task_until}'.")
        if end <= reference_time:
            raise CliError('--current-task-until must be after the reference time.')
        windows.append(BusyWindow(label=current_task_title, start=reference_time, end=end, source='current-task'))

    windows.sort(key=lambda item: (item.start, item.end, item.label))
    return windows


def build_schedule_entry(task: dict[str, Any], default_duration_minutes: int) -> ScheduleEntry:
    time_zone_name = str(task.get('timeZone') or default_time_zone_name())
    all_day = is_task_all_day(task)
    start_raw = str(task.get('startDate') or '')
    due_raw = str(task.get('dueDate') or '')
    start = parse_schedule_datetime(start_raw, time_zone_name)
    end = parse_schedule_datetime(due_raw, time_zone_name, treat_date_only_as_end=all_day)

    if all_day:
        schedule_type = 'all-day'
    elif start is not None and end is not None and end > start:
        schedule_type = 'timed'
    elif start is not None and end is not None and end <= start:
        schedule_type = 'invalid'
    elif start is not None:
        schedule_type = 'start-only'
    elif end is not None:
        schedule_type = 'deadline-only'
    else:
        schedule_type = 'unscheduled'

    duration_minutes: int | None = None
    if schedule_type == 'timed' and start is not None and end is not None:
        duration_minutes = max(int((end - start).total_seconds() // 60), 1)
    elif schedule_type == 'start-only':
        duration_minutes = default_duration_minutes

    return ScheduleEntry(
        task=task,
        task_id=str(task.get('id') or ''),
        project_id=str(task.get('projectId') or ''),
        project_name=str(task.get('projectName') or ''),
        title=str(task.get('title') or task.get('id') or '<unknown task>'),
        start=start,
        end=end,
        deadline=end,
        time_zone=time_zone_name,
        schedule_type=schedule_type,
        duration_minutes=duration_minutes,
        priority=task_priority_value(task),
        all_day=all_day,
    )


def schedule_entry_sort_key(entry: ScheduleEntry) -> tuple[Any, ...]:
    marker = entry.start or entry.deadline
    fallback = datetime.max.replace(tzinfo=timezone.utc)
    return (marker is None, marker or fallback, -entry.priority, entry.title.casefold(), entry.project_name.casefold())


def schedule_entry_within_horizon(entry: ScheduleEntry, reference_time: datetime, days: int | None) -> bool:
    if days is None:
        return True
    marker = entry.start or entry.deadline
    if marker is None:
        return True
    horizon_end = reference_time + timedelta(days=days)
    return marker <= horizon_end or marker <= reference_time


def serialize_schedule_entry(entry: ScheduleEntry, reference_time: datetime) -> dict[str, Any]:
    task = entry.task
    subtasks = task.get('items') if isinstance(task.get('items'), list) else []
    return {
        'id': entry.task_id,
        'projectId': entry.project_id,
        'projectName': entry.project_name,
        'title': entry.title,
        'priority': entry.priority,
        'status': task.get('status'),
        'scheduleType': entry.schedule_type,
        'timeZone': entry.time_zone,
        'isAllDay': entry.all_day,
        'startAt': serialize_task_datetime(task.get('startDate'), entry.start, entry.time_zone, entry.all_day),
        'endAt': serialize_task_datetime(task.get('dueDate'), entry.end, entry.time_zone, entry.all_day),
        'deadlineAt': serialize_task_datetime(task.get('dueDate'), entry.deadline, entry.time_zone, entry.all_day),
        'durationMinutes': entry.duration_minutes,
        'isOverdue': is_task_overdue(task, reference_time),
        'subtaskCount': len([item for item in subtasks if isinstance(item, dict)]),
        'tags': task.get('tags') if isinstance(task.get('tags'), list) else [],
        'rawStartDate': task.get('startDate'),
        'rawDueDate': task.get('dueDate'),
    }


def interval_overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def schedule_entry_matches_queries(entry: ScheduleEntry, queries: list[str]) -> bool:
    if not queries:
        return True
    for query in queries:
        if build_task_search_result(query, entry.task, TASK_SEARCH_FIELDS) is not None:
            return True
    return False


def schedule_entry_is_protected(entry: ScheduleEntry, protected_titles: list[str]) -> bool:
    if not protected_titles:
        return False
    for title in protected_titles:
        if classify_match(title, entry.title) is not None:
            return True
    return False


def build_schedule_analysis(
    entries: list[ScheduleEntry],
    busy_windows: list[BusyWindow],
    reference_time: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    timed_entries = [entry for entry in entries if entry.schedule_type == 'timed' and entry.start is not None and entry.end is not None]
    timed_entries.sort(key=schedule_entry_sort_key)

    for index, entry in enumerate(timed_entries):
        for other in timed_entries[index + 1:]:
            if other.start is None or other.end is None:
                continue
            if other.start >= entry.end:
                break
            if not interval_overlaps(entry.start, entry.end, other.start, other.end):
                continue
            overlap_start = max(entry.start, other.start)
            overlap_end = min(entry.end, other.end)
            conflicts.append({
                'type': 'task-overlap',
                'startAt': format_schedule_datetime(overlap_start, entry.time_zone),
                'endAt': format_schedule_datetime(overlap_end, entry.time_zone),
                'tasks': [
                    {
                        'id': entry.task_id,
                        'projectId': entry.project_id,
                        'projectName': entry.project_name,
                        'title': entry.title,
                        'startAt': serialize_task_datetime(entry.task.get('startDate'), entry.start, entry.time_zone, entry.all_day),
                        'endAt': serialize_task_datetime(entry.task.get('dueDate'), entry.end, entry.time_zone, entry.all_day),
                    },
                    {
                        'id': other.task_id,
                        'projectId': other.project_id,
                        'projectName': other.project_name,
                        'title': other.title,
                        'startAt': serialize_task_datetime(other.task.get('startDate'), other.start, other.time_zone, other.all_day),
                        'endAt': serialize_task_datetime(other.task.get('dueDate'), other.end, other.time_zone, other.all_day),
                    },
                ],
            })

    for entry in timed_entries:
        for window in busy_windows:
            if not interval_overlaps(entry.start, entry.end, window.start, window.end):
                continue
            conflicts.append({
                'type': 'busy-window-overlap',
                'label': window.label,
                'source': window.source,
                'window': serialize_busy_window(window, entry.time_zone),
                'task': {
                    'id': entry.task_id,
                    'projectId': entry.project_id,
                    'projectName': entry.project_name,
                    'title': entry.title,
                    'startAt': serialize_task_datetime(entry.task.get('startDate'), entry.start, entry.time_zone, entry.all_day),
                    'endAt': serialize_task_datetime(entry.task.get('dueDate'), entry.end, entry.time_zone, entry.all_day),
                },
            })

    for entry in entries:
        overdue = is_task_overdue(entry.task, reference_time)
        if entry.schedule_type == 'invalid':
            risks.append({
                'type': 'invalid-time-range',
                'taskId': entry.task_id,
                'projectId': entry.project_id,
                'projectName': entry.project_name,
                'title': entry.title,
                'detail': 'dueDate is not after startDate.',
            })
        elif overdue:
            risks.append({
                'type': 'overdue',
                'taskId': entry.task_id,
                'projectId': entry.project_id,
                'projectName': entry.project_name,
                'title': entry.title,
                'detail': 'Task is overdue relative to the reference time.',
            })
        elif entry.schedule_type == 'deadline-only' and entry.priority >= 3:
            risks.append({
                'type': 'deadline-without-time-block',
                'taskId': entry.task_id,
                'projectId': entry.project_id,
                'projectName': entry.project_name,
                'title': entry.title,
                'detail': 'Task has a due date but no scheduled start block.',
            })
        elif entry.schedule_type == 'start-only':
            risks.append({
                'type': 'start-without-end',
                'taskId': entry.task_id,
                'projectId': entry.project_id,
                'projectName': entry.project_name,
                'title': entry.title,
                'detail': 'Task has a start time but no explicit end time.',
            })
        elif entry.schedule_type == 'unscheduled' and entry.priority >= 3:
            risks.append({
                'type': 'unscheduled-priority-task',
                'taskId': entry.task_id,
                'projectId': entry.project_id,
                'projectName': entry.project_name,
                'title': entry.title,
                'detail': 'High-priority task has no time assignment yet.',
            })

    summary = {
        'taskCount': len(entries),
        'timedTaskCount': len([entry for entry in entries if entry.schedule_type == 'timed']),
        'allDayTaskCount': len([entry for entry in entries if entry.schedule_type == 'all-day']),
        'deadlineOnlyCount': len([entry for entry in entries if entry.schedule_type == 'deadline-only']),
        'startOnlyCount': len([entry for entry in entries if entry.schedule_type == 'start-only']),
        'unscheduledCount': len([entry for entry in entries if entry.schedule_type == 'unscheduled']),
        'conflictCount': len(conflicts),
        'riskCount': len(risks),
        'busyWindowCount': len(busy_windows),
    }
    return conflicts, risks, summary


def round_up_datetime(value: datetime, step_minutes: int) -> datetime:
    if step_minutes <= 1:
        return value.replace(second=0, microsecond=0)
    trimmed = value.replace(second=0, microsecond=0)
    remainder = trimmed.minute % step_minutes
    if remainder == 0 and value.second == 0 and value.microsecond == 0:
        return trimmed
    return trimmed + timedelta(minutes=(step_minutes - remainder) % step_minutes or step_minutes)


def first_overlapping_window(start: datetime, end: datetime, windows: list[BusyWindow]) -> BusyWindow | None:
    for window in sorted(windows, key=lambda item: (item.start, item.end, item.label)):
        if interval_overlaps(start, end, window.start, window.end):
            return window
    return None


def make_entry_window(entry: ScheduleEntry, start: datetime, end: datetime, source: str) -> BusyWindow:
    label = f"{entry.title} @ {entry.project_name}" if entry.project_name else entry.title
    return BusyWindow(label=label, start=start, end=end, source=source)


def find_next_available_slot(
    candidate_start: datetime,
    duration_minutes: int,
    occupied_windows: list[BusyWindow],
    search_end: datetime,
    step_minutes: int,
) -> tuple[datetime, datetime] | None:
    current_start = round_up_datetime(candidate_start, step_minutes)
    duration = timedelta(minutes=duration_minutes)
    windows = sorted(occupied_windows, key=lambda item: (item.start, item.end, item.label))
    while current_start + duration <= search_end:
        current_end = current_start + duration
        overlap = first_overlapping_window(current_start, current_end, windows)
        if overlap is None:
            return current_start, current_end
        current_start = round_up_datetime(max(current_start + timedelta(minutes=step_minutes), overlap.end), step_minutes)
    return None


def propose_rebalanced_schedule(
    entries: list[ScheduleEntry],
    busy_windows: list[BusyWindow],
    reference_time: datetime,
    search_horizon_days: int,
    step_minutes: int,
    task_queries: list[str],
    protected_titles: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    timed_entries = [
        entry
        for entry in sorted(entries, key=schedule_entry_sort_key)
        if entry.schedule_type == 'timed' and entry.start is not None and entry.end is not None and entry.duration_minutes is not None
    ]
    movable_ids = {
        entry.task_id
        for entry in timed_entries
        if schedule_entry_matches_queries(entry, task_queries) and not schedule_entry_is_protected(entry, protected_titles)
    }

    fixed_windows = list(busy_windows)
    for entry in timed_entries:
        if entry.task_id in movable_ids:
            continue
        if entry.end is not None and entry.end > reference_time:
            fixed_windows.append(make_entry_window(entry, entry.start, entry.end, 'fixed-task'))

    proposals: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    placed_windows: list[BusyWindow] = []
    search_end = reference_time + timedelta(days=search_horizon_days)

    for entry in timed_entries:
        if entry.task_id not in movable_ids:
            continue
        if entry.end is not None and entry.end <= reference_time:
            continue

        occupied_windows = fixed_windows + placed_windows
        overlap = first_overlapping_window(entry.start, entry.end, occupied_windows)
        starts_in_past = entry.start < reference_time
        if overlap is None and not starts_in_past:
            placed_windows.append(make_entry_window(entry, entry.start, entry.end, 'kept-task'))
            continue

        candidate_start = entry.start
        reason = 'schedule optimization'
        if overlap is not None:
            candidate_start = max(candidate_start, overlap.end)
            reason = f"overlaps {overlap.source}: {overlap.label}"
        if starts_in_past:
            candidate_start = max(candidate_start, reference_time)
            if overlap is None:
                reason = 'starts before the reference time'

        slot = find_next_available_slot(
            candidate_start=candidate_start,
            duration_minutes=entry.duration_minutes,
            occupied_windows=occupied_windows,
            search_end=search_end,
            step_minutes=step_minutes,
        )
        if slot is None:
            skipped.append({
                'taskId': entry.task_id,
                'projectId': entry.project_id,
                'projectName': entry.project_name,
                'title': entry.title,
                'reason': 'No available slot found within the search horizon.',
            })
            placed_windows.append(make_entry_window(entry, entry.start, entry.end, 'unchanged-task'))
            continue

        new_start, new_end = slot
        if new_start == entry.start and new_end == entry.end:
            placed_windows.append(make_entry_window(entry, entry.start, entry.end, 'kept-task'))
            continue

        proposals.append({
            'taskId': entry.task_id,
            'projectId': entry.project_id,
            'projectName': entry.project_name,
            'title': entry.title,
            'timeZone': entry.time_zone,
            'oldStartDate': serialize_task_datetime(entry.task.get('startDate'), entry.start, entry.time_zone, entry.all_day),
            'oldDueDate': serialize_task_datetime(entry.task.get('dueDate'), entry.end, entry.time_zone, entry.all_day),
            'newStartDate': format_schedule_datetime(new_start, entry.time_zone),
            'newDueDate': format_schedule_datetime(new_end, entry.time_zone),
            'durationMinutes': entry.duration_minutes,
            'reason': reason,
        })
        placed_windows.append(make_entry_window(entry, new_start, new_end, 'proposed-task'))

    return proposals, skipped

def task_priority_value(task: dict[str, Any]) -> int:
    value = task.get("priority")
    return int(value) if isinstance(value, int) else 0


def task_sort_key(task: dict[str, Any]) -> tuple[Any, ...]:
    due_value = str(task.get("dueDate") or "")
    return (due_value == "", due_value, -task_priority_value(task), str(task.get("title", "")).casefold())


def is_task_all_day(task: dict[str, Any]) -> bool:
    return bool(task.get("isAllDay"))


def is_task_overdue(task: dict[str, Any], reference: datetime | None = None) -> bool:
    reference_time = reference or now_utc()
    due_raw = str(task.get("dueDate") or "")
    due_time = parse_ticktick_datetime(due_raw)
    if due_time is None:
        return False
    if is_task_all_day(task) or len(due_raw) <= 10:
        return due_time.date() < reference_time.date()
    if due_time.tzinfo is None:
        return due_time < reference_time.replace(tzinfo=None)
    return due_time.astimezone(timezone.utc) < reference_time


def is_task_due_in_days(task: dict[str, Any], days: int, reference: datetime | None = None) -> bool:
    reference_time = reference or now_utc()
    due_time = parse_ticktick_datetime(str(task.get("dueDate") or ""))
    if due_time is None:
        return False
    return due_time.date() == (reference_time + timedelta(days=days)).date()


def is_task_due_within_days(task: dict[str, Any], days: int, reference: datetime | None = None) -> bool:
    reference_time = reference or now_utc()
    due_time = parse_ticktick_datetime(str(task.get("dueDate") or ""))
    if due_time is None:
        return False
    due_date = due_time.date()
    start_date = reference_time.date()
    end_date = (reference_time + timedelta(days=days)).date()
    return start_date <= due_date <= end_date


def list_projects(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    include_closed_projects: bool = False,
) -> list[dict[str, Any]]:
    response = api_request(args, region, token_path, "GET", "/project")
    projects = response if isinstance(response, list) else []
    result: list[dict[str, Any]] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        if project.get("closed") and not include_closed_projects:
            continue
        result.append(project)
    return result


def summarize_project(project: dict[str, Any]) -> str:
    return str(project.get("name") or project.get("id") or "<unknown project>")


def summarize_task(task: dict[str, Any]) -> str:
    title = str(task.get("title") or task.get("id") or "<unknown task>")
    project_name = str(task.get("projectName") or "")
    return f"{title} @ {project_name}" if project_name else title


def summarize_subtask(item: dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("id") or "<unknown subtask>")
    parent_title = str(item.get("parentTaskTitle") or "")
    return f"{title} under {parent_title}" if parent_title else title


def summarize_matches(matches: list[dict[str, Any]], summarize) -> str:
    preview = ", ".join(summarize(item) for item in matches[:5])
    if len(matches) > 5:
        preview += ", ..."
    return preview


def choose_single_match(
    matches: list[dict[str, Any]],
    query: str,
    kind: str,
    summarize,
) -> dict[str, Any]:
    if not matches:
        raise CliError(f"No {kind} match found for '{query}'.")
    if len(matches) == 1:
        return matches[0]

    exact_matches = [item for item in matches if str(item.get("matchType", "")) == "exact"]
    if len(exact_matches) == 1:
        return exact_matches[0]

    first_rank = match_rank(str(matches[0].get("matchType", "")))
    second_rank = match_rank(str(matches[1].get("matchType", "")))
    if first_rank < second_rank:
        return matches[0]

    raise CliError(f"Ambiguous {kind} match for '{query}': {summarize_matches(matches, summarize)}")


def find_project_matches(projects: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for project in projects:
        name = str(project.get("name", ""))
        match_type = classify_match(query, name)
        if match_type is None:
            continue
        project_copy = dict(project)
        project_copy["matchType"] = match_type
        matches.append(project_copy)
    matches.sort(key=lambda item: (match_rank(str(item.get("matchType", ""))), str(item.get("name", "")).casefold()))
    return matches


def resolve_project_selection(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    project_id: str | None = None,
    project_name: str | None = None,
    include_closed_projects: bool = False,
) -> tuple[str | None, str | None]:
    if project_id:
        project_lookup = list_projects(args, region, token_path, include_closed_projects=True)
        for project in project_lookup:
            if str(project.get("id", "")) == project_id:
                return project_id, str(project.get("name", ""))
        return project_id, None

    if not project_name:
        return None, None

    projects = list_projects(
        args,
        region,
        token_path,
        include_closed_projects=include_closed_projects,
    )
    matches = find_project_matches(projects, project_name)
    match = choose_single_match(matches, project_name, "project", summarize_project)
    return str(match.get("id") or ""), str(match.get("name") or "")


def collect_tasks(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    project_id: str | None = None,
    project_name: str | None = None,
    include_completed: bool = False,
    include_closed_projects: bool = False,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    selected_project: dict[str, Any] | None = None
    target_projects: list[dict[str, Any]] = []

    if project_id or project_name:
        resolved_project_id, resolved_project_name = resolve_project_selection(
            args,
            region,
            token_path,
            project_id=project_id,
            project_name=project_name,
            include_closed_projects=include_closed_projects,
        )
        if not resolved_project_id:
            return None, []
        selected_project = {"id": resolved_project_id, "name": resolved_project_name or ""}
        target_projects = [selected_project]
    else:
        target_projects = list_projects(
            args,
            region,
            token_path,
            include_closed_projects=include_closed_projects,
        )

    tasks: list[dict[str, Any]] = []
    for project in target_projects:
        project_id_value = project.get("id")
        if not isinstance(project_id_value, str) or not project_id_value:
            continue
        data = api_request(args, region, token_path, "GET", f"/project/{project_id_value}/data")
        if not isinstance(data, dict):
            continue

        project_doc = data.get("project")
        if selected_project and isinstance(project_doc, dict):
            selected_project = dict(project_doc)

        project_name_value = str(project_doc.get("name", "")) if isinstance(project_doc, dict) else str(project.get("name", ""))
        for task in data.get("tasks", []):
            if not isinstance(task, dict):
                continue
            if task.get("status") == 2 and not include_completed:
                continue
            task_copy = dict(task)
            task_copy["projectName"] = project_name_value
            tasks.append(task_copy)

    tasks.sort(key=task_sort_key)
    return selected_project, tasks


def build_task_search_result(
    query: str,
    task: dict[str, Any],
    search_fields: tuple[str, ...],
) -> dict[str, Any] | None:
    matched_fields: list[dict[str, str]] = []

    def consider(field_name: str, candidate: str | None) -> None:
        match_type = classify_match(query, candidate)
        if match_type is None:
            return
        matched_fields.append({"field": field_name, "matchType": match_type})

    if "title" in search_fields:
        consider("title", str(task.get("title", "")))
    if "content" in search_fields:
        consider("content", str(task.get("content", "")))
    if "desc" in search_fields:
        consider("desc", str(task.get("desc", "")))
    if "project" in search_fields:
        consider("project", str(task.get("projectName", "")))
    if "tag" in search_fields:
        for tag in task.get("tags", []):
            if isinstance(tag, str):
                consider("tag", tag)
    if "subtask" in search_fields:
        items = task.get("items") if isinstance(task.get("items"), list) else []
        for item in items:
            if isinstance(item, dict):
                consider("subtask", str(item.get("title", "")))

    if not matched_fields:
        return None

    best_match = min(matched_fields, key=lambda item: match_rank(item["matchType"]))
    task_copy = dict(task)
    task_copy["matchType"] = best_match["matchType"]
    task_copy["matchedFields"] = matched_fields
    return task_copy


def search_tasks_in_collection(
    query: str,
    tasks: list[dict[str, Any]],
    search_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for task in tasks:
        result = build_task_search_result(query, task, search_fields)
        if result is not None:
            matches.append(result)

    matches.sort(key=lambda item: (
        match_rank(str(item.get("matchType", ""))),
        str(item.get("projectName", "")).casefold(),
        str(item.get("title", "")).casefold(),
    ))
    return matches


def resolve_task_selection(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    task_title: str,
    project_id: str | None = None,
    project_name: str | None = None,
    include_completed: bool = False,
    include_closed_projects: bool = False,
) -> dict[str, Any]:
    _, tasks = collect_tasks(
        args,
        region,
        token_path,
        project_id=project_id,
        project_name=project_name,
        include_completed=include_completed,
        include_closed_projects=include_closed_projects,
    )
    matches = search_tasks_in_collection(task_title, tasks, ("title",))
    return choose_single_match(matches, task_title, "task", summarize_task)


def search_subtasks_in_task(query: str, task: dict[str, Any]) -> list[dict[str, Any]]:
    items = task.get("items") if isinstance(task.get("items"), list) else []
    matches: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        match_type = classify_match(query, str(item.get("title", "")))
        if match_type is None:
            continue
        item_copy = dict(item)
        item_copy["matchType"] = match_type
        item_copy["parentTaskId"] = task.get("id")
        item_copy["parentTaskTitle"] = task.get("title")
        item_copy["projectId"] = task.get("projectId")
        item_copy["projectName"] = task.get("projectName", "")
        matches.append(item_copy)

    matches.sort(key=lambda item: (
        match_rank(str(item.get("matchType", ""))),
        str(item.get("title", "")).casefold(),
    ))
    return matches


def find_existing_subtask_item(items: list[dict[str, Any]], match: dict[str, Any]) -> dict[str, Any] | None:
    match_id = str(match.get("id") or "")
    if match_id:
        for item in items:
            if str(item.get("id", "")) == match_id:
                return item

    match_title = str(match.get("title") or "")
    for item in items:
        if str(item.get("title", "")) == match_title:
            return item
    return None


def resolve_parent_task(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    project_id: str | None = None,
    project_name: str | None = None,
    task_id: str | None = None,
    parent_task_title: str | None = None,
    include_completed: bool = False,
    include_closed_projects: bool = False,
) -> dict[str, Any]:
    resolved_project_id, resolved_project_name = resolve_project_selection(
        args,
        region,
        token_path,
        project_id=project_id,
        project_name=project_name,
        include_closed_projects=include_closed_projects,
    )

    if task_id:
        if not resolved_project_id:
            raise CliError("Provide --project-id or --project-name when using --task-id.")
        task = fetch_task(args, region, token_path, resolved_project_id, task_id)
        task["projectName"] = resolved_project_name or task.get("projectName") or ""
        return task

    if not parent_task_title:
        raise CliError("Provide --task-id with project context or --parent-task-title.")

    match = resolve_task_selection(
        args,
        region,
        token_path,
        parent_task_title,
        project_id=resolved_project_id,
        project_name=resolved_project_name,
        include_completed=include_completed,
        include_closed_projects=include_closed_projects,
    )
    task = fetch_task(args, region, token_path, str(match.get("projectId")), str(match.get("id")))
    task["projectName"] = str(match.get("projectName", ""))
    return task


def resolve_subtask_selection(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    subtask_title: str,
    project_id: str | None = None,
    project_name: str | None = None,
    task_id: str | None = None,
    parent_task_title: str | None = None,
    include_completed: bool = False,
    include_closed_projects: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    parent_task = resolve_parent_task(
        args,
        region,
        token_path,
        project_id=project_id,
        project_name=project_name,
        task_id=task_id,
        parent_task_title=parent_task_title,
        include_completed=include_completed,
        include_closed_projects=include_closed_projects,
    )
    matches = search_subtasks_in_task(subtask_title, parent_task)
    match = choose_single_match(matches, subtask_title, "subtask", summarize_subtask)
    return parent_task, match


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

    redirect_uri_info = classify_redirect_uri(redirect_uri)
    is_localhost_callback = redirect_uri_info['mode'] == 'localhost'

    emit(
        {
            "ok": True,
            "region": region.name,
            "scope": scope,
            "redirect_uri": redirect_uri,
            "redirect_uri_analysis": redirect_uri_info,
            "authorization_url": auth_url,
            "state": state,
            "state_file": str(state_path),
            "callback_capture_mode": "manual_copy" if is_localhost_callback else "browser_redirect",
            "next_steps": [
                "Open authorization_url in a local browser and approve access.",
                (
                    "After approval, the browser may fail to open the localhost callback page. Copy the full callback URL from the address bar."
                    if is_localhost_callback
                    else "After approval, copy the full callback URL from the browser."
                ),
                "Run auth-exchange on the cloud host with --callback-url set to that full URL.",
            ],
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

    if args.callback_url and not callback_url_matches_redirect_uri(args.callback_url, redirect_uri):
        raise CliError(
            f"Callback URL does not match redirect URI. Expected base '{redirect_uri}'. Copy the full browser callback URL and keep TICKTICK_REDIRECT_URI unchanged."
        )

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




def command_doctor(
    args: argparse.Namespace,
    region: RegionConfig,
    token_path: Path,
    state_path: Path,
) -> None:
    client_id = getattr(args, 'client_id', None) or os.getenv('TICKTICK_CLIENT_ID')
    client_secret = getattr(args, 'client_secret', None) or os.getenv('TICKTICK_CLIENT_SECRET')
    redirect_uri = getattr(args, 'redirect_uri', None) or os.getenv('TICKTICK_REDIRECT_URI')
    redirect_uri_info = classify_redirect_uri(redirect_uri)

    token_source = value_source(getattr(args, 'token_path', None), 'TICKTICK_TOKEN_PATH')
    state_source = value_source(getattr(args, 'state_path', None), 'TICKTICK_STATE_PATH')
    region_source = value_source(getattr(args, 'region', None), 'TICKTICK_REGION')

    token_path_info = build_path_diagnostic(token_path, token_source)
    state_path_info = build_path_diagnostic(state_path, state_source)
    token_info = inspect_token_file(token_path, region)

    issues: list[str] = []
    recommendations: list[str] = []

    if not client_id:
        issues.append('Missing TICKTICK_CLIENT_ID or --client-id.')
    if not client_secret:
        issues.append('Missing TICKTICK_CLIENT_SECRET or --client-secret.')
    if not redirect_uri:
        issues.append('Missing TICKTICK_REDIRECT_URI or --redirect-uri.')
    elif redirect_uri_info['mode'] == 'invalid':
        issues.append(f"Redirect URI is not a valid absolute URL: {redirect_uri}")
    if not token_path_info['parentWritable']:
        issues.append(f"Token directory is not writable: {token_path_info['parentPath']}")
    if not state_path_info['parentWritable']:
        issues.append(f"State directory is not writable: {state_path_info['parentPath']}")
    if not token_info['exists']:
        issues.append('Token file does not exist yet. Run auth-url and auth-exchange.')
    elif not token_info['valid']:
        issues.append(token_info['error'] or 'Token file is invalid.')
    elif token_info['regionMatchesCommand'] is False:
        issues.append(
            f"Token region '{token_info.get('tokenRegion')}' does not match command region '{region.name}'."
        )

    if token_source == 'default':
        recommendations.append(
            'If your cloud deployment has a dedicated persistent volume, set TICKTICK_TOKEN_PATH explicitly to that path.'
        )
    if state_source == 'default':
        recommendations.append(
            'Set TICKTICK_STATE_PATH explicitly if you want OAuth state files stored beside your persistent token path.'
        )
    if not redirect_uri:
        recommendations.append(
            f'For headless cloud OAuth without a public callback, set TICKTICK_REDIRECT_URI to {DEFAULT_LOCALHOST_REDIRECT_URI} and register the same value in the Dida/TickTick developer console.'
        )
    elif redirect_uri_info['mode'] == 'localhost':
        recommendations.append(
            'Localhost redirect detected. Run auth-url on the cloud host, open authorization_url in a local browser, then copy the full localhost callback URL from the address bar into auth-exchange.'
        )
    if token_info['exists'] and token_info['valid'] and token_info['needsRefresh']:
        recommendations.append('Token is near expiry. Run token-status --auto-refresh or re-authenticate if refresh fails.')
    if not token_info['exists'] and client_id and client_secret and redirect_uri:
        if redirect_uri_info['mode'] == 'localhost':
            recommendations.append('Credential variables are present. Next step: run auth-url, approve access in a local browser, copy the full localhost callback URL, then run auth-exchange.')
        else:
            recommendations.append('Credential variables are present. Next step: run auth-url, approve access, then run auth-exchange.')

    api_check: dict[str, Any] | None = None
    if args.check_api:
        api_check = {'attempted': True, 'ok': False, 'autoRefresh': bool(args.auto_refresh)}
        try:
            token_data = get_access_token(args, region, token_path, allow_refresh=args.auto_refresh)
            projects = api_request(args, region, token_path, 'GET', '/project')
            api_check['ok'] = True
            api_check['tokenRegion'] = token_data.get('region')
            api_check['projectCount'] = len(projects) if isinstance(projects, list) else None
        except CliError as exc:
            api_check['error'] = str(exc)
            issues.append(f"API check failed: {exc}")

    emit(
        {
            'ok': len(issues) == 0,
            'region': region.name,
            'regionSource': region_source,
            'pythonVersion': sys.version.split()[0],
            'homePath': str(Path.home()),
            'environment': {
                'clientIdPresent': bool(client_id),
                'clientIdSource': value_source(getattr(args, 'client_id', None), 'TICKTICK_CLIENT_ID'),
                'clientSecretPresent': bool(client_secret),
                'clientSecretSource': value_source(getattr(args, 'client_secret', None), 'TICKTICK_CLIENT_SECRET'),
                'redirectUriPresent': bool(redirect_uri),
                'redirectUriSource': value_source(getattr(args, 'redirect_uri', None), 'TICKTICK_REDIRECT_URI'),
                'redirectUri': redirect_uri,
                'redirectUriAnalysis': redirect_uri_info,
            },
            'paths': {
                'token': token_path_info,
                'state': state_path_info,
            },
            'token': token_info,
            'apiCheck': api_check,
            'issues': issues,
            'recommendations': recommendations,
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


def command_project_find(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    response = api_request(args, region, token_path, "GET", "/project")
    projects = response if isinstance(response, list) else []
    matches: list[dict[str, Any]] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        name = str(project.get("name", ""))
        match_type = classify_match(args.name, name)
        if match_type is None:
            continue
        project_copy = dict(project)
        project_copy["matchType"] = match_type
        matches.append(project_copy)

    matches.sort(key=lambda item: (match_rank(str(item.get("matchType", ""))), str(item.get("name", "")).casefold()))
    emit({"ok": True, "query": args.name, "count": len(matches), "projects": matches})


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
    project, tasks = collect_tasks(
        args,
        region,
        token_path,
        project_id=args.project_id,
        project_name=getattr(args, "project_name", None),
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    if args.limit and args.limit > 0:
        tasks = tasks[: args.limit]

    emit(
        {
            "ok": True,
            "project": project,
            "count": len(tasks),
            "tasks": tasks,
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

    normalized_due_date = normalize_user_datetime_value(args.due_date, args.time_zone)
    normalized_start_date = normalize_user_datetime_value(args.start_date, args.time_zone)

    mapping = {
        "title": args.title,
        "content": args.content,
        "desc": args.desc,
        "dueDate": normalized_due_date,
        "startDate": normalized_start_date,
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
    project_id, project_name = resolve_project_selection(
        args,
        region,
        token_path,
        project_id=args.project_id,
        project_name=getattr(args, "project_name", None),
    )
    payload = build_task_payload(args, include_identity=False)
    payload["projectId"] = project_id or "inbox"
    task = api_request(args, region, token_path, "POST", "/task", json_body=payload)
    emit(
        {
            "ok": True,
            "task": task,
            "resolved_project_id": payload["projectId"],
            "resolved_project_name": project_name or ("Inbox" if payload["projectId"] == "inbox" else None),
        }
    )


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


def command_task_find(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    _, tasks = collect_tasks(
        args,
        region,
        token_path,
        project_id=args.project_id,
        project_name=getattr(args, "project_name", None),
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    matches = search_tasks_in_collection(args.title, tasks, ("title",))
    if args.limit and args.limit > 0:
        matches = matches[: args.limit]
    emit({"ok": True, "query": args.title, "count": len(matches), "tasks": matches})


def command_task_search(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    _, tasks = collect_tasks(
        args,
        region,
        token_path,
        project_id=args.project_id,
        project_name=getattr(args, "project_name", None),
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    search_fields = tuple(args.field) if args.field else TASK_SEARCH_FIELDS
    matches = search_tasks_in_collection(args.query, tasks, search_fields)
    if args.limit and args.limit > 0:
        matches = matches[: args.limit]
    emit({"ok": True, "query": args.query, "fields": list(search_fields), "count": len(matches), "tasks": matches})


def command_task_smart_update(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    match = resolve_task_selection(
        args,
        region,
        token_path,
        args.task_title,
        project_id=args.project_id,
        project_name=args.project_name,
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    update_args = argparse.Namespace(
        task_id=str(match.get("id")),
        project_id=str(match.get("projectId")),
        title=args.title,
        content=args.content,
        desc=args.desc,
        priority=args.priority,
        due_date=args.due_date,
        start_date=args.start_date,
        time_zone=args.time_zone,
        all_day=args.all_day,
        tags=args.tags,
        repeat_flag=args.repeat_flag,
        reminders=args.reminders,
        subtask=None,
        clear_due_date=args.clear_due_date,
        clear_start_date=args.clear_start_date,
    )
    payload = build_task_payload(update_args, include_identity=True)
    if len(payload.keys()) <= 2:
        raise CliError("No update fields provided.")
    task = api_request(args, region, token_path, "POST", f"/task/{match['id']}", json_body=payload)
    emit({
        "ok": True,
        "matched_task": {
            "id": match.get("id"),
            "projectId": match.get("projectId"),
            "title": match.get("title"),
            "projectName": match.get("projectName"),
        },
        "task": task,
    })


def command_task_smart_complete(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    match = resolve_task_selection(
        args,
        region,
        token_path,
        args.task_title,
        project_id=args.project_id,
        project_name=args.project_name,
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    api_request(
        args,
        region,
        token_path,
        "POST",
        f"/project/{match['projectId']}/task/{match['id']}/complete",
        expected_statuses=(200, 201),
    )
    emit({
        "ok": True,
        "matched_task": {
            "id": match.get("id"),
            "projectId": match.get("projectId"),
            "title": match.get("title"),
            "projectName": match.get("projectName"),
        },
        "completed_task_id": match.get("id"),
        "project_id": match.get("projectId"),
    })


def command_task_smart_delete(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    match = resolve_task_selection(
        args,
        region,
        token_path,
        args.task_title,
        project_id=args.project_id,
        project_name=args.project_name,
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    api_request(
        args,
        region,
        token_path,
        "DELETE",
        f"/project/{match['projectId']}/task/{match['id']}",
        expected_statuses=(200, 201),
    )
    emit({
        "ok": True,
        "matched_task": {
            "id": match.get("id"),
            "projectId": match.get("projectId"),
            "title": match.get("title"),
            "projectName": match.get("projectName"),
        },
        "deleted_task_id": match.get("id"),
        "project_id": match.get("projectId"),
    })


def command_tasks_due(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    if args.days is not None and args.days < 0:
        raise CliError("--days must be 0 or greater.")

    project, tasks = collect_tasks(
        args,
        region,
        token_path,
        project_id=args.project_id,
        project_name=getattr(args, "project_name", None),
        include_completed=False,
        include_closed_projects=args.include_closed_projects,
    )
    reference_time = now_utc()

    if args.days is not None:
        mode = f"in-{args.days}-days"
        filtered = [task for task in tasks if is_task_due_in_days(task, args.days, reference_time)]
    elif args.when == "today":
        mode = "today"
        filtered = [task for task in tasks if is_task_due_in_days(task, 0, reference_time)]
    elif args.when == "tomorrow":
        mode = "tomorrow"
        filtered = [task for task in tasks if is_task_due_in_days(task, 1, reference_time)]
    elif args.when == "this-week":
        mode = "this-week"
        filtered = [task for task in tasks if is_task_due_within_days(task, 7, reference_time)]
    elif args.when == "overdue":
        mode = "overdue"
        filtered = [task for task in tasks if is_task_overdue(task, reference_time)]
    else:
        raise CliError("Provide --when or --days.")

    filtered.sort(key=task_sort_key)
    if args.limit and args.limit > 0:
        filtered = filtered[: args.limit]
    emit({"ok": True, "project": project, "mode": mode, "count": len(filtered), "tasks": filtered})


def command_tasks_focus(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    project, tasks = collect_tasks(
        args,
        region,
        token_path,
        project_id=args.project_id,
        project_name=getattr(args, "project_name", None),
        include_completed=False,
        include_closed_projects=args.include_closed_projects,
    )
    reference_time = now_utc()

    if args.mode == "engaged":
        filtered = [
            task for task in tasks
            if task_priority_value(task) == 5 or is_task_due_in_days(task, 0, reference_time) or is_task_overdue(task, reference_time)
        ]
    elif args.mode == "next":
        filtered = [
            task for task in tasks
            if task_priority_value(task) == 3 or is_task_due_in_days(task, 1, reference_time)
        ]
    else:
        raise CliError("Invalid focus mode.")

    filtered.sort(key=lambda task: (-task_priority_value(task),) + task_sort_key(task))
    if args.limit and args.limit > 0:
        filtered = filtered[: args.limit]
    emit({"ok": True, "project": project, "mode": args.mode, "count": len(filtered), "tasks": filtered})


def command_tasks_batch_create(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    document = parse_json_document(args.json, args.json_file)
    if not isinstance(document, list):
        raise CliError("Batch input must be a JSON array of task objects.")

    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    project_cache: dict[str, tuple[str | None, str | None]] = {}

    for index, item in enumerate(document, start=1):
        if not isinstance(item, dict):
            failed.append({"index": index, "error": "Each batch item must be a JSON object."})
            continue

        title = str(item.get("title") or "").strip()
        if not title:
            failed.append({"index": index, "error": "Missing required field 'title'."})
            continue

        project_id_value = item.get("projectId") or item.get("project_id")
        project_name_value = item.get("projectName") or item.get("project_name")

        try:
            resolved_project_id: str | None = str(project_id_value).strip() if isinstance(project_id_value, str) and project_id_value.strip() else None
            resolved_project_name: str | None = None
            if isinstance(project_name_value, str) and project_name_value.strip():
                cache_key = project_name_value.strip().casefold()
                if cache_key not in project_cache:
                    project_cache[cache_key] = resolve_project_selection(
                        args,
                        region,
                        token_path,
                        project_id=resolved_project_id,
                        project_name=project_name_value.strip(),
                    )
                resolved_project_id, resolved_project_name = project_cache[cache_key]
            elif resolved_project_id:
                resolved_project_id, resolved_project_name = resolve_project_selection(
                    args,
                    region,
                    token_path,
                    project_id=resolved_project_id,
                )

            payload: dict[str, Any] = {
                "title": title,
                "projectId": resolved_project_id or "inbox",
            }
            item_time_zone = item.get("timeZone") or item.get("time_zone")
            if item.get("content") is not None:
                payload["content"] = item.get("content")
            if item.get("desc") is not None:
                payload["desc"] = item.get("desc")
            normalized_due_date = normalize_user_datetime_value(
                item.get("dueDate") or item.get("due_date"),
                item_time_zone if isinstance(item_time_zone, str) else None,
            )
            if normalized_due_date is not None:
                payload["dueDate"] = normalized_due_date
            normalized_start_date = normalize_user_datetime_value(
                item.get("startDate") or item.get("start_date"),
                item_time_zone if isinstance(item_time_zone, str) else None,
            )
            if normalized_start_date is not None:
                payload["startDate"] = normalized_start_date
            if isinstance(item_time_zone, str) and item_time_zone.strip():
                payload["timeZone"] = item_time_zone.strip()
            repeat_flag = item.get("repeatFlag") or item.get("repeat_flag")
            if repeat_flag is not None:
                payload["repeatFlag"] = repeat_flag

            priority = item.get("priority")
            if priority is not None:
                payload["priority"] = priority

            all_day = item.get("isAllDay")
            if all_day is None:
                all_day = item.get("allDay")
            if all_day is None:
                all_day = item.get("all_day")
            if all_day:
                payload["isAllDay"] = True

            tags = item.get("tags")
            if isinstance(tags, str):
                payload["tags"] = [tag.strip() for tag in tags.split(",") if tag.strip()]
            elif isinstance(tags, list):
                payload["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]

            reminders = item.get("reminders")
            if isinstance(reminders, str):
                payload["reminders"] = [value.strip() for value in reminders.split(",") if value.strip()]
            elif isinstance(reminders, list):
                payload["reminders"] = [str(value).strip() for value in reminders if str(value).strip()]

            items_value = item.get("items")
            subtasks = item.get("subtasks")
            if isinstance(items_value, list):
                payload["items"] = [subtask for subtask in items_value if isinstance(subtask, dict)]
            elif isinstance(subtasks, list):
                payload["items"] = [{"title": str(value).strip()} for value in subtasks if str(value).strip()]

            task = api_request(args, region, token_path, "POST", "/task", json_body=payload)
            created.append({
                "index": index,
                "title": title,
                "projectId": payload["projectId"],
                "projectName": resolved_project_name or ("Inbox" if payload["projectId"] == "inbox" else None),
                "task": task,
            })
        except CliError as exc:
            failed.append({"index": index, "title": title, "error": str(exc)})

    emit({
        "ok": len(failed) == 0,
        "createdCount": len(created),
        "failedCount": len(failed),
        "created": created,
        "failed": failed,
    })


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
    project_ids = list(args.project_id or [])
    if getattr(args, "project_name", None):
        resolved_project_id, _ = resolve_project_selection(args, region, token_path, project_name=args.project_name)
        if resolved_project_id:
            project_ids.append(resolved_project_id)
    if project_ids:
        payload["projectIds"] = project_ids
    normalized_start_date = normalize_user_datetime_value(args.start_date)
    normalized_end_date = normalize_user_datetime_value(args.end_date)
    if normalized_start_date:
        payload["startDate"] = normalized_start_date
    if normalized_end_date:
        payload["endDate"] = normalized_end_date

    response = api_request(args, region, token_path, "POST", "/task/completed", json_body=payload)
    tasks = response if isinstance(response, list) else []
    emit({"ok": True, "count": len(tasks), "tasks": tasks})


def command_tasks_filter(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    payload: dict[str, Any] = {}
    project_ids = list(args.project_id or [])
    if getattr(args, "project_name", None):
        resolved_project_id, _ = resolve_project_selection(args, region, token_path, project_name=args.project_name)
        if resolved_project_id:
            project_ids.append(resolved_project_id)
    if project_ids:
        payload["projectIds"] = project_ids
    normalized_start_date = normalize_user_datetime_value(args.start_date)
    normalized_end_date = normalize_user_datetime_value(args.end_date)
    if normalized_start_date:
        payload["startDate"] = normalized_start_date
    if normalized_end_date:
        payload["endDate"] = normalized_end_date
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
    normalized_start_date = normalize_user_datetime_value(args.start_date, args.time_zone)
    if normalized_start_date:
        new_item["startDate"] = normalized_start_date
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
    normalized_start_date = normalize_user_datetime_value(args.start_date, args.time_zone) if args.start_date is not None else None
    if args.start_date is not None:
        target["startDate"] = normalized_start_date
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


def command_subtask_find(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    parent_task = resolve_parent_task(
        args,
        region,
        token_path,
        project_id=args.project_id,
        project_name=args.project_name,
        task_id=args.task_id,
        parent_task_title=args.parent_task_title,
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    matches = search_subtasks_in_task(args.subtask_title, parent_task)
    if args.limit and args.limit > 0:
        matches = matches[: args.limit]
    emit({
        "ok": True,
        "query": args.subtask_title,
        "parentTask": {
            "id": parent_task.get("id"),
            "projectId": parent_task.get("projectId"),
            "title": parent_task.get("title"),
            "projectName": parent_task.get("projectName"),
        },
        "count": len(matches),
        "subtasks": matches,
    })


def command_subtask_smart_add(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    parent_task = resolve_parent_task(
        args,
        region,
        token_path,
        project_id=args.project_id,
        project_name=args.project_name,
        task_id=args.task_id,
        parent_task_title=args.parent_task_title,
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    existing_items = parent_task.get("items") if isinstance(parent_task.get("items"), list) else []
    items = [clean_subtask_item(item) for item in existing_items if isinstance(item, dict)]

    new_item: dict[str, Any] = {"title": args.title}
    normalized_start_date = normalize_user_datetime_value(args.start_date, args.time_zone)
    if normalized_start_date:
        new_item["startDate"] = normalized_start_date
    if args.time_zone:
        new_item["timeZone"] = args.time_zone
    if args.sort_order is not None:
        new_item["sortOrder"] = args.sort_order
    if args.all_day:
        new_item["isAllDay"] = True

    items.append(new_item)
    updated = update_task_items(args, region, token_path, str(parent_task.get("projectId")), str(parent_task.get("id")), items)
    updated_items = updated.get("items") if isinstance(updated.get("items"), list) else []
    emit({
        "ok": True,
        "parentTask": {
            "id": parent_task.get("id"),
            "projectId": parent_task.get("projectId"),
            "title": parent_task.get("title"),
            "projectName": parent_task.get("projectName"),
        },
        "task": updated,
        "subtask_count": len(updated_items),
    })


def command_subtask_smart_update(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    parent_task, match = resolve_subtask_selection(
        args,
        region,
        token_path,
        args.subtask_title,
        project_id=args.project_id,
        project_name=args.project_name,
        task_id=args.task_id,
        parent_task_title=args.parent_task_title,
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    existing_items = parent_task.get("items") if isinstance(parent_task.get("items"), list) else []
    items = [clean_subtask_item(item) for item in existing_items if isinstance(item, dict)]
    target = find_existing_subtask_item(items, match)
    if target is None:
        raise CliError(f"Subtask {args.subtask_title} not found in task {parent_task.get('id')}.")

    changed = False
    if args.new_title:
        target["title"] = args.new_title
        changed = True
    normalized_start_date = normalize_user_datetime_value(args.start_date, args.time_zone) if args.start_date is not None else None
    if args.start_date is not None:
        target["startDate"] = normalized_start_date
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

    updated = update_task_items(args, region, token_path, str(parent_task.get("projectId")), str(parent_task.get("id")), items)
    emit({
        "ok": True,
        "parentTask": {
            "id": parent_task.get("id"),
            "projectId": parent_task.get("projectId"),
            "title": parent_task.get("title"),
            "projectName": parent_task.get("projectName"),
        },
        "matchedSubtask": match,
        "task": updated,
    })


def command_subtask_smart_complete(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    parent_task, match = resolve_subtask_selection(
        args,
        region,
        token_path,
        args.subtask_title,
        project_id=args.project_id,
        project_name=args.project_name,
        task_id=args.task_id,
        parent_task_title=args.parent_task_title,
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    existing_items = parent_task.get("items") if isinstance(parent_task.get("items"), list) else []
    items = [clean_subtask_item(item) for item in existing_items if isinstance(item, dict)]
    target = find_existing_subtask_item(items, match)
    if target is None:
        raise CliError(f"Subtask {args.subtask_title} not found in task {parent_task.get('id')}.")

    target["status"] = 1
    target["completedTime"] = args.completed_time or ticktick_time_now()
    updated = update_task_items(args, region, token_path, str(parent_task.get("projectId")), str(parent_task.get("id")), items)
    emit({
        "ok": True,
        "parentTask": {
            "id": parent_task.get("id"),
            "projectId": parent_task.get("projectId"),
            "title": parent_task.get("title"),
            "projectName": parent_task.get("projectName"),
        },
        "matchedSubtask": match,
        "task": updated,
        "completed_subtask_id": match.get("id"),
    })


def command_subtask_smart_delete(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    parent_task, match = resolve_subtask_selection(
        args,
        region,
        token_path,
        args.subtask_title,
        project_id=args.project_id,
        project_name=args.project_name,
        task_id=args.task_id,
        parent_task_title=args.parent_task_title,
        include_completed=args.include_completed,
        include_closed_projects=args.include_closed_projects,
    )
    existing_items = parent_task.get("items") if isinstance(parent_task.get("items"), list) else []
    items = [clean_subtask_item(item) for item in existing_items if isinstance(item, dict)]
    target = find_existing_subtask_item(items, match)
    if target is None:
        raise CliError(f"Subtask {args.subtask_title} not found in task {parent_task.get('id')}.")

    remaining = [item for item in items if item is not target]
    updated = update_task_items(args, region, token_path, str(parent_task.get("projectId")), str(parent_task.get("id")), remaining)
    emit({
        "ok": True,
        "parentTask": {
            "id": parent_task.get("id"),
            "projectId": parent_task.get("projectId"),
            "title": parent_task.get("title"),
            "projectName": parent_task.get("projectName"),
        },
        "matchedSubtask": match,
        "task": updated,
        "deleted_subtask_id": match.get("id"),
    })




def build_schedule_entries(
    tasks: list[dict[str, Any]],
    reference_time: datetime,
    default_duration_minutes: int,
    horizon_days: int | None,
) -> list[ScheduleEntry]:
    entries = [build_schedule_entry(task, default_duration_minutes) for task in tasks]
    entries = [entry for entry in entries if schedule_entry_within_horizon(entry, reference_time, horizon_days)]
    entries.sort(key=schedule_entry_sort_key)
    return entries


def command_schedule_analyze(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    if args.default_duration_minutes <= 0:
        raise CliError('--default-duration-minutes must be greater than 0.')
    if args.days is not None and args.days < 0:
        raise CliError('--days must be 0 or greater.')

    reference_time = resolve_reference_time(getattr(args, 'reference_time', None), getattr(args, 'time_zone', None))
    project, tasks = collect_tasks(
        args,
        region,
        token_path,
        project_id=args.project_id,
        project_name=getattr(args, 'project_name', None),
        include_completed=False,
        include_closed_projects=args.include_closed_projects,
    )
    entries = build_schedule_entries(tasks, reference_time, args.default_duration_minutes, args.days)
    busy_windows = build_busy_windows(args, reference_time)
    conflicts, risks, summary = build_schedule_analysis(entries, busy_windows, reference_time)
    serialized_tasks = [serialize_schedule_entry(entry, reference_time) for entry in entries]
    if args.limit and args.limit > 0:
        serialized_tasks = serialized_tasks[: args.limit]
    emit({
        'ok': True,
        'project': project,
        'referenceTime': format_schedule_datetime(reference_time, getattr(args, 'time_zone', None)),
        'summary': summary,
        'busyWindows': [serialize_busy_window(window, getattr(args, 'time_zone', None)) for window in busy_windows],
        'conflicts': conflicts,
        'risks': risks,
        'count': len(serialized_tasks),
        'tasks': serialized_tasks,
    })


def command_schedule_rebalance(args: argparse.Namespace, region: RegionConfig, token_path: Path) -> None:
    if args.default_duration_minutes <= 0:
        raise CliError('--default-duration-minutes must be greater than 0.')
    if args.days is not None and args.days < 0:
        raise CliError('--days must be 0 or greater.')
    if args.step_minutes <= 0:
        raise CliError('--step-minutes must be greater than 0.')
    if args.search_horizon_days <= 0:
        raise CliError('--search-horizon-days must be greater than 0.')

    reference_time = resolve_reference_time(getattr(args, 'reference_time', None), getattr(args, 'time_zone', None))
    project, tasks = collect_tasks(
        args,
        region,
        token_path,
        project_id=args.project_id,
        project_name=getattr(args, 'project_name', None),
        include_completed=False,
        include_closed_projects=args.include_closed_projects,
    )
    entries = build_schedule_entries(tasks, reference_time, args.default_duration_minutes, args.days)
    busy_windows = build_busy_windows(args, reference_time)
    conflicts, risks, summary = build_schedule_analysis(entries, busy_windows, reference_time)
    proposals, skipped = propose_rebalanced_schedule(
        entries=entries,
        busy_windows=busy_windows,
        reference_time=reference_time,
        search_horizon_days=args.search_horizon_days,
        step_minutes=args.step_minutes,
        task_queries=list(args.task_query or []),
        protected_titles=list(args.protect_task_title or []),
    )

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if args.apply:
        for proposal in proposals:
            update_args = argparse.Namespace(
                task_id=proposal['taskId'],
                project_id=proposal['projectId'],
                title=None,
                content=None,
                desc=None,
                priority=None,
                due_date=proposal['newDueDate'],
                start_date=proposal['newStartDate'],
                time_zone=proposal['timeZone'],
                all_day=False,
                tags=None,
                repeat_flag=None,
                reminders=None,
                subtask=None,
                clear_due_date=False,
                clear_start_date=False,
            )
            try:
                payload = build_task_payload(update_args, include_identity=True)
                task = api_request(args, region, token_path, 'POST', f"/task/{proposal['taskId']}", json_body=payload)
                applied.append({
                    'taskId': proposal['taskId'],
                    'projectId': proposal['projectId'],
                    'title': proposal['title'],
                    'newStartDate': proposal['newStartDate'],
                    'newDueDate': proposal['newDueDate'],
                    'task': task,
                })
            except CliError as exc:
                failed.append({
                    'taskId': proposal['taskId'],
                    'projectId': proposal['projectId'],
                    'title': proposal['title'],
                    'error': str(exc),
                })

    emit({
        'ok': len(failed) == 0,
        'project': project,
        'referenceTime': format_schedule_datetime(reference_time, getattr(args, 'time_zone', None)),
        'summary': summary,
        'busyWindows': [serialize_busy_window(window, getattr(args, 'time_zone', None)) for window in busy_windows],
        'conflicts': conflicts,
        'risks': risks,
        'proposalCount': len(proposals),
        'proposals': proposals,
        'skipped': skipped,
        'applyRequested': bool(args.apply),
        'appliedCount': len(applied),
        'failedCount': len(failed),
        'applied': applied,
        'failed': failed,
    })

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
    parser.add_argument("--redirect-uri", help=f"OAuth redirect URI; headless cloud recommendation: {DEFAULT_LOCALHOST_REDIRECT_URI}")

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


    doctor = subparsers.add_parser('doctor', help='Check cloud deployment prerequisites and token storage')
    doctor.add_argument('--check-api', action='store_true', help='Also call GET /project using the current token')
    doctor.add_argument('--auto-refresh', action='store_true', help='Allow token refresh during --check-api')

    subparsers.add_parser("projects", help="List projects")

    project_find = subparsers.add_parser("project-find", help="Find project by name")
    project_find.add_argument("--name", required=True)

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
    tasks.add_argument("--project-name")
    tasks.add_argument("--include-completed", action="store_true")
    tasks.add_argument("--include-closed-projects", action="store_true")
    tasks.add_argument("--limit", type=int, default=0)

    task_find = subparsers.add_parser("task-find", help="Find tasks by title")
    task_find.add_argument("--title", required=True)
    task_find.add_argument("--project-id")
    task_find.add_argument("--project-name")
    task_find.add_argument("--include-completed", action="store_true")
    task_find.add_argument("--include-closed-projects", action="store_true")
    task_find.add_argument("--limit", type=int, default=20)

    task_search = subparsers.add_parser("task-search", help="Search tasks across title, content, subtasks, tags, and project")
    task_search.add_argument("--query", required=True)
    task_search.add_argument("--project-id")
    task_search.add_argument("--project-name")
    task_search.add_argument("--field", action="append", choices=list(TASK_SEARCH_FIELDS), help="Repeat to limit search fields")
    task_search.add_argument("--include-completed", action="store_true")
    task_search.add_argument("--include-closed-projects", action="store_true")
    task_search.add_argument("--limit", type=int, default=20)

    task_get = subparsers.add_parser("task-get", help="Get task by project and task id")
    task_get.add_argument("--project-id", required=True)
    task_get.add_argument("--task-id", required=True)

    task_create = subparsers.add_parser("task-create", help="Create task")
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--project-id")
    task_create.add_argument("--project-name")
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

    task_smart_update = subparsers.add_parser("task-smart-update", help="Resolve task by title then update it")
    task_smart_update.add_argument("--task-title", required=True)
    task_smart_update.add_argument("--project-id")
    task_smart_update.add_argument("--project-name")
    task_smart_update.add_argument("--include-completed", action="store_true")
    task_smart_update.add_argument("--include-closed-projects", action="store_true")
    task_smart_update.add_argument("--title")
    task_smart_update.add_argument("--content")
    task_smart_update.add_argument("--desc")
    task_smart_update.add_argument("--priority", type=int, choices=[0, 1, 3, 5])
    task_smart_update.add_argument("--due-date")
    task_smart_update.add_argument("--start-date")
    task_smart_update.add_argument("--time-zone")
    task_smart_update.add_argument("--all-day", action="store_true")
    task_smart_update.add_argument("--tags", help="Comma-separated tags")
    task_smart_update.add_argument("--repeat-flag")
    task_smart_update.add_argument("--reminders", help="Comma-separated reminder triggers")
    task_smart_update.add_argument("--clear-due-date", action="store_true")
    task_smart_update.add_argument("--clear-start-date", action="store_true")

    task_complete = subparsers.add_parser("task-complete", help="Complete task")
    task_complete.add_argument("--task-id", required=True)
    task_complete.add_argument("--project-id", required=True)

    task_smart_complete = subparsers.add_parser("task-smart-complete", help="Resolve task by title then complete it")
    task_smart_complete.add_argument("--task-title", required=True)
    task_smart_complete.add_argument("--project-id")
    task_smart_complete.add_argument("--project-name")
    task_smart_complete.add_argument("--include-completed", action="store_true")
    task_smart_complete.add_argument("--include-closed-projects", action="store_true")

    task_delete = subparsers.add_parser("task-delete", help="Delete task")
    task_delete.add_argument("--task-id", required=True)
    task_delete.add_argument("--project-id", required=True)

    task_smart_delete = subparsers.add_parser("task-smart-delete", help="Resolve task by title then delete it")
    task_smart_delete.add_argument("--task-title", required=True)
    task_smart_delete.add_argument("--project-id")
    task_smart_delete.add_argument("--project-name")
    task_smart_delete.add_argument("--include-completed", action="store_true")
    task_smart_delete.add_argument("--include-closed-projects", action="store_true")

    task_move = subparsers.add_parser("task-move", help="Move one or more tasks between projects")
    task_move.add_argument("--from-project-id", required=True)
    task_move.add_argument("--to-project-id", required=True)
    task_move.add_argument("--task-id", action="append", required=True, help="Repeat for multiple task ids")

    tasks_completed = subparsers.add_parser("tasks-completed", help="List completed tasks")
    tasks_completed.add_argument("--project-id", action="append", help="Repeat for multiple project ids")
    tasks_completed.add_argument("--project-name")
    tasks_completed.add_argument("--start-date")
    tasks_completed.add_argument("--end-date")

    tasks_filter = subparsers.add_parser("tasks-filter", help="Filter tasks with server-side criteria")
    tasks_filter.add_argument("--project-id", action="append", help="Repeat for multiple project ids")
    tasks_filter.add_argument("--project-name")
    tasks_filter.add_argument("--start-date")
    tasks_filter.add_argument("--end-date")
    tasks_filter.add_argument("--priority", help="Comma-separated values, e.g. 0,3,5")
    tasks_filter.add_argument("--tag", help="Comma-separated tags")
    tasks_filter.add_argument("--status", help="Comma-separated values, e.g. 0,2")
    tasks_filter.add_argument("--limit", type=int, default=0)

    tasks_due = subparsers.add_parser("tasks-due", help="List tasks by due window")
    tasks_due.add_argument("--project-id")
    tasks_due.add_argument("--project-name")
    tasks_due.add_argument("--include-closed-projects", action="store_true")
    tasks_due.add_argument("--limit", type=int, default=0)
    due_group = tasks_due.add_mutually_exclusive_group(required=True)
    due_group.add_argument("--when", choices=["today", "tomorrow", "this-week", "overdue"])
    due_group.add_argument("--days", type=int)

    tasks_focus = subparsers.add_parser("tasks-focus", help="List focused task sets inspired by GTD workflows")
    tasks_focus.add_argument("--mode", required=True, choices=["engaged", "next"])
    tasks_focus.add_argument("--project-id")
    tasks_focus.add_argument("--project-name")
    tasks_focus.add_argument("--include-closed-projects", action="store_true")
    tasks_focus.add_argument("--limit", type=int, default=0)


    schedule_analyze = subparsers.add_parser('schedule-analyze', help='Analyze active tasks as a schedule and detect conflicts')
    schedule_analyze.add_argument('--project-id')
    schedule_analyze.add_argument('--project-name')
    schedule_analyze.add_argument('--include-closed-projects', action='store_true')
    schedule_analyze.add_argument('--reference-time')
    schedule_analyze.add_argument('--time-zone')
    schedule_analyze.add_argument('--busy-window', action='append', help="Repeat 'start/end' blocks for unavailable time")
    schedule_analyze.add_argument('--current-task-title')
    schedule_analyze.add_argument('--current-task-until')
    schedule_analyze.add_argument('--default-duration-minutes', type=int, default=30)
    schedule_analyze.add_argument('--days', type=int)
    schedule_analyze.add_argument('--limit', type=int, default=0)

    schedule_rebalance = subparsers.add_parser('schedule-rebalance', help='Propose or apply task rescheduling after conflicts or blocked time')
    schedule_rebalance.add_argument('--project-id')
    schedule_rebalance.add_argument('--project-name')
    schedule_rebalance.add_argument('--include-closed-projects', action='store_true')
    schedule_rebalance.add_argument('--reference-time')
    schedule_rebalance.add_argument('--time-zone')
    schedule_rebalance.add_argument('--busy-window', action='append', help="Repeat 'start/end' blocks for unavailable time")
    schedule_rebalance.add_argument('--current-task-title')
    schedule_rebalance.add_argument('--current-task-until')
    schedule_rebalance.add_argument('--default-duration-minutes', type=int, default=30)
    schedule_rebalance.add_argument('--days', type=int)
    schedule_rebalance.add_argument('--search-horizon-days', type=int, default=14)
    schedule_rebalance.add_argument('--step-minutes', type=int, default=15)
    schedule_rebalance.add_argument('--task-query', action='append', help='Only move tasks matching these queries')
    schedule_rebalance.add_argument('--protect-task-title', action='append', help='Repeat to keep matching tasks fixed')
    schedule_rebalance.add_argument('--apply', action='store_true')

    tasks_batch_create = subparsers.add_parser("tasks-batch-create", help="Create many tasks from a JSON array")
    tasks_batch_create.add_argument("--json", help="Inline JSON array")
    tasks_batch_create.add_argument("--json-file", help="Path to JSON file containing an array of task objects")

    subtask_add = subparsers.add_parser("subtask-add", help="Add subtask to a parent task")
    subtask_add.add_argument("--project-id", required=True)
    subtask_add.add_argument("--task-id", required=True)
    subtask_add.add_argument("--title", required=True)
    subtask_add.add_argument("--start-date")
    subtask_add.add_argument("--time-zone")
    subtask_add.add_argument("--sort-order", type=int)
    subtask_add.add_argument("--all-day", action="store_true")

    subtask_find = subparsers.add_parser("subtask-find", help="Find subtasks by title under a parent task")
    subtask_find.add_argument("--project-id")
    subtask_find.add_argument("--project-name")
    subtask_find.add_argument("--task-id")
    subtask_find.add_argument("--parent-task-title")
    subtask_find.add_argument("--subtask-title", required=True)
    subtask_find.add_argument("--include-completed", action="store_true")
    subtask_find.add_argument("--include-closed-projects", action="store_true")
    subtask_find.add_argument("--limit", type=int, default=20)

    subtask_smart_add = subparsers.add_parser("subtask-smart-add", help="Resolve parent task by title or id, then add a subtask")
    subtask_smart_add.add_argument("--project-id")
    subtask_smart_add.add_argument("--project-name")
    subtask_smart_add.add_argument("--task-id")
    subtask_smart_add.add_argument("--parent-task-title")
    subtask_smart_add.add_argument("--title", required=True)
    subtask_smart_add.add_argument("--start-date")
    subtask_smart_add.add_argument("--time-zone")
    subtask_smart_add.add_argument("--sort-order", type=int)
    subtask_smart_add.add_argument("--all-day", action="store_true")
    subtask_smart_add.add_argument("--include-completed", action="store_true")
    subtask_smart_add.add_argument("--include-closed-projects", action="store_true")

    subtask_update = subparsers.add_parser("subtask-update", help="Update a subtask")
    subtask_update.add_argument("--project-id", required=True)
    subtask_update.add_argument("--task-id", required=True)
    subtask_update.add_argument("--subtask-id", required=True)
    subtask_update.add_argument("--title")
    subtask_update.add_argument("--start-date")
    subtask_update.add_argument("--time-zone")
    subtask_update.add_argument("--sort-order", type=int)
    subtask_update.add_argument("--all-day", action="store_true")

    subtask_smart_update = subparsers.add_parser("subtask-smart-update", help="Resolve a subtask by title, then update it")
    subtask_smart_update.add_argument("--project-id")
    subtask_smart_update.add_argument("--project-name")
    subtask_smart_update.add_argument("--task-id")
    subtask_smart_update.add_argument("--parent-task-title")
    subtask_smart_update.add_argument("--subtask-title", required=True)
    subtask_smart_update.add_argument("--new-title")
    subtask_smart_update.add_argument("--start-date")
    subtask_smart_update.add_argument("--time-zone")
    subtask_smart_update.add_argument("--sort-order", type=int)
    subtask_smart_update.add_argument("--all-day", action="store_true")
    subtask_smart_update.add_argument("--include-completed", action="store_true")
    subtask_smart_update.add_argument("--include-closed-projects", action="store_true")

    subtask_complete = subparsers.add_parser("subtask-complete", help="Complete a subtask")
    subtask_complete.add_argument("--project-id", required=True)
    subtask_complete.add_argument("--task-id", required=True)
    subtask_complete.add_argument("--subtask-id", required=True)
    subtask_complete.add_argument("--completed-time", help="Time in yyyy-MM-dd'T'HH:mm:ssZ")

    subtask_smart_complete = subparsers.add_parser("subtask-smart-complete", help="Resolve a subtask by title, then complete it")
    subtask_smart_complete.add_argument("--project-id")
    subtask_smart_complete.add_argument("--project-name")
    subtask_smart_complete.add_argument("--task-id")
    subtask_smart_complete.add_argument("--parent-task-title")
    subtask_smart_complete.add_argument("--subtask-title", required=True)
    subtask_smart_complete.add_argument("--completed-time", help="Time in yyyy-MM-dd'T'HH:mm:ssZ")
    subtask_smart_complete.add_argument("--include-completed", action="store_true")
    subtask_smart_complete.add_argument("--include-closed-projects", action="store_true")

    subtask_delete = subparsers.add_parser("subtask-delete", help="Delete a subtask")
    subtask_delete.add_argument("--project-id", required=True)
    subtask_delete.add_argument("--task-id", required=True)
    subtask_delete.add_argument("--subtask-id", required=True)

    subtask_smart_delete = subparsers.add_parser("subtask-smart-delete", help="Resolve a subtask by title, then delete it")
    subtask_smart_delete.add_argument("--project-id")
    subtask_smart_delete.add_argument("--project-name")
    subtask_smart_delete.add_argument("--task-id")
    subtask_smart_delete.add_argument("--parent-task-title")
    subtask_smart_delete.add_argument("--subtask-title", required=True)
    subtask_smart_delete.add_argument("--include-completed", action="store_true")
    subtask_smart_delete.add_argument("--include-closed-projects", action="store_true")

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
    elif args.command == "doctor":
        command_doctor(args, region, token_path, state_path)
    elif args.command == "projects":
        command_projects(args, region, token_path)
    elif args.command == "project-find":
        command_project_find(args, region, token_path)
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
    elif args.command == "task-find":
        command_task_find(args, region, token_path)
    elif args.command == "task-search":
        command_task_search(args, region, token_path)
    elif args.command == "task-get":
        command_task_get(args, region, token_path)
    elif args.command == "task-create":
        command_task_create(args, region, token_path)
    elif args.command == "task-update":
        command_task_update(args, region, token_path)
    elif args.command == "task-smart-update":
        command_task_smart_update(args, region, token_path)
    elif args.command == "task-complete":
        command_task_complete(args, region, token_path)
    elif args.command == "task-smart-complete":
        command_task_smart_complete(args, region, token_path)
    elif args.command == "task-delete":
        command_task_delete(args, region, token_path)
    elif args.command == "task-smart-delete":
        command_task_smart_delete(args, region, token_path)
    elif args.command == "task-move":
        command_task_move(args, region, token_path)
    elif args.command == "tasks-completed":
        command_tasks_completed(args, region, token_path)
    elif args.command == "tasks-filter":
        command_tasks_filter(args, region, token_path)
    elif args.command == "tasks-due":
        command_tasks_due(args, region, token_path)
    elif args.command == "tasks-focus":
        command_tasks_focus(args, region, token_path)
    elif args.command == "schedule-analyze":
        command_schedule_analyze(args, region, token_path)
    elif args.command == "schedule-rebalance":
        command_schedule_rebalance(args, region, token_path)
    elif args.command == "tasks-batch-create":
        command_tasks_batch_create(args, region, token_path)
    elif args.command == "subtask-add":
        command_subtask_add(args, region, token_path)
    elif args.command == "subtask-find":
        command_subtask_find(args, region, token_path)
    elif args.command == "subtask-smart-add":
        command_subtask_smart_add(args, region, token_path)
    elif args.command == "subtask-update":
        command_subtask_update(args, region, token_path)
    elif args.command == "subtask-smart-update":
        command_subtask_smart_update(args, region, token_path)
    elif args.command == "subtask-complete":
        command_subtask_complete(args, region, token_path)
    elif args.command == "subtask-smart-complete":
        command_subtask_smart_complete(args, region, token_path)
    elif args.command == "subtask-delete":
        command_subtask_delete(args, region, token_path)
    elif args.command == "subtask-smart-delete":
        command_subtask_smart_delete(args, region, token_path)
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
