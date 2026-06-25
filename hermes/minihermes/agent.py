from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AgentConfig
from .llm import ChatCompletionsClient
from .logging import log_event, preview
from .memory import JsonlMemory
from .tools import ToolRegistry, create_default_registry


SYSTEM_TEMPLATE = """You are MiniHermes, a concise Python agent inspired by Hermes Agent.

Core behavior:
- Think step by step privately, then respond clearly.
- Use tools when you need file access, command output, or memory.
- Never claim you used a tool unless a tool call was actually made.
- Keep changes inside the configured workspace.

Workspace: {workspace}
Persistent memory:
{memory}
"""


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False

    def add(self, raw: dict[str, Any] | None) -> None:
        if not raw:
            return
        self.prompt_tokens += int(raw.get("prompt_tokens", 0) or 0)
        self.completion_tokens += int(raw.get("completion_tokens", 0) or 0)
        self.total_tokens += int(raw.get("total_tokens", 0) or 0)

    def ensure_total(self) -> None:
        if not self.total_tokens:
            self.total_tokens = self.prompt_tokens + self.completion_tokens

    def report(self) -> str:
        label = "Token usage"
        if self.estimated:
            label += " (estimated)"
        return (
            f"{label}: prompt={self.prompt_tokens}, "
            f"completion={self.completion_tokens}, total={self.total_tokens}"
        )


@dataclass
class AgentResult:
    content: str
    usage: TokenUsage

    def with_usage_report(self) -> str:
        return f"{self.content}\n\n{self.usage.report()}"


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        client: ChatCompletionsClient | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.config = config
        self.memory = JsonlMemory(config.memory_path)
        self.registry = registry or create_default_registry(
            config.workspace,
            self.memory,
            default_pinchtab_profiles=config.pinchtab_profiles,
        )
        self.client = client or ChatCompletionsClient(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            temperature=config.temperature,
            tls_verify=config.tls_verify,
        )
        self.messages: list[dict[str, Any]] = []
        self.last_usage = TokenUsage()
        self.last_provider_exchanges: list[dict[str, Any]] = []
        self.reset()
        log_event(
            self.config,
            "agent.init",
            model=config.model,
            base_url=config.base_url,
            workspace=str(config.workspace),
            home_dir=str(config.home_dir),
            enabled_toolsets=config.enabled_toolsets,
            max_iterations=config.max_iterations,
            temperature=config.temperature,
            tls_verify=config.tls_verify,
        )

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self._system_prompt()}]
        log_event(self.config, "agent.reset", message_count=len(self.messages), workspace=str(self.config.workspace))

    def _system_prompt(self) -> str:
        return SYSTEM_TEMPLATE.format(workspace=self.config.workspace, memory=self.memory.prompt_block())

    def ask(self, prompt: str) -> str:
        return self.ask_with_usage(prompt).content

    def context_preview_for(self, prompt: str, max_chars: int | None = None) -> str:
        self.messages[0] = {"role": "system", "content": self._system_prompt()}
        context = [*self.messages, {"role": "user", "content": prompt}]
        return format_context(context, max_chars or self.config.context_max_chars)

    def ask_with_usage(self, prompt: str) -> AgentResult:
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        self.messages[0] = {"role": "system", "content": self._system_prompt()}
        self.messages.append({"role": "user", "content": prompt})
        tools = self.registry.schemas(self.config.enabled_toolsets)
        usage = TokenUsage()
        saw_provider_usage = False
        provider_exchanges: list[dict[str, Any]] = []
        log_event(
            self.config,
            "agent.ask.start",
            run_id=run_id,
            prompt_preview=preview(prompt, self.config.log_max_value_chars),
            prompt_length=len(prompt),
            message_count_before=len(self.messages) - 1,
            tool_schema_count=len(tools),
            enabled_toolsets=self.config.enabled_toolsets,
        )

        for iteration in range(1, self.config.max_iterations + 1):
            log_event(
                self.config,
                "agent.iteration.start",
                run_id=run_id,
                iteration=iteration,
                message_count=len(self.messages),
                tool_schema_count=len(tools),
            )
            llm_started = time.monotonic()
            log_event(
                self.config,
                "llm.request.start",
                run_id=run_id,
                iteration=iteration,
                model=self.config.model,
                base_url=self.config.base_url,
                message_count=len(self.messages),
                tool_schema_count=len(tools),
            )
            try:
                assistant_message = self.client.complete(self.messages, tools)
            except Exception as exc:
                log_event(
                    self.config,
                    "llm.request.error",
                    "ERROR",
                    run_id=run_id,
                    iteration=iteration,
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                raise
            provider_exchanges.append(
                {
                    "request": getattr(self.client, "last_request", {}),
                    "response": getattr(self.client, "last_response", {}),
                }
            )
            raw_usage = assistant_message.pop("_usage", None)
            if raw_usage:
                saw_provider_usage = True
                usage.add(raw_usage)
            self.messages.append(assistant_message)
            tool_calls = assistant_message.get("tool_calls") or []
            log_event(
                self.config,
                "llm.request.end",
                run_id=run_id,
                iteration=iteration,
                duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                has_tool_calls=bool(tool_calls),
                tool_call_count=len(tool_calls),
                content_preview=preview(assistant_message.get("content") or "", self.config.log_max_value_chars),
                usage=raw_usage or {},
            )
            if not tool_calls:
                content = assistant_message.get("content") or ""
                if not saw_provider_usage:
                    usage = self._estimate_usage(prompt, content)
                usage.ensure_total()
                self.last_usage = usage
                self.last_provider_exchanges = provider_exchanges
                self._save_session_turn(prompt, content, run_id=run_id)
                log_event(
                    self.config,
                    "agent.ask.end",
                    run_id=run_id,
                    duration_ms=round((time.monotonic() - started) * 1000, 2),
                    iterations=iteration,
                    response_preview=preview(content, self.config.log_max_value_chars),
                    response_length=len(content),
                    usage=usage.__dict__,
                    message_count_after=len(self.messages),
                )
                return AgentResult(content=content, usage=usage)

            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments", "{}")
                tool_started = time.monotonic()
                log_event(
                    self.config,
                    "tool.call.start",
                    run_id=run_id,
                    iteration=iteration,
                    tool_call_id=call.get("id", name),
                    tool_name=name,
                    arguments=_json_arguments(arguments),
                )
                result = self.registry.run(name, arguments)
                log_event(
                    self.config,
                    "tool.call.end",
                    run_id=run_id,
                    iteration=iteration,
                    tool_call_id=call.get("id", name),
                    tool_name=name,
                    duration_ms=round((time.monotonic() - tool_started) * 1000, 2),
                    result_preview=preview(result, self.config.log_max_value_chars),
                    result_length=len(result),
                    failed=result.startswith(f"Tool {name} failed:") or result.startswith("Unknown tool:"),
                )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", name),
                        "name": name,
                        "content": result,
                    }
                )

        content = "I stopped because the agent reached its iteration limit."
        if not saw_provider_usage:
            usage = self._estimate_usage(prompt, content)
        usage.ensure_total()
        self.last_usage = usage
        self.last_provider_exchanges = provider_exchanges
        log_event(
            self.config,
            "agent.ask.iteration_limit",
            "WARNING",
            run_id=run_id,
            max_iterations=self.config.max_iterations,
            message_count=len(self.messages),
            usage=usage.__dict__,
        )
        return AgentResult(content=content, usage=usage)

    def _save_session_turn(self, prompt: str, response: str, run_id: str = "") -> None:
        stamp = datetime.now().strftime("%Y%m%d")
        path = self.config.sessions_dir / f"{stamp}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"user": prompt, "assistant": response}, ensure_ascii=False) + "\n")
        log_event(
            self.config,
            "session.turn.saved",
            run_id=run_id,
            path=str(path),
            prompt_length=len(prompt),
            response_length=len(response),
        )

    def available_tools(self) -> list[str]:
        return self.registry.names()

    def history(self) -> list[dict[str, Any]]:
        return self.messages[:]

    def _estimate_usage(self, prompt: str, content: str) -> TokenUsage:
        prompt_text = json.dumps(self.messages, ensure_ascii=False)
        prompt_tokens = _estimate_tokens(prompt_text or prompt)
        completion_tokens = _estimate_tokens(content)
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated=True,
        )


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _json_arguments(arguments: str | dict[str, Any]) -> Any:
    if isinstance(arguments, dict):
        return arguments
    try:
        return json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return arguments


def format_context(messages: list[dict[str, Any]], max_chars: int) -> str:
    parts: list[str] = []
    for index, message in enumerate(messages, start=1):
        role = message.get("role", "unknown")
        content = message.get("content")
        if content is None and message.get("tool_calls"):
            content = json.dumps(message.get("tool_calls"), ensure_ascii=False)
        parts.append(f"[{index}] {role}\n{content or ''}")

    text = "\n\n".join(parts)
    if max_chars > 0 and len(text) > max_chars:
        omitted = len(text) - max_chars
        text = text[:max_chars] + f"\n\n... truncated {omitted} characters ..."
    return text
