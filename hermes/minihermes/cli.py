from __future__ import annotations

import argparse
import os
import threading
import time
import sys
from pathlib import Path

from .agent import Agent
from .codex import run_codex_cli
from .config import load_config, write_default_config
from .llm import LLMError
from .logging import latest_log_path, tail_lines
from .pinchtab import install_pinchtab
from .telegram import TelegramBot, TelegramError
from .web import run_web


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MiniHermes agent CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Create ~/.minihermes/config.json")
    sub.add_parser("tools", help="List available tools")

    logs = sub.add_parser("logs", help="Show recent structured agent logs")
    logs.add_argument("--lines", type=int, default=100)
    logs.add_argument("--tail", action="store_true")

    ask = sub.add_parser("ask", help="Ask one question and print the answer")
    ask.add_argument("prompt", nargs="+")
    ask.add_argument("--workspace", type=Path)

    chat = sub.add_parser("chat", help="Start an interactive chat session")
    chat.add_argument("--workspace", type=Path)

    telegram = sub.add_parser("telegram", help="Run Telegram long-polling chat bot")
    telegram.add_argument("--workspace", type=Path)

    web = sub.add_parser("web", help="Run browser chat UI")
    web.add_argument("--workspace", type=Path)
    web.add_argument("--host", default=None)
    web.add_argument("--port", type=int, default=None)

    serve = sub.add_parser("serve", help="Run web UI and Telegram bot when configured")
    serve.add_argument("--workspace", type=Path)
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        path = write_default_config()
        print(f"Config ready: {path}")
        return 0

    if args.command == "tools":
        cfg = load_config()
        agent = Agent(cfg)
        print("\n".join(agent.available_tools()))
        return 0

    if args.command == "logs":
        cfg = load_config()
        return _print_logs(cfg.logs_dir, args.lines, args.tail)

    if args.command == "ask":
        cfg = load_config(workspace=args.workspace)
        agent = Agent(cfg)
        return _print_answer(agent, " ".join(args.prompt))

    if args.command == "telegram":
        cfg = load_config(workspace=args.workspace)
        try:
            TelegramBot(cfg).run_forever()
            return 0
        except TelegramError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    if args.command == "web":
        cfg = load_config(workspace=args.workspace)
        run_web(cfg, host=args.host, port=args.port)
        return 0

    if args.command == "serve":
        cfg = load_config(workspace=args.workspace)
        if cfg.telegram_token:
            thread = threading.Thread(target=_run_telegram, args=(cfg,), daemon=True)
            thread.start()
        else:
            print("TELEGRAM_BOT_TOKEN is not set; running web UI only.")
        run_web(cfg, host=args.host, port=args.port)
        return 0

    if args.command == "chat" or args.command is None:
        cfg = load_config(workspace=getattr(args, "workspace", None))
        agent = Agent(cfg)
        print("MiniHermes chat. Commands: /tools, /codex, /install_pinchtab, /new, /exit")
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not prompt:
                continue
            if prompt in {"/exit", "/quit"}:
                return 0
            if prompt in {"/new", "/reset"}:
                agent.reset()
                print("Started a fresh conversation.")
                continue
            if prompt == "/tools":
                print("\n".join(agent.available_tools()))
                continue
            if prompt == "/codex" or prompt.startswith("/codex "):
                print(run_codex_cli(_slash_args(prompt), cfg.workspace))
                continue
            if prompt == "/install_pinchtab":
                print(install_pinchtab())
                continue
            _print_answer(agent, prompt)

    parser.print_help()
    return 1


def _print_answer(agent: Agent, prompt: str) -> int:
    try:
        if agent.config.show_context:
            print("Context sent to model:")
            print(agent.context_preview_for(prompt))
            print()
        print(agent.ask_with_usage(prompt).with_usage_report())
        return 0
    except LLMError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def _slash_args(text: str) -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _run_telegram(cfg) -> None:
    try:
        TelegramBot(cfg).run_forever()
    except TelegramError as exc:
        print(f"Telegram error: {exc}", file=sys.stderr)
        if os.environ.get("MINIHERMES_TELEGRAM_REQUIRED", "").lower() in {"1", "true", "yes", "on"}:
            raise


def _print_logs(logs_dir: Path, lines: int, follow: bool) -> int:
    path = latest_log_path(logs_dir)
    for line in tail_lines(path, max(1, lines)):
        print(line)
    if not follow:
        if not path.exists():
            print(f"No log file found yet: {path}", file=sys.stderr)
            return 1
        return 0

    position = path.stat().st_size if path.exists() else 0
    try:
        while True:
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    f.seek(position)
                    for line in f:
                        print(line, end="")
                    position = f.tell()
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
