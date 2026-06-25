from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .credentials import CredentialStore
from .memory import JsonlMemory


ToolHandler = Callable[..., str]


_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_URL_TRAILING_CHARS = ".,;:!?)]}"


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    toolset: str

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self, enabled_toolsets: tuple[str, ...]) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values() if tool.toolset in enabled_toolsets]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def run(self, name: str, raw_arguments: str | dict[str, Any]) -> str:
        if name not in self._tools:
            return f"Unknown tool: {name}"
        if isinstance(raw_arguments, str):
            arguments = json.loads(raw_arguments or "{}")
        else:
            arguments = raw_arguments
        try:
            return self._tools[name].handler(**arguments)
        except Exception as exc:
            return f"Tool {name} failed: {type(exc).__name__}: {exc}"


def _safe_path(workspace: Path, path: str) -> Path:
    target = (workspace / path).resolve()
    if workspace.resolve() not in [target, *target.parents]:
        raise ValueError("Path is outside the configured workspace.")
    return target


def create_default_registry(
    workspace: Path,
    memory: JsonlMemory,
    default_pinchtab_profiles: tuple[str, ...] = (),
) -> ToolRegistry:
    registry = ToolRegistry()
    credential_store = CredentialStore(memory.path.parent / "credentials.json")
    pinchtab_url_mapper = _PinchTabUrlMapper()

    def list_files(path: str = ".") -> str:
        base = _safe_path(workspace, path)
        if not base.exists():
            return f"Path does not exist: {path}"
        if base.is_file():
            return str(base.relative_to(workspace))
        files = []
        for child in sorted(base.iterdir()):
            suffix = "/" if child.is_dir() else ""
            files.append(str(child.relative_to(workspace)) + suffix)
        return "\n".join(files) or "(empty directory)"

    def read_file(path: str, max_chars: int = 8000) -> str:
        target = _safe_path(workspace, path)
        return target.read_text(encoding="utf-8")[:max_chars]

    def write_file(path: str, content: str) -> str:
        target = _safe_path(workspace, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {target.relative_to(workspace)}"

    def run_shell(command: str, timeout: int = 20) -> str:
        parts = shlex.split(command)
        if not parts:
            return "No command provided."
        blocked = {"rm", "mv", "cp", "chmod", "chown", "sudo", "curl", "wget", "ssh"}
        if parts[0] in blocked:
            return f"Command blocked by basic safety policy: {parts[0]}"
        completed = subprocess.run(
            parts,
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output = (completed.stdout + completed.stderr).strip()
        return output[:12000] or f"Command exited with code {completed.returncode}."

    def remember(text: str) -> str:
        item = memory.add(text)
        return f"Remembered at {item.created_at}: {item.text}"

    def recall(query: str = "", limit: int = 5) -> str:
        items = memory.search(query, limit=limit)
        return "\n".join(f"- {item.text}" for item in items) or "No matching memories."

    def credential_store_tool(
        action: str,
        alias: str = "",
        protocol: str = "",
        username: str = "",
        password: str = "",
        host: str = "",
        notes: str = "",
    ) -> str:
        action = action.strip().lower()
        if action == "save":
            credential = credential_store.save(alias, protocol, username, password, host, notes)
            return f"Saved credential alias={credential.alias}, protocol={credential.protocol}, host={credential.host}."
        if action == "get":
            credential = credential_store.get(alias)
            if not credential:
                return f"No credential found for alias={alias}."
            return json.dumps(credential.__dict__, ensure_ascii=False, indent=2)
        if action == "list":
            credentials = credential_store.list()
            if not credentials:
                return "No saved credentials."
            return json.dumps([item.public_dict() for item in credentials], ensure_ascii=False, indent=2)
        if action == "delete":
            deleted = credential_store.delete(alias)
            return f"Deleted credential alias={alias}." if deleted else f"No credential found for alias={alias}."
        raise ValueError("Unsupported credential action. Use save, get, list, or delete.")

    def pinchtab_control(
        action: str,
        url: str = "",
        ref: str = "",
        text: str = "",
        key: str = "",
        value: str = "",
        direction: str = "",
        profile: str = "",
        interactive: bool = True,
        compact: bool = True,
        timeout: int = 30,
    ) -> str:
        url = pinchtab_url_mapper.decode(url)
        binary = os.environ.get("PINCHTAB_BINARY", "pinchtab")
        if shutil.which(binary) is None and "/" not in binary:
            return (
                "PinchTab binary was not found on PATH. Install it first, then start the daemon:\n"
                "  curl -fsSL https://pinchtab.com/install.sh | bash\n"
                "  pinchtab daemon install\n"
                "  pinchtab daemon\n"
                "Or set PINCHTAB_BINARY to the full binary path."
            )
        selected_profile = profile or (default_pinchtab_profiles[0] if default_pinchtab_profiles else "")
        server = _pinchtab_server_for_profile(binary, selected_profile) if selected_profile else ""
        if selected_profile and not server:
            return (
                f"No running PinchTab instance found for profile={selected_profile}. "
                "Start/open that profile in PinchTab first, then try again."
            )
        tab = ""
        if server:
            tab = _pinchtab_tab_for_server(binary, server)
            if not tab and action != "nav":
                tab = _open_pinchtab_tab(binary, server)
        args = _build_pinchtab_args(
            binary,
            action,
            url,
            ref,
            text,
            key,
            value,
            direction,
            interactive,
            compact,
            server=server,
            tab=tab,
        )
        completed = subprocess.run(
            args,
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output = (completed.stdout + completed.stderr).strip()
        if server and "not found" in output.lower() and "tab" in output.lower():
            tab = _open_pinchtab_tab(binary, server)
            if tab:
                args = _build_pinchtab_args(
                    binary,
                    action,
                    url,
                    ref,
                    text,
                    key,
                    value,
                    direction,
                    interactive,
                    compact,
                    server=server,
                    tab=tab,
                )
                completed = subprocess.run(
                    args,
                    cwd=workspace,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
                output = (completed.stdout + completed.stderr).strip()
        if not output:
            output = f"PinchTab command exited with code {completed.returncode}."
        return pinchtab_url_mapper.map_text(output)[:12000]

    string_prop = {"type": "string"}
    registry.register(
        Tool(
            "list_files",
            "List files under a workspace-relative directory.",
            {"type": "object", "properties": {"path": string_prop}},
            list_files,
            "file",
        )
    )
    registry.register(
        Tool(
            "read_file",
            "Read a UTF-8 file from the workspace.",
            {
                "type": "object",
                "properties": {"path": string_prop, "max_chars": {"type": "integer", "default": 8000}},
                "required": ["path"],
            },
            read_file,
            "file",
        )
    )
    registry.register(
        Tool(
            "write_file",
            "Write a UTF-8 file inside the workspace.",
            {
                "type": "object",
                "properties": {"path": string_prop, "content": string_prop},
                "required": ["path", "content"],
            },
            write_file,
            "file",
        )
    )
    registry.register(
        Tool(
            "run_shell",
            "Run a simple local command in the workspace. Shell syntax is not supported.",
            {
                "type": "object",
                "properties": {"command": string_prop, "timeout": {"type": "integer", "default": 20}},
                "required": ["command"],
            },
            run_shell,
            "terminal",
        )
    )
    registry.register(
        Tool(
            "remember",
            "Store a durable memory for future sessions.",
            {"type": "object", "properties": {"text": string_prop}, "required": ["text"]},
            remember,
            "memory",
        )
    )
    registry.register(
        Tool(
            "recall",
            "Search durable memory.",
            {
                "type": "object",
                "properties": {"query": string_prop, "limit": {"type": "integer", "default": 5}},
            },
            recall,
            "memory",
        )
    )
    registry.register(
        Tool(
            "credential_store",
            "Save, retrieve, list, or delete protocol credentials by alias. Use get only when the password is needed.",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["save", "get", "list", "delete"]},
                    "alias": {"type": "string", "description": "Short name, for example may1 or gpm01."},
                    "protocol": {"type": "string", "description": "Protocol such as ssh, http, mysql, postgres."},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "host": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["action"],
            },
            credential_store_tool,
            "credentials",
        )
    )
    registry.register(
        Tool(
            "pinchtab_control",
            "Control a local PinchTab browser through the PinchTab CLI. Pass profile when the user selected a PinchTab profile.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "health",
                            "nav",
                            "snap",
                            "text",
                            "click",
                            "type",
                            "fill",
                            "press",
                            "hover",
                            "scroll",
                            "select",
                            "focus",
                        ],
                    },
                    "url": {
                        "type": "string",
                        "description": "URL or private snapshot alias such as web_001 for action=nav.",
                    },
                    "ref": {"type": "string", "description": "Element ref such as e5 for click/fill/type/etc."},
                    "text": {"type": "string", "description": "Text for type/fill/select."},
                    "key": {"type": "string", "description": "Keyboard key for press, for example Enter."},
                    "value": {"type": "string", "description": "Value for select."},
                    "direction": {"type": "string", "description": "Scroll direction or pixel amount."},
                    "profile": {"type": "string", "description": "PinchTab profile name selected for this session."},
                    "interactive": {"type": "boolean", "default": True},
                    "compact": {"type": "boolean", "default": True},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["action"],
            },
            pinchtab_control,
            "browser",
        )
    )
    return registry


def _build_pinchtab_args(
    binary: str,
    action: str,
    url: str = "",
    ref: str = "",
    text: str = "",
    key: str = "",
    value: str = "",
    direction: str = "",
    interactive: bool = True,
    compact: bool = True,
    server: str = "",
    tab: str = "",
) -> list[str]:
    action = action.strip().lower()
    prefix = [binary, "--server", server] if server else [binary]
    if action == "health":
        return [*prefix, "health"]
    if action == "nav":
        if not url:
            raise ValueError("action=nav requires url.")
        args = [*prefix, "nav", url]
        if tab:
            args.extend(["--tab", tab])
        elif server:
            args.append("--new-tab")
        return args
    if action == "snap":
        args = [*prefix, "snap"]
        if tab:
            args.extend(["--tab", tab])
        if interactive:
            args.append("-i")
        if compact:
            args.append("-c")
        return args
    if action == "text":
        return [*prefix, "text", *(["--tab", tab] if tab else [])]
    if action == "click":
        _require(ref, "action=click requires ref.")
        return [*prefix, "click", ref, *(["--tab", tab] if tab else [])]
    if action == "type":
        _require(ref, "action=type requires ref.")
        _require(text, "action=type requires text.")
        return [*prefix, "type", ref, text, *(["--tab", tab] if tab else [])]
    if action == "fill":
        _require(ref, "action=fill requires ref.")
        _require(text, "action=fill requires text.")
        return [*prefix, "fill", ref, text, *(["--tab", tab] if tab else [])]
    if action == "press":
        _require(key, "action=press requires key.")
        args = [*prefix, "press", ref, key] if ref else [*prefix, "press", key]
        return [*args, *(["--tab", tab] if tab else [])]
    if action == "hover":
        _require(ref, "action=hover requires ref.")
        return [*prefix, "hover", ref, *(["--tab", tab] if tab else [])]
    if action == "scroll":
        _require(direction, "action=scroll requires direction.")
        return [*prefix, "scroll", direction, *(["--tab", tab] if tab else [])]
    if action == "select":
        _require(ref, "action=select requires ref.")
        selected = value or text
        _require(selected, "action=select requires value or text.")
        return [*prefix, "select", ref, selected, *(["--tab", tab] if tab else [])]
    if action == "focus":
        _require(ref, "action=focus requires ref.")
        return [*prefix, "focus", ref, *(["--tab", tab] if tab else [])]
    raise ValueError(f"Unsupported PinchTab action: {action}")


class _PinchTabUrlMapper:
    def __init__(self) -> None:
        self._alias_by_url: dict[str, str] = {}
        self._url_by_alias: dict[str, str] = {}

    def decode(self, url: str) -> str:
        return self._url_by_alias.get(url, url)

    def map_text(self, text: str) -> str:
        if not text:
            return text
        return _URL_RE.sub(self._replace_match, text)

    def _replace_match(self, match: re.Match[str]) -> str:
        raw_url = match.group(0)
        url = raw_url.rstrip(_URL_TRAILING_CHARS)
        suffix = raw_url[len(url) :]
        return self._alias_for(url) + suffix

    def _alias_for(self, url: str) -> str:
        alias = self._alias_by_url.get(url)
        if alias:
            return alias
        alias = f"web_{len(self._alias_by_url) + 1:03d}"
        self._alias_by_url[url] = alias
        self._url_by_alias[alias] = url
        return alias


def _require(value: str, message: str) -> None:
    if not value:
        raise ValueError(message)


def _pinchtab_server_for_profile(binary: str, profile: str) -> str:
    try:
        completed = subprocess.run(
            [binary, "instances", "--json"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return _parse_pinchtab_instance_server(completed.stdout, profile)


def _pinchtab_tab_for_server(binary: str, server: str) -> str:
    try:
        completed = subprocess.run(
            [binary, "--server", server, "tab", "--json"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return _parse_pinchtab_active_tab(completed.stdout)


def _open_pinchtab_tab(binary: str, server: str) -> str:
    try:
        completed = subprocess.run(
            [binary, "--server", server, "nav", "https://example.com", "--new-tab", "--print-tab-id"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    output = (completed.stdout + "\n" + completed.stderr).strip()
    for line in output.splitlines():
        line = line.strip()
        if len(line) >= 16 and " " not in line and "error" not in line.lower():
            return line
    return _pinchtab_tab_for_server(binary, server)


def _parse_pinchtab_instance_server(output: str, profile: str) -> str:
    try:
        instances = json.loads(output)
    except json.JSONDecodeError:
        return ""
    if not isinstance(instances, list):
        return ""
    for item in instances:
        if not isinstance(item, dict):
            continue
        if item.get("profileName") == profile and item.get("status") == "running":
            return str(item.get("url") or "")
    return ""


def _parse_pinchtab_active_tab(output: str) -> str:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return ""
    tabs = data.get("tabs") if isinstance(data, dict) else data
    if not isinstance(tabs, list):
        return ""
    fallback = ""
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        tab_id = str(tab.get("id") or "")
        if not tab_id:
            continue
        if not fallback:
            fallback = tab_id
        if tab.get("status") == "active":
            return tab_id
    return fallback
