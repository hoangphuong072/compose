from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


def run_codex_cli(prompt: str, workspace: Path) -> str:
    binary = os.environ.get("CODEX_BINARY", "codex")
    resolved_binary = _resolve_binary(binary)
    if resolved_binary is None:
        return (
            "Codex CLI was not found on PATH. Install or expose it first, "
            "or set CODEX_BINARY to the full binary path."
        )

    prompt = prompt.strip()
    if not prompt:
        return "Usage: /codex <request for Codex CLI>"

    result = _run_codex(resolved_binary, prompt, workspace)
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
    if result.returncode != 0:
        suffix = f"\n\n{output}" if output else ""
        return f"Codex CLI failed with exit code {result.returncode}.{suffix}"[:12000]
    return (output or "(Codex CLI completed with no output)")[:12000]


def _run_codex(binary: str, prompt: str, workspace: Path) -> subprocess.CompletedProcess[str]:
    timeout = int(os.environ.get("MINIHERMES_CODEX_TIMEOUT", "900"))
    args = [
        binary,
        "exec",
        "--cd",
        str(workspace),
        "--sandbox",
        os.environ.get("MINIHERMES_CODEX_SANDBOX", "workspace-write"),
        "--ask-for-approval",
        os.environ.get("MINIHERMES_CODEX_APPROVAL", "never"),
        "--color",
        "never",
        "-",
    ]
    model = os.environ.get("MINIHERMES_CODEX_MODEL", "").strip()
    if model:
        args[2:2] = ["--model", model]
    profile = os.environ.get("MINIHERMES_CODEX_PROFILE", "").strip()
    if profile:
        args[2:2] = ["--profile", profile]
    if _bool_env("MINIHERMES_CODEX_SKIP_GIT_CHECK", True):
        args.insert(-1, "--skip-git-repo-check")
    if _bool_env("MINIHERMES_CODEX_EPHEMERAL", False):
        args.insert(-1, "--ephemeral")

    try:
        return subprocess.run(
            args,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, exc.stdout or "", exc.stderr or "Codex CLI timed out.")
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def _resolve_binary(binary: str) -> str | None:
    expanded = os.path.expanduser(binary)
    if "/" in expanded:
        return expanded if os.path.isfile(expanded) and os.access(expanded, os.X_OK) else None
    return shutil.which(expanded)


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}
