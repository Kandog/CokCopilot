# ADR-0001: Tech Stack

## Status
Superseded in part by ADR-0002 (Rust rewrite)

## Context
Building a desktop AI chat application similar to Microsoft Copilot / LM Studio Chat.

## Decision

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.11+ | User preference, strong AI/ML ecosystem |
| GUI Framework | PyQt6 | Mature, native look, same tech as LM Studio |
| AI Protocol | OpenAI-compatible API | Universal standard — covers Ollama, LM Studio, vLLM, Together, Groq, etc. |
| Config Storage | Plain JSON file | Simple, personal desktop app — no encryption needed |
| Markdown Rendering | `markdown` + `Pygments` | Battle-tested Python markdown rendering with syntax highlighting |
| Document Parsing | `PyMuPDF` (PDF), `python-docx` (DOCX), `txt` (plain) | Local text extraction for document uploads |

## Consequences
- Single protocol keeps codebase small and maintainable
- PyQt6 provides native OS look and feel on Windows/macOS/Linux
- Plain config file is easy to debug and hand-edit
- Local text extraction means no external dependencies for document processing
