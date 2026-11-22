# ADR-0002: Rewrite in Rust

## Status
Accepted — supersedes the language, GUI, markdown and parsing choices in ADR-0001

## Context
The Python/PyQt6 build shipped as a large PyInstaller folder (`CokCopilot.exe` plus `_internal/`) and needed Python and several packages to build. The app is small enough to port in one go.

## Decision

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Rust (stable, MSVC) | Single self-contained executable, no runtime to bundle |
| GUI Framework | egui / eframe 0.31 | Pure Rust, immediate mode suits token streaming, small binary |
| Markdown Rendering | `egui_commonmark` for prose, own code-block widget with `egui_extras` (syntect) highlighting | Keeps the per-block header + Copy button |
| HTTP / SSE | `ureq` (blocking) on worker threads | Per-read timeouts match the Python client; no async runtime needed |
| Document Parsing | `pdf-extract` (PDF), `zip` + `quick-xml` (DOCX), plain read (text) | All local, no external tools |
| Folder watching | `notify` (recursive) + debounce + 10 s poll | Same layered detection as before |
| Dialogs / files | `rfd` (native dialogs), `trash` (Recycle Bin), `open` | Native look for file pickers and message boxes |
| Config Storage | Plain JSON, same shapes as before | Existing `config.json`, conversations, memory and knowledge index load unchanged |

## Consequences
- The installer ships one `.exe`; building needs only Rust and NSIS
- On-disk formats are unchanged, so users keep their data when upgrading
- egui draws its own widgets rather than native ones; the warm light/dark palette is reproduced in `src/ui/theme.rs`
