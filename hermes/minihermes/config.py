from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOME = Path.home() / ".minihermes"


@dataclass
class AgentConfig:
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    max_iterations: int = 8
    temperature: float = 0.2
    home_dir: Path = DEFAULT_HOME
    workspace: Path = Path.cwd()
    enabled_toolsets: tuple[str, ...] = ("file", "memory", "terminal", "browser", "credentials")
    telegram_token: str = ""
    telegram_allowed_chat_ids: tuple[int, ...] = ()
    tls_verify: bool = True
    show_context: bool = False
    context_max_chars: int = 4000
    pinchtab_profiles: tuple[str, ...] = ()
    logging_enabled: bool = True
    log_level: str = "INFO"
    log_dir: Path | None = None
    log_max_value_chars: int = 4000
    log_sensitive_data: bool = False
    log_to_console: bool = False

    @property
    def memory_path(self) -> Path:
        return self.home_dir / "memory.jsonl"

    @property
    def sessions_dir(self) -> Path:
        return self.home_dir / "sessions"

    @property
    def credentials_path(self) -> Path:
        return self.home_dir / "credentials.json"

    @property
    def logs_dir(self) -> Path:
        return self.log_dir or self.home_dir / "logs"


def load_config(path: Path | None = None, workspace: Path | None = None) -> AgentConfig:
    home = Path(os.environ.get("MINIHERMES_HOME", DEFAULT_HOME)).expanduser()
    config_path = path or home / "config.json"
    data: dict[str, object] = {}

    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))

    cfg = AgentConfig(
        model=str(os.environ.get("MINIHERMES_MODEL", data.get("model", AgentConfig.model))),
        base_url=str(os.environ.get("MINIHERMES_BASE_URL", data.get("base_url", AgentConfig.base_url))),
        api_key=str(os.environ.get("MINIHERMES_API_KEY", data.get("api_key", ""))),
        max_iterations=int(data.get("max_iterations", AgentConfig.max_iterations)),
        temperature=float(data.get("temperature", AgentConfig.temperature)),
        home_dir=home,
        workspace=_resolve_workspace(workspace, data),
        enabled_toolsets=_enabled_toolsets(data),
        telegram_token=str(os.environ.get("TELEGRAM_BOT_TOKEN", data.get("telegram_token", ""))),
        telegram_allowed_chat_ids=_telegram_allowed_chat_ids(data),
        tls_verify=_bool_value(os.environ.get("MINIHERMES_TLS_VERIFY", data.get("tls_verify", True))),
        show_context=_bool_value(os.environ.get("MINIHERMES_SHOW_CONTEXT", data.get("show_context", False))),
        context_max_chars=int(os.environ.get("MINIHERMES_CONTEXT_MAX_CHARS", data.get("context_max_chars", 4000))),
        logging_enabled=_bool_value(os.environ.get("MINIHERMES_LOGGING_ENABLED", data.get("logging_enabled", True))),
        log_level=str(os.environ.get("MINIHERMES_LOG_LEVEL", data.get("log_level", "INFO"))).upper(),
        log_dir=_optional_path(os.environ.get("MINIHERMES_LOG_DIR", data.get("log_dir", ""))),
        log_max_value_chars=int(os.environ.get("MINIHERMES_LOG_MAX_VALUE_CHARS", data.get("log_max_value_chars", 4000))),
        log_sensitive_data=_bool_value(os.environ.get("MINIHERMES_LOG_SENSITIVE_DATA", data.get("log_sensitive_data", False))),
        log_to_console=_bool_value(os.environ.get("MINIHERMES_LOG_TO_CONSOLE", data.get("log_to_console", False))),
    )
    cfg.home_dir.mkdir(parents=True, exist_ok=True)
    cfg.sessions_dir.mkdir(parents=True, exist_ok=True)
    if cfg.logging_enabled:
        cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def write_default_config(path: Path | None = None) -> Path:
    home = Path(os.environ.get("MINIHERMES_HOME", DEFAULT_HOME)).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    config_path = path or home / "config.json"
    if not config_path.exists():
        config_path.write_text(
            json.dumps(
                {
                    "model": "gpt-4o-mini",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "",
                    "max_iterations": 8,
                    "temperature": 0.2,
                    "workspace": str(Path.cwd()),
                    "enabled_toolsets": ["file", "memory", "terminal", "browser", "credentials"],
                    "telegram_token": "",
                    "telegram_allowed_chat_ids": [],
                    "tls_verify": True,
                    "show_context": False,
                    "context_max_chars": 4000,
                    "logging_enabled": True,
                    "log_level": "INFO",
                    "log_dir": "",
                    "log_max_value_chars": 4000,
                    "log_sensitive_data": False,
                    "log_to_console": False,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return config_path


def _split_ints(value: str) -> list[int]:
    ids: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            ids.append(int(part))
    return ids


def _telegram_allowed_chat_ids(data: dict[str, object]) -> tuple[int, ...]:
    env_value = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS")
    if env_value is not None:
        return tuple(_split_ints(env_value))
    return tuple(int(chat_id) for chat_id in data.get("telegram_allowed_chat_ids", ()))


def _enabled_toolsets(data: dict[str, object]) -> tuple[str, ...]:
    env_value = os.environ.get("MINIHERMES_ENABLED_TOOLSETS")
    if env_value:
        return tuple(part.strip() for part in env_value.split(",") if part.strip())
    return tuple(data.get("enabled_toolsets", AgentConfig.enabled_toolsets))


def _resolve_workspace(workspace: Path | None, data: dict[str, object]) -> Path:
    raw = workspace or os.environ.get("MINIHERMES_WORKSPACE") or data.get("workspace") or Path.cwd()
    return Path(str(raw)).expanduser().resolve()


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _optional_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text).expanduser().resolve()
