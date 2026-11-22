# ADR-0003: TypeScript frontend + Python backend, with MCP

## Status
Accepted. Supersedes ADR-0002 (Rust/egui). The Rust code is kept in `legacy-rust/`.

## Context
The owner is more comfortable with TypeScript and Python than with Rust. They also want a Copilot-like UI and support for MCP (Model Context Protocol) tool servers. egui draws its own widgets, which made a polished Copilot-style UI hard to build.

## Decision

| Layer | Choice | Rationale |
|-------|--------|-----------|
| UI | React 18 + TypeScript, built with Vite | Familiar stack; CSS makes the Copilot look (gradients, rounded composer, cards) easy |
| Markdown | `react-markdown` + `remark-gfm` + `rehype-highlight` | Tables, code highlighting, safe by default (no raw HTML) |
| Backend | Python 3.11+, FastAPI + uvicorn, managed with `uv` | Familiar; the official MCP SDK is available in Python |
| LLM client | `httpx` against the OpenAI-compatible API | Same no-thinking fields and retries as before, plus streamed tool calls |
| MCP | Official `mcp` SDK v2 (stdio, Streamable HTTP, SSE) | Standard; config file format compatible with Claude Desktop / VS Code |
| Streaming | Server-Sent Events from `POST /api/chat`; each reply is its own task | Stop and tool approvals work through separate requests |
| Desktop shell | `pywebview` if installed, otherwise the default browser | No Electron/Tauri toolchain needed |
| Storage | Same JSON files and folder as before | Existing users keep their data |

## Consequences
- Running needs Python (with uv); building the UI needs Node.js. There is no single-file .exe yet. A PyInstaller bundle could be added later.
- Tool calls require user approval by default, because MCP servers can act on the machine.
- The local API rejects cross-origin requests so websites can't add MCP servers (which would run commands).
