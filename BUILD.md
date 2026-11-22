# CokCopilot — Development Guide

## Layout

```
backend/                 Python (FastAPI) — run with `uv`
  cokcopilot/
    __main__.py          command line: local app, `serve` (team server), `users` (access keys)
    app.py               HTTP API (/api/...), auth middleware, serves frontend/dist
    auth.py              users, access keys (hashed), signed session cookies
    groups.py            groups: owners, members, invitations (groups.json)
    chat.py              one reply: prompt planning, streaming, MCP tool loop, approvals
    llm.py               OpenAI-compatible client (SSE streaming, tool calls, embeddings)
    mcp_manager.py       MCP servers: connect (stdio/http/sse), list and call tools, mcp.json
    knowledge.py         knowledge base: folder sync, chunking, embeddings, retrieval, watcher
    rag.py               knowledge-only prompt, hidden SOURCES line, References footer
    memory.py            long-term memory facts
    skills.py            SKILL.md skills: parse, install (folder/.md/.zip), create
    parsers.py           PDF/DOCX/text extraction
    models.py, config.py data shapes, data folder, settings and conversation files
    state.py             shared app state, per-user context (settings, memory, chats), background tasks
  tests/                 pytest (Rust-test ports, MCP end-to-end, server-mode auth and isolation)
frontend/                React + TypeScript (Vite)
  src/App.tsx            app state, streaming, layout
  src/api.ts             typed API client and SSE reader
  src/components/        Sidebar, Composer, MessageView, ToolCallCard, Markdown, ui helpers
  src/panels/            Settings, Knowledge, Memory, Skills, MCP, Users, Groups dialogs
  src/styles.css         design tokens (light/dark) and all styles
desktop/                 Electron shell (TypeScript) → portable .exe that connects to a server
  src/main.ts            window, server address, navigation rules, menu
  static/connect.html    "connect to server" screen
deploy/iis/web.config    sample IIS (HttpPlatformHandler) config — see docs/DEPLOY.md
legacy-rust/             the previous Rust/egui app (not built anymore)
```

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`)
- Node.js 18+ (`winget install OpenJS.NodeJS.LTS`)

## Develop with hot reload

Run the backend in one terminal. `--dev` runs the API only and lets it accept requests proxied by the Vite dev server (it works the same in cmd, PowerShell and bash):

```bash
cd backend && uv run python -m cokcopilot --dev
```

Run the frontend in another terminal, then open http://localhost:5173:

```bash
cd frontend && npm install && npm run dev
```

## Build and run

```bash
cd frontend && npm run build
```

```bash
cd backend && uv run python -m cokcopilot
```

Launcher options: `--browser` always opens the browser, `--server` runs the API only, and `--port N` picks the port (default 8765, or the next free one).

## Tests

```bash
cd backend && uv run pytest
```

```bash
cd frontend && npm run typecheck
```

The MCP tests start `backend/tests/echo_server.py`, a tiny stdio MCP server with `add` and `shout` tools. You can also add it in the MCP panel to try tools by hand. Use the `.venv` Python as the command and the script path as the argument.

## Notes

- MCP uses the official `mcp` Python SDK (v2). Each server runs in its own asyncio task; see the docstring in `mcp_manager.py`.
- A reply runs as a background task that feeds a queue. `POST /api/chat` relays that queue as Server-Sent Events, so Stop (`POST /api/streams/{id}/stop`) can cancel it at any time. Tool approvals arrive on `POST /api/streams/{id}/approve`.
- On-disk formats match the Rust version. New optional message fields: `attachments` and `tool_calls`.

## Server mode locally

To try the team setup on your own machine, run:

```bash
cd backend && uv run python -m cokcopilot serve --port 8780 --data-dir ../data-server
```

It prints an admin access key on first start. Open http://127.0.0.1:8780 and sign in with it. Deployment is covered in [docs/DEPLOY.md](docs/DEPLOY.md).

## Desktop app

```bash
cd desktop && npm install && npm start
```

`npm start` runs the app from source. `npm run dist` builds the portable `.exe` and a zip into `desktop/release/`. See [desktop/BUILD.md](desktop/BUILD.md) for the full steps, versioning and code signing.

If a Windows build fails with "Cannot create symbolic link", turn on Windows **Developer Mode** (Settings → System → For developers), or run the build once as administrator. electron-builder's signing-tools archive contains macOS symlinks.
