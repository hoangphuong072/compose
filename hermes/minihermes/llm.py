from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .http import HttpError, post_json


class LLMError(RuntimeError):
    pass


@dataclass
class ChatCompletionsClient:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.2
    timeout: int = 120
    tls_verify: bool = True
    last_request: dict[str, Any] = field(default_factory=dict)
    last_response: dict[str, Any] = field(default_factory=dict)

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError(
                "Missing API key. Set MINIHERMES_API_KEY or use `minihermes init` and edit ~/.minihermes/config.json."
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        self.last_request = json.loads(json.dumps(payload))

        url = self.base_url.rstrip("/") + "/chat/completions"
        try:
            response = post_json(
                url,
                payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=self.timeout,
                tls_verify=self.tls_verify,
            )
            data = response.json()
            self.last_response = data
        except HttpError as exc:
            raise LLMError(f"LLM request failed with HTTP {exc.status}: {exc.body}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"Unexpected LLM response: {response.body}") from exc

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response: {data}") from exc
        if data.get("usage"):
            message["_usage"] = data["usage"]
        return message
