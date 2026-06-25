from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess


INSTALL_SCRIPT_COMMAND = "curl -fsSL https://pinchtab.com/install.sh | bash"
BREW_INSTALL_COMMAND = ["brew", "install", "pinchtab/tap/pinchtab"]
GO_INSTALL_COMMAND = ["go", "install", "github.com/pinchtab/pinchtab/cmd/pinchtab@latest"]


@dataclass(frozen=True)
class _InstallStep:
    name: str
    args: list[str]
    timeout: int


def install_pinchtab() -> str:
    requested_binary = os.environ.get("PINCHTAB_BINARY", "pinchtab")
    binary = _resolve_binary(requested_binary)
    lines: list[str] = []

    if binary is None:
        if "/" in requested_binary:
            return f"PINCHTAB_BINARY points to a missing executable: {requested_binary}"
        binary = _install_and_find_binary(lines, requested_binary)
        if binary is None:
            return "\n".join(lines)
    else:
        lines.append(f"PinchTab CLI is already available as `{binary}`.")

    daemon_install = _run_step([binary, "daemon", "install"], timeout=60)
    if daemon_install.returncode != 0:
        return "\n".join(lines + [_format_failure("PinchTab daemon install failed", daemon_install)])
    lines.append("PinchTab daemon install completed.")

    try:
        subprocess.Popen(
            [binary, "daemon"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        lines.append(f"Could not start PinchTab daemon automatically: {exc}")
        lines.append(f"Run manually: `{binary} daemon`")
    else:
        lines.append("PinchTab daemon start requested.")

    return "\n".join(lines)


def _install_and_find_binary(lines: list[str], requested_binary: str) -> str | None:
    failures: list[str] = []
    for step in _install_steps():
        result = _run_step(step.args, timeout=step.timeout)
        if result.returncode == 0:
            binary = _resolve_binary(requested_binary)
            if binary is not None:
                lines.append(f"{step.name} completed.")
                lines.append(f"PinchTab CLI found as `{binary}`.")
                return binary
            lines.append(f"{step.name} completed, but `{requested_binary}` was not found on PATH.")
            continue
        failures.append(_format_failure(f"{step.name} failed", result))

    lines.append("Could not install PinchTab automatically.")
    lines.extend(failures)
    lines.append(
        "Install manually with `curl -fsSL https://pinchtab.com/install.sh | bash`, "
        "then make sure `pinchtab` is on PATH or set PINCHTAB_BINARY."
    )
    return None


def _install_steps() -> list[_InstallStep]:
    steps = [_InstallStep("PinchTab install script", ["bash", "-c", INSTALL_SCRIPT_COMMAND], 120)]
    if shutil.which("brew"):
        steps.append(_InstallStep("Homebrew PinchTab install", BREW_INSTALL_COMMAND, 180))
    if shutil.which("go"):
        steps.append(_InstallStep("Go PinchTab install", GO_INSTALL_COMMAND, 180))
    return steps


def _resolve_binary(binary: str) -> str | None:
    expanded = os.path.expanduser(binary)
    if "/" in expanded:
        return expanded if os.path.isfile(expanded) and os.access(expanded, os.X_OK) else None

    found = shutil.which(expanded)
    if found:
        return found

    for candidate in _common_binary_paths(expanded):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _common_binary_paths(binary: str) -> list[str]:
    home = Path.home()
    return [
        str(home / ".local" / "bin" / binary),
        str(home / "go" / "bin" / binary),
        f"/opt/homebrew/bin/{binary}",
        f"/usr/local/bin/{binary}",
        f"/usr/bin/{binary}",
    ]


def _run_step(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, exc.stdout or "", exc.stderr or "Command timed out.")
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def _format_failure(title: str, result: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
    suffix = f"\n{output}" if output else ""
    return f"{title} with exit code {result.returncode}.{suffix}"
