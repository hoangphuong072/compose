from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = ("api_key", "authorization", "password", "token", "secret", "credential", "credentials")


def log_event(config: Any, event: str, level: str = "INFO", **fields: Any) -> None:
    if not getattr(config, "logging_enabled", False):
        return
    min_level = _level_value(str(getattr(config, "log_level", "INFO")))
    if _level_value(level) < min_level:
        return

    logs_dir = getattr(config, "logs_dir")
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"agent-{datetime.now().strftime('%Y%m%d')}.jsonl"
    record = {
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "level": level.upper(),
        "event": event,
        **redact(fields, max_chars=int(getattr(config, "log_max_value_chars", 4000)), allow_sensitive=bool(getattr(config, "log_sensitive_data", False))),
    }
    line = json.dumps(record, ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    if bool(getattr(config, "log_to_console", False)):
        print(line, flush=True)


def redact(value: Any, max_chars: int = 4000, allow_sensitive: bool = False, key: str = "") -> Any:
    if allow_sensitive:
        return _truncate(value, max_chars)
    if key and any(part in key.lower() for part in SENSITIVE_KEYS):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(k): redact(v, max_chars=max_chars, allow_sensitive=allow_sensitive, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, max_chars=max_chars, allow_sensitive=allow_sensitive, key=key) for item in value]
    return _truncate(value, max_chars)


def preview(value: Any, max_chars: int = 4000) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + f"... truncated {len(text) - max_chars} characters ..."
    return text


def latest_log_path(logs_dir: Path) -> Path:
    return logs_dir / f"agent-{datetime.now().strftime('%Y%m%d')}.jsonl"


def tail_lines(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8").splitlines()
    return content[-lines:]


def _truncate(value: Any, max_chars: int) -> Any:
    if isinstance(value, str) and max_chars > 0 and len(value) > max_chars:
        return value[:max_chars] + f"... truncated {len(value) - max_chars} characters ..."
    return value


def _level_value(level: str) -> int:
    return {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}.get(level.upper(), 20)
