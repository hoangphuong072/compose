from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class HttpResponse:
    status: int
    body: str

    def json(self) -> Any:
        return json.loads(self.body)


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    tls_verify: bool = True,
) -> HttpResponse:
    return request_json("POST", url, payload=payload, headers=headers, timeout=timeout, tls_verify=tls_verify)


def get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    tls_verify: bool = True,
) -> HttpResponse:
    return request_json("GET", url, payload=None, headers=headers, timeout=timeout, tls_verify=tls_verify)


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    tls_verify: bool = True,
) -> HttpResponse:
    config = [f'url = "{_curl_quote(url)}"', f"request = {method}", "header = Content-Type: application/json"]
    for key, value in (headers or {}).items():
        config.append(f"header = {_curl_quote(f'{key}: {value}')}")
    if not tls_verify:
        config.append("insecure")

    args = [
        "curl",
        "--silent",
        "--show-error",
        "--max-time",
        str(timeout),
        "--write-out",
        "\n%{http_code}",
        "--config",
        "-",
    ]

    payload_path = ""
    if payload is not None:
        payload_path = _payload_path()
        fd = os.open(payload_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        args.extend(["--data-binary", f"@{payload_path}"])

    try:
        completed = subprocess.run(
            args,
            input="\n".join(config) + "\n",
            text=True,
            capture_output=True,
            timeout=timeout + 5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HttpError(0, str(exc)) from exc
    finally:
        if payload_path:
            try:
                os.unlink(payload_path)
            except OSError:
                pass

    stdout = completed.stdout
    body, _, status_text = stdout.rpartition("\n")
    try:
        status = int(status_text)
    except ValueError:
        status = 0
        body = stdout
    if completed.returncode != 0 or status >= 400 or status == 0:
        detail = body or completed.stderr.strip()
        raise HttpError(status, detail)
    return HttpResponse(status=status, body=body)


def _curl_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _payload_path() -> str:
    root = Path(os.environ.get("TMPDIR", "/tmp"))
    return str(root / f"minihermes-curl-{os.getpid()}-{time.time_ns()}.json")
