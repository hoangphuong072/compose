from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .agent import Agent
from .config import AgentConfig
from .codex import run_codex_cli
from .credentials import CredentialStore
from .http import HttpError, get_json, post_json
from .llm import LLMError
from .logging import log_event, preview
from .pinchtab import install_pinchtab


BASE_TOOLSETS = ("browser", "credentials", "file", "memory", "terminal")


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, timeout: int = 60, tls_verify: bool = True) -> None:
        if not token:
            raise TelegramError("Missing Telegram token. Set TELEGRAM_BOT_TOKEN or telegram_token in config.json.")
        self.token = token
        self.timeout = timeout
        self.tls_verify = tls_verify
        self.base_url = f"https://api.telegram.org/bot{token}"

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        data = self._request("getUpdates", payload)
        return list(data.get("result", []))

    def send_message(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        for chunk in _chunks(text or "(empty response)", 3900):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if reply_markup and chunk == text:
                payload["reply_markup"] = reply_markup
            self._request("sendMessage", payload)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._request("editMessageText", payload)

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._request("answerCallbackQuery", payload)

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        self._request("setMyCommands", {"commands": commands})

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        self._request("sendChatAction", {"chat_id": chat_id, "action": action})

    def _request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{method}"
        try:
            data = post_json(url, payload, timeout=self.timeout, tls_verify=self.tls_verify).json()
        except HttpError as exc:
            raise TelegramError(f"Telegram HTTP {exc.status}: {exc.body}") from exc
        except json.JSONDecodeError as exc:
            raise TelegramError(f"Telegram response was not JSON: {exc}") from exc

        if not data.get("ok"):
            raise TelegramError(f"Telegram API error: {data}")
        return data


@dataclass
class SessionSelection:
    selected_profiles: set[str] = field(default_factory=set)
    selected_credentials: set[str] = field(default_factory=set)
    selected_skills: set[str] = field(default_factory=set)
    step: str = "skills"

    def summary(self) -> str:
        skills = ", ".join(sorted(self.selected_skills)) if self.selected_skills else "(none)"
        profiles = ", ".join(sorted(self.selected_profiles)) if self.selected_profiles else "(none)"
        credentials = ", ".join(sorted(self.selected_credentials)) if self.selected_credentials else "(none)"
        return (
            "Session configuration\n"
            f"PinchTab profiles: {profiles}\n"
            f"Credential aliases: {credentials}\n"
            f"Selected skills: {skills}"
        )


class TelegramBot:
    def __init__(
        self,
        config: AgentConfig,
        client: TelegramClient | None = None,
        agent_factory: Callable[[AgentConfig], Agent] = Agent,
    ) -> None:
        self.config = config
        self.client = client or TelegramClient(config.telegram_token, tls_verify=config.tls_verify)
        self.agent_factory = agent_factory
        self.agents: dict[int, Agent] = {}
        self.allowed_chat_ids = set(config.telegram_allowed_chat_ids)
        self.sessions: dict[int, SessionSelection] = {}
        self.wizards: dict[int, SessionSelection] = {}
        self.last_contexts: dict[int, str] = {}
        self.last_sessions: dict[int, dict[str, str]] = {}
        self.credential_store = CredentialStore(config.credentials_path)

    def run_forever(self, poll_timeout: int = 30, sleep_seconds: float = 1.0) -> None:
        offset: int | None = None
        self.register_bot_commands()
        print("MiniHermes Telegram bot is running. Press Ctrl+C to stop.")
        while True:
            try:
                updates = self.client.get_updates(offset=offset, timeout=poll_timeout)
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    self.handle_update(update)
            except KeyboardInterrupt:
                print()
                return
            except TelegramError as exc:
                print(f"Telegram error: {exc}")
                time.sleep(sleep_seconds)

    def register_bot_commands(self) -> None:
        self.client.set_my_commands(
            [
                {"command": "start", "description": "Show help"},
                {"command": "help", "description": "Show help"},
                {"command": "new", "description": "Reset conversation"},
                {"command": "new_sesion", "description": "Configure a new session"},
                {"command": "new_session", "description": "Configure a new session"},
                {"command": "last_context", "description": "Show previous request context"},
                {"command": "last_session", "description": "Show previous request and response"},
                {"command": "tools", "description": "List available tools"},
                {"command": "codex", "description": "Run Codex CLI in the workspace"},
                {"command": "install_pinchtab", "description": "Install and start PinchTab"},
            ]
        )

    def handle_update(self, update: dict[str, Any]) -> None:
        if update.get("callback_query"):
            callback = update["callback_query"]
            log_event(self.config, "telegram.callback.received", callback_id=str(callback.get("id", "")), data=str(callback.get("data", "")))
            self._handle_callback(update["callback_query"])
            return

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        if not isinstance(chat_id, int) or not text:
            return

        command = _command_name(text)
        allowed = not self.allowed_chat_ids or chat_id in self.allowed_chat_ids
        log_event(
            self.config,
            "telegram.message.received",
            chat_id=chat_id,
            text_preview=preview(text, self.config.log_max_value_chars),
            command=command,
            allowed=allowed,
        )

        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            self.client.send_message(chat_id, "This chat is not allowed to use this bot.")
            return

        if command:
            log_event(self.config, "telegram.command", chat_id=chat_id, command=command)
        if command in {"/start", "/help"}:
            self.client.send_message(chat_id, self._help_text())
            return
        if command in {"/new_sesion", "/new_session"}:
            self._start_session_wizard(chat_id)
            return
        if command in {"/new", "/reset"}:
            self._agent_for(chat_id).reset()
            self.client.send_message(chat_id, "Started a fresh conversation.")
            return
        if command == "/last_context":
            self.client.send_message(chat_id, self.last_contexts.get(chat_id, "No previous context for this chat."))
            return
        if command == "/last_session":
            self.client.send_message(chat_id, self._last_session_message(chat_id))
            return
        if command == "/tools":
            self.client.send_message(chat_id, "\n".join(self._agent_for(chat_id).available_tools()))
            return
        if command == "/codex":
            self.client.send_message(chat_id, self._run_codex_while_typing(chat_id, _command_args(text)))
            return
        if command == "/install_pinchtab":
            self._send_typing(chat_id)
            self.client.send_message(chat_id, install_pinchtab())
            return

        try:
            prompt = self._prompt_for_chat(chat_id, text)
            self.last_contexts[chat_id] = self._context_message(chat_id, prompt)
            ask_started = time.monotonic()
            log_event(self.config, "telegram.agent.ask.start", chat_id=chat_id, prompt_length=len(prompt))
            answer = self._ask_while_typing(chat_id, prompt)
        except LLMError as exc:
            log_event(self.config, "telegram.agent.ask.error", "ERROR", chat_id=chat_id, error_type=type(exc).__name__, error_message=str(exc))
            answer = f"LLM error: {exc}"
        else:
            log_event(
                self.config,
                "telegram.agent.ask.end",
                chat_id=chat_id,
                duration_ms=round((time.monotonic() - ask_started) * 1000, 2),
                response_length=len(answer),
            )
        self.client.send_message(chat_id, answer)

    def _ask_while_typing(self, chat_id: int, prompt: str) -> str:
        self._send_typing(chat_id)
        agent = self._agent_for(chat_id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(agent.ask_with_usage, prompt)
            while not future.done():
                time.sleep(4)
                if not future.done():
                    self._send_typing(chat_id)
            result = future.result().with_usage_report()
            self.last_sessions[chat_id] = {
                "provider": json.dumps(agent.last_provider_exchanges, ensure_ascii=False, indent=2),
            }
            return result

    def _run_codex_while_typing(self, chat_id: int, prompt: str) -> str:
        self._send_typing(chat_id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_codex_cli, prompt, self.config.workspace)
            while not future.done():
                time.sleep(4)
                if not future.done():
                    self._send_typing(chat_id)
            return future.result()

    def _agent_for(self, chat_id: int) -> Agent:
        if chat_id not in self.agents:
            self.agents[chat_id] = self.agent_factory(self._config_for_chat(chat_id))
        return self.agents[chat_id]

    def _send_typing(self, chat_id: int) -> None:
        try:
            self.client.send_chat_action(chat_id, "typing")
        except TelegramError as exc:
            log_event(self.config, "telegram.typing.error", "ERROR", chat_id=chat_id, error_type=type(exc).__name__, error_message=str(exc))
            print(f"Telegram typing action failed: {exc}")

    def _context_message(self, chat_id: int, text: str) -> str:
        preview = self._agent_for(chat_id).context_preview_for(text, self.config.context_max_chars)
        return f"Context sent to model:\n\n{preview}"

    def _last_session_message(self, chat_id: int) -> str:
        session = self.last_sessions.get(chat_id)
        if not session:
            return "No previous session turn for this chat."
        return "Last provider request/response\n\n" + session.get("provider", "(empty)")

    def _start_session_wizard(self, chat_id: int) -> None:
        selection = SessionSelection()
        self.wizards[chat_id] = selection
        self.client.send_message(
            chat_id,
            "Chọn skill cho session này:",
            reply_markup=self._skill_keyboard(selection),
        )

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id", ""))
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        data = str(callback.get("data", ""))
        if not isinstance(chat_id, int) or not isinstance(message_id, int):
            return
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            self.client.answer_callback_query(callback_id, "Not allowed.")
            return
        if not data.startswith("session:"):
            self.client.answer_callback_query(callback_id)
            return

        self.client.answer_callback_query(callback_id)
        selection = self.wizards.setdefault(chat_id, SessionSelection())
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        value = parts[2] if len(parts) > 2 else ""

        if action == "skill":
            options = self._session_skill_options()
            skill_name = options[int(value)].name if value.isdigit() and int(value) < len(options) else ""
            if not skill_name:
                self._render_skill_picker(chat_id, message_id, selection)
                return
            if skill_name in selection.selected_skills:
                selection.selected_skills.remove(skill_name)
            else:
                selection.selected_skills.add(skill_name)
            self._render_skill_picker(chat_id, message_id, selection)
            return

        if action == "skills_next":
            if "pinchtab_control" in selection.selected_skills:
                selection.step = "profiles"
                self._render_profile_picker(chat_id, message_id, selection)
            else:
                self._finish_session(chat_id, message_id, selection)
            return

        if action == "profile":
            profiles = self._pinchtab_profile_options()
            selected = profiles[int(value)] if value.isdigit() and int(value) < len(profiles) else None
            profile = selected.name if selected else ""
            if profile:
                if profile in selection.selected_profiles:
                    selection.selected_profiles.remove(profile)
                else:
                    selection.selected_profiles.add(profile)
                    start_result = _start_pinchtab_profile(selected)
                    if start_result:
                        self.client.send_message(chat_id, start_result)
            self._render_profile_picker(chat_id, message_id, selection)
            return

        if action == "profiles_next":
            selection.step = "credentials"
            self._render_credential_picker(chat_id, message_id, selection)
            return

        if action == "credential":
            aliases = self._credential_aliases()
            alias = aliases[int(value)] if value.isdigit() and int(value) < len(aliases) else ""
            if alias and alias != "(none)":
                if alias in selection.selected_credentials:
                    selection.selected_credentials.remove(alias)
                else:
                    selection.selected_credentials.add(alias)
            self._render_credential_picker(chat_id, message_id, selection)
            return

        if action == "done":
            self._finish_session(chat_id, message_id, selection)

    def _render_skill_picker(self, chat_id: int, message_id: int, selection: SessionSelection) -> None:
        self.client.edit_message_text(
            chat_id,
            message_id,
            "Chọn skill cho session này:",
            reply_markup=self._skill_keyboard(selection),
        )

    def _skill_keyboard(self, selection: SessionSelection) -> dict[str, Any]:
        rows = []
        for index, skill in enumerate(self._session_skill_options()):
            checked = skill.name in selection.selected_skills
            label = f"[x] {skill.label}" if checked else f"[ ] {skill.label}"
            rows.append([(label, f"session:skill:{index}")])
        rows.append([("Tiếp tục", "session:skills_next:")])
        return _inline_keyboard(rows)

    def _render_profile_picker(self, chat_id: int, message_id: int, selection: SessionSelection) -> None:
        rows = []
        for index, profile in enumerate(self._pinchtab_profile_options()):
            profile_name = profile.name
            checked = profile_name in selection.selected_profiles
            label = f"[x] {profile_name}" if checked else f"[ ] {profile_name}"
            rows.append([(label, f"session:profile:{index}")])
        rows.append([("Tiếp tục", "session:profiles_next:")])
        self.client.edit_message_text(
            chat_id,
            message_id,
            "Chọn PinchTab profile sẽ dùng:",
            reply_markup=_inline_keyboard(rows),
        )

    def _render_credential_picker(self, chat_id: int, message_id: int, selection: SessionSelection) -> None:
        rows = []
        for index, alias in enumerate(self._credential_aliases()):
            checked = alias in selection.selected_credentials
            label = f"[x] {alias}" if checked else f"[ ] {alias}"
            rows.append([(label, f"session:credential:{index}")])
        rows.append([("Done", "session:done:")])
        self.client.edit_message_text(
            chat_id,
            message_id,
            "Chọn credential/mật khẩu sẽ dùng:",
            reply_markup=_inline_keyboard(rows),
        )

    def _finish_session(self, chat_id: int, message_id: int, selection: SessionSelection) -> None:
        selection.step = "done"
        self.sessions[chat_id] = selection
        self.wizards.pop(chat_id, None)
        self.agents.pop(chat_id, None)
        self.client.edit_message_text(chat_id, message_id, selection.summary())
        self.client.send_message(chat_id, "Session mới đã sẵn sàng. Bạn có thể gửi yêu cầu tiếp theo.")

    def _pinchtab_profiles(self) -> list[str]:
        return [profile.name for profile in self._pinchtab_profile_options()]

    def _pinchtab_profile_options(self) -> list["PinchTabProfile"]:
        profiles = _fetch_pinchtab_profiles(self.config.tls_verify)
        return profiles or [PinchTabProfile(id="", name="default")]

    def _credential_aliases(self) -> list[str]:
        aliases = [credential.alias for credential in self.credential_store.list()]
        return aliases or ["(none)"]

    def _config_for_chat(self, chat_id: int) -> AgentConfig:
        session = self.sessions.get(chat_id)
        if not session or not session.selected_profiles:
            return self.config
        return replace(self.config, pinchtab_profiles=tuple(sorted(session.selected_profiles)))

    def _prompt_for_chat(self, chat_id: int, text: str) -> str:
        session = self.sessions.get(chat_id)
        if not session:
            return text
        credential_block = self._selected_credential_context(session)
        skill_block = self._selected_skill_context(session)
        selected_skills = ", ".join(sorted(session.selected_skills)) if session.selected_skills else "(none)"
        selected_profiles = ", ".join(sorted(session.selected_profiles)) if session.selected_profiles else "(none)"
        return (
            "Session context selected by the user:\n"
            f"PinchTab profiles: {selected_profiles}\n"
            "When using pinchtab_control, pass the selected profile name in the profile argument.\n"
            f"Base toolsets enabled by default: {', '.join(BASE_TOOLSETS)}\n"
            f"Selected skills: {selected_skills}\n"
            f"{credential_block}\n"
            f"{skill_block}\n"
            "User request:\n"
            f"{text}"
        )

    def _selected_credential_context(self, session: SessionSelection) -> str:
        if not session.selected_credentials:
            return ""
        blocks: list[str] = []
        for alias in sorted(session.selected_credentials):
            credential = self.credential_store.get(alias)
            if credential:
                blocks.append(
                    "Credential:\n"
                    f"Alias: {credential.alias}\n"
                    f"Protocol: {credential.protocol}\n"
                    f"Username: {credential.username}\n"
                    f"Password: {credential.password}\n"
                    f"Host: {credential.host}"
                )
        return "\n\n".join(blocks)

    def _session_skill_options(self) -> list["SessionSkill"]:
        options = [
            SessionSkill(
                name="pinchtab_control",
                label="pinchtab_control",
                content="Use the pinchtab_control tool for browser navigation, snapshots, text extraction, and element actions.",
            )
        ]
        skills_dir = self.config.workspace / "skills"
        if skills_dir.exists():
            for path in sorted(skills_dir.glob("*.md")):
                options.append(
                    SessionSkill(
                        name=path.stem,
                        label=path.name,
                        content=_read_skill_file(path),
                    )
                )
        return options

    def _selected_skill_context(self, session: SessionSelection) -> str:
        if not session.selected_skills:
            return ""
        by_name = {skill.name: skill for skill in self._session_skill_options()}
        blocks: list[str] = []
        for skill_name in sorted(session.selected_skills):
            skill = by_name.get(skill_name)
            if skill:
                blocks.append(f"Skill: {skill.label}\n{skill.content}")
        return "Selected skill instructions:\n" + "\n\n".join(blocks) if blocks else ""

    @staticmethod
    def _help_text() -> str:
        return (
            "MiniHermes Telegram bot\n"
            "/new - reset conversation\n"
            "/new_sesion - configure a new session\n"
            "/last_context - show previous request context\n"
            "/last_session - show previous request and response\n"
            "/tools - list available tools\n"
            "/codex <request> - run Codex CLI in the workspace\n"
            "/install_pinchtab - install and start PinchTab\n"
            "/help - show this help\n\n"
            "Send any normal message to chat with the agent."
        )


def _chunks(text: str, size: int) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def _command_name(text: str) -> str:
    if not text.startswith("/"):
        return ""
    command = text.split()[0]
    return command.split("@", 1)[0]


def _command_args(text: str) -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": callback_data} for label, callback_data in row]
            for row in rows
        ]
    }


@dataclass
class SessionSkill:
    name: str
    label: str
    content: str


@dataclass
class PinchTabProfile:
    id: str
    name: str


def _read_skill_file(path: Path, max_chars: int = 4000) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) > max_chars:
        return content[:max_chars] + f"\n\n... truncated {len(content) - max_chars} characters ..."
    return content


def _fetch_pinchtab_profiles(tls_verify: bool) -> list[PinchTabProfile]:
    cli_profiles = _fetch_pinchtab_profiles_cli()
    if cli_profiles:
        return cli_profiles

    base_url = os.environ.get("PINCHTAB_BASE_URL", "http://127.0.0.1:9867").rstrip("/")
    token = os.environ.get("PINCHTAB_TOKEN") or os.environ.get("BRIDGE_TOKEN", "")
    url = f"{base_url}/profiles?all=true"
    headers = {}
    if token:
        headers = {"Authorization": f"Bearer {token}", "X-PinchTab-Token": token}
    try:
        data = get_json(url, headers=headers, timeout=3, tls_verify=tls_verify).json()
    except (HttpError, json.JSONDecodeError):
        return []

    if isinstance(data, dict):
        candidates = data.get("profiles") or data.get("items") or data.get("result") or []
    else:
        candidates = data
    profiles: list[PinchTabProfile] = []
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, dict):
                profile_id = str(item.get("id") or item.get("profileId") or "")
                name = str(item.get("name") or item.get("profileName") or profile_id or "default")
                profiles.append(PinchTabProfile(id=profile_id, name=name))
            elif item:
                profiles.append(PinchTabProfile(id="", name=str(item)))
    return profiles


def _fetch_pinchtab_profiles_cli() -> list[PinchTabProfile]:
    binary = os.environ.get("PINCHTAB_BINARY", "pinchtab")
    if shutil.which(binary) is None and "/" not in binary:
        return []
    try:
        completed = subprocess.run(
            [binary, "profiles"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return _parse_pinchtab_profiles_cli(completed.stdout + "\n" + completed.stderr)


def _parse_pinchtab_profiles_cli(output: str) -> list[str]:
    profiles: list[PinchTabProfile] = []
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0].startswith("prof_"):
            profiles.append(PinchTabProfile(id=parts[0], name=parts[1]))
    return profiles


def _start_pinchtab_profile(profile: PinchTabProfile | str) -> str:
    if isinstance(profile, str):
        profile_id = profile
        profile_name = ""
    else:
        profile_id = profile.id
        profile_name = profile.name
    if not profile_id:
        return ""
    token = os.environ.get("PINCHTAB_TOKEN") or os.environ.get("BRIDGE_TOKEN", "")
    if not token:
        return "PINCHTAB_TOKEN is not set, so the selected PinchTab profile could not be started automatically."
    base_url = os.environ.get("PINCHTAB_BASE_URL", "http://127.0.0.1:9867").rstrip("/")
    payload = {"profileId": profile_id, "mode": "headed"}
    try:
        post_json(
            f"{base_url}/instances/start",
            payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "X-PinchTab-Token": token,
            },
            timeout=10,
        )
    except HttpError as exc:
        return f"Failed to start PinchTab profile {profile_id}: HTTP {exc.status}: {exc.body}"
    tab_message = _ensure_pinchtab_profile_tab(profile_name)
    suffix = f"\n{tab_message}" if tab_message else ""
    return f"Started PinchTab profile {profile_id}.{suffix}"


def _ensure_pinchtab_profile_tab(profile_name: str) -> str:
    if not profile_name:
        return ""
    binary = os.environ.get("PINCHTAB_BINARY", "pinchtab")
    if shutil.which(binary) is None and "/" not in binary:
        return ""
    server = _pinchtab_server_for_profile(binary, profile_name)
    if not server:
        return f"Could not find a running PinchTab instance for profile {profile_name} after start."
    tab = _pinchtab_tab_for_server(binary, server)
    if tab:
        return f"PinchTab profile {profile_name} has active tab {tab}."
    tab = _open_pinchtab_tab(binary, server)
    if tab:
        return f"Created tab {tab} for PinchTab profile {profile_name}."
    return f"Could not create a tab for PinchTab profile {profile_name}."


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
    try:
        instances = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ""
    if not isinstance(instances, list):
        return ""
    for item in instances:
        if isinstance(item, dict) and item.get("profileName") == profile and item.get("status") == "running":
            return str(item.get("url") or "")
    return ""


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
