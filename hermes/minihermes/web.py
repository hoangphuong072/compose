from __future__ import annotations

import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .agent import Agent
from .config import AgentConfig


class WebApp:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._agents: dict[str, Agent] = {}
        self._lock = threading.Lock()

    def agent_for(self, session_id: str) -> Agent:
        key = session_id.strip() or "default"
        with self._lock:
            agent = self._agents.get(key)
            if agent is None:
                agent = Agent(self.config)
                self._agents[key] = agent
            return agent

    def reset(self, session_id: str) -> None:
        self.agent_for(session_id).reset()


class HermesHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], app: WebApp) -> None:
        super().__init__(server_address, HermesHandler)
        self.app = app


class HermesHandler(BaseHTTPRequestHandler):
    server: HermesHTTPServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(INDEX_HTML)
            return
        if path == "/health":
            self._send_json({"ok": True, "service": "minihermes-web"})
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/chat":
            self._handle_chat()
            return
        if path == "/api/reset":
            self._handle_reset()
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _handle_chat(self) -> None:
        payload = self._read_json()
        message = str(payload.get("message", "")).strip()
        session_id = str(payload.get("session_id", "default")).strip() or "default"
        if not message:
            self._send_json({"error": "Message is required"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            result = self.server.app.agent_for(session_id).ask_with_usage(message)
        except Exception as exc:
            self._send_json(
                {"error": str(exc), "error_type": type(exc).__name__},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self._send_json(
            {
                "message": result.content,
                "usage": {
                    "prompt_tokens": result.usage.prompt_tokens,
                    "completion_tokens": result.usage.completion_tokens,
                    "total_tokens": result.usage.total_tokens,
                    "estimated": result.usage.estimated,
                },
            }
        )

    def _handle_reset(self) -> None:
        payload = self._read_json()
        session_id = str(payload.get("session_id", "default")).strip() or "default"
        self.server.app.reset(session_id)
        self._send_json({"ok": True})

    def _read_json(self) -> dict[str, Any]:
        length = min(int(self.headers.get("Content-Length", "0") or "0"), 1024 * 1024)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def run_web(config: AgentConfig, host: str | None = None, port: int | None = None) -> None:
    bind_host = host or os.environ.get("MINIHERMES_WEB_HOST", "0.0.0.0")
    bind_port = port or int(os.environ.get("MINIHERMES_WEB_PORT", "8080"))
    server = HermesHTTPServer((bind_host, bind_port), WebApp(config))
    print(f"MiniHermes web UI is running on http://{bind_host}:{bind_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MiniHermes</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f4;
      --panel: #ffffff;
      --text: #17201a;
      --muted: #657067;
      --line: #d9ded8;
      --accent: #0f766e;
      --accent-dark: #115e59;
      --user: #0f766e;
      --assistant: #eef2f0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
    }
    .app {
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 100vh;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.92);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .status {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      color: var(--muted);
      font-size: 13px;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #16a34a;
      flex: 0 0 auto;
    }
    main {
      width: min(980px, 100%);
      margin: 0 auto;
      padding: 22px 14px 130px;
    }
    .messages {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .message {
      display: grid;
      gap: 6px;
      max-width: min(760px, 92%);
    }
    .message.user { align-self: flex-end; }
    .message.assistant { align-self: flex-start; }
    .label {
      color: var(--muted);
      font-size: 12px;
      padding: 0 4px;
    }
    .bubble {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
      line-height: 1.5;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      box-shadow: 0 1px 2px rgba(22, 32, 26, 0.05);
    }
    .user .bubble {
      background: var(--user);
      color: white;
      border-color: var(--user);
    }
    .assistant .bubble {
      background: var(--assistant);
      color: var(--text);
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      padding: 0 4px;
    }
    form {
      position: fixed;
      left: 0;
      right: 0;
      bottom: 0;
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
      padding: 12px;
    }
    .composer {
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 10px;
      width: min(980px, 100%);
      margin: 0 auto;
      align-items: end;
    }
    textarea {
      width: 100%;
      min-height: 48px;
      max-height: 180px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      color: var(--text);
      font: inherit;
      line-height: 1.4;
      outline: none;
    }
    textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.14);
    }
    button {
      min-height: 48px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 14px;
      background: white;
      color: var(--text);
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: white;
    }
    button.primary:hover { background: var(--accent-dark); }
    button:disabled {
      cursor: wait;
      opacity: 0.62;
    }
    @media (max-width: 620px) {
      header { padding: 12px; }
      .status span { display: none; }
      main { padding-inline: 10px; }
      .message { max-width: 100%; }
      .composer {
        grid-template-columns: 1fr;
      }
      button {
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <h1>MiniHermes</h1>
      <div class="status"><span class="dot"></span><span id="status">Ready</span></div>
    </header>
    <main>
      <div id="messages" class="messages">
        <div class="message assistant">
          <div class="label">Hermes</div>
          <div class="bubble">Xin chào. Bạn muốn tôi làm gì?</div>
        </div>
      </div>
    </main>
    <form id="form">
      <div class="composer">
        <textarea id="input" placeholder="Nhập tin nhắn..." autocomplete="off"></textarea>
        <button type="button" id="reset">Reset</button>
        <button class="primary" id="send" type="submit">Send</button>
      </div>
    </form>
  </div>
  <script>
    const messages = document.querySelector("#messages");
    const input = document.querySelector("#input");
    const form = document.querySelector("#form");
    const send = document.querySelector("#send");
    const reset = document.querySelector("#reset");
    const statusEl = document.querySelector("#status");
    const sessionId = localStorage.getItem("minihermes_session_id") || crypto.randomUUID();
    localStorage.setItem("minihermes_session_id", sessionId);

    function setBusy(isBusy, text) {
      send.disabled = isBusy;
      reset.disabled = isBusy;
      statusEl.textContent = text || (isBusy ? "Thinking" : "Ready");
    }

    function addMessage(role, text, meta) {
      const item = document.createElement("div");
      item.className = `message ${role}`;
      const label = document.createElement("div");
      label.className = "label";
      label.textContent = role === "user" ? "You" : "Hermes";
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = text;
      item.append(label, bubble);
      if (meta) {
        const metaEl = document.createElement("div");
        metaEl.className = "meta";
        metaEl.textContent = meta;
        item.append(metaEl);
      }
      messages.append(item);
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || `Request failed: ${response.status}`);
      }
      return data;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      addMessage("user", text);
      setBusy(true, "Thinking");
      try {
        const data = await postJson("/api/chat", { session_id: sessionId, message: text });
        const usage = data.usage ? `tokens: ${data.usage.total_tokens}` : "";
        addMessage("assistant", data.message || "", usage);
        setBusy(false, "Ready");
      } catch (error) {
        addMessage("assistant", error.message || String(error));
        setBusy(false, "Error");
      }
    });

    reset.addEventListener("click", async () => {
      setBusy(true, "Resetting");
      try {
        await postJson("/api/reset", { session_id: sessionId });
        messages.innerHTML = "";
        addMessage("assistant", "Đã bắt đầu phiên mới.");
        setBusy(false, "Ready");
      } catch (error) {
        addMessage("assistant", error.message || String(error));
        setBusy(false, "Error");
      }
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        form.requestSubmit();
      }
    });
  </script>
</body>
</html>
"""
