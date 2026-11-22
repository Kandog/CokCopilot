# CokCopilot

A Copilot-style AI chat app for any OpenAI-compatible API, with MCP tools, knowledge bases, long-term memory and reusable skills. Use it on your own computer, or run it as a server for a team.

| Part | Folder | What it is |
|---|---|---|
| Frontend | `frontend/` | React + TypeScript (Vite). The chat UI, served by the backend. |
| Backend | `backend/` | Python (FastAPI). Talks to the model API, runs MCP servers, indexes documents, stores data, signs users in. |
| Desktop app | `desktop/` | Electron (TypeScript). A portable `.exe` that connects to a CokCopilot server. |
| IIS sample | `deploy/iis/` | `web.config` for hosting the server behind IIS. |

It runs in two modes:

- **Personal (local):** one user, no sign-in, reachable only from `127.0.0.1`. Opens in its own window (with `pywebview`) or your browser.
- **Team server:** `python -m cokcopilot serve`. Users sign in with access keys. Put it behind IIS for HTTPS.

> The previous Rust/egui version is kept in `legacy-rust/` for reference. Your existing `config.json`, conversations, memory, skills and knowledge index load unchanged.

## Quick start

Requirements: **Python 3.11+** with [uv](https://docs.astral.sh/uv/), and **Node.js 18+** (needed only to build the UI once).

Double-click `start.bat`, or run:

```bash
cd frontend
npm install
npm run build
```

```bash
cd backend
uv run python -m cokcopilot
```

Then open **Settings** (Ctrl+,), enter your Base URL and API key, click **Test connection**, and pick a default model.

To use a native window instead of a browser tab, install the desktop extra once with `uv sync --extra desktop`.

## Team server and desktop app

CokCopilot can also run as a shared server for a group:

- `python -m cokcopilot serve` runs the backend for many users. Put it behind IIS for HTTPS.
- Everyone signs in with a personal **access key**, or with their **Microsoft work account** (Azure AD / Entra ID single sign-on, turned on and off by an admin), and gets their own chats, memory and **knowledge base**.
- Admins see a **usage dashboard**: replies and tokens per user, per day and per model, with CSV export.
- Users and groups can link a **shared folder** (e.g. a file share) as their knowledge base; everything in it and its subfolders is indexed and kept up to date.
- Admins manage the AI connection, MCP servers, the shared team documents, skills and users from the app.
- Users open the server in a browser, or run the **portable desktop app** (`desktop/`, one `.exe`, nothing to install), which connects to the server.

See **[docs/DEPLOY.md](docs/DEPLOY.md)** for IIS setup, user management and building the desktop app.

## Features

- **Copilot-style UI:** a sidebar with chats grouped by date and search, a greeting screen with suggestions, user messages in bubbles, and a rounded composer. Light, dark and system themes.
- **Streaming replies** with a Stop button (Esc). Markdown, tables, and syntax-highlighted code with a Copy button.
- **Attachments:** paste, drag-and-drop or pick images (sent to vision models) and documents (PDF, DOCX, TXT, MD, CSV, JSON, XML, HTML). Documents are read locally.
- **Edit and resend** any of your messages, **regenerate** the last reply, and **switch models** per chat from the composer.
- **Export** a chat as Markdown or JSON (chat menu → Export).

### MCP tools (new)

Connect [Model Context Protocol](https://modelcontextprotocol.io) servers to give the assistant tools such as files, web fetch, GitHub or databases.

- Open **MCP servers** in the sidebar, then **Add** a server or **Import JSON**. Import accepts the `mcpServers` format used by Claude Desktop, VS Code and Cursor:

  ```json
  {
    "mcpServers": {
      "filesystem": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/me/Documents"] },
      "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] },
      "remote": { "type": "http", "url": "https://example.com/mcp", "headers": { "Authorization": "Bearer …" } }
    }
  }
  ```

- Transports: local programs (**stdio**), **Streamable HTTP**, and **SSE**.
- Each server shows its status, tools, errors and log. You can enable or disable whole servers or single tools.
- **You approve each tool call.** The card shows the arguments, with **Allow once**, **Always allow this tool** (admins only on a team server) and **Deny**. Turn on "Run tools without asking" in Settings → Tools only if you trust every server.
- The composer's **Tools** menu shows how many tools are active and switches servers on or off.
- The config is saved to `mcp.json` in the data folder.
- Tools need a model that supports function calling. If a model rejects tools, the reply continues without them and a notice is shown.

### Knowledge base (RAG)

Any document in your knowledge folder (subfolders included) is indexed and re-indexed automatically when files change. With **Knowledge** on (the composer chip or Settings):

- Replies use only the matching excerpts from your documents and end with a **References** list.
- If nothing matches, the reply is "I don't know — your knowledge base doesn't contain information about this." and the model isn't called.
- MCP tools are not offered in this mode.

Embeddings come from your API's `/embeddings` endpoint when it's available. Otherwise a local keyword index is used, so this works offline too.

On a team server, the Knowledge panel has two tabs:

- **My documents:** files each user uploads for themselves. Only they can see or search them. Each file can be up to 25 MB, with 200 MB per user.
- **Team documents:** the shared folder. Admins manage it, and everyone can search it.

Answers search both, and the References show where each source came from, e.g. `My documents/plan.pdf` or `Team documents/handbook.docx`.

### Groups (team server)

Groups let people share chats and documents. Open **Groups** in the sidebar:

- **Create a group.** You become its owner. Invite other users by name. They see the invitation in their Groups panel and can **Join** or **Decline**.
- **Group chats:** pick the group in the switcher at the top of the sidebar, then start a chat. Everyone in the group can read every chat in it. Only the person who started a chat can continue, rename or edit it. The owner can also delete it.
- **Group documents:** the Knowledge panel has a tab for each of your groups. Any member can add or remove documents (25 MB per file, 200 MB per group). Or the owner links a **shared folder** instead (see [docs/DEPLOY.md](docs/DEPLOY.md#3c-shared-folders-as-knowledge-bases)).
- **Answers in a group chat** search the group's documents and the team documents. They never use anyone's own documents or memory, because the whole group can read the chat.
- The owner renames the group, removes members, or deletes the group, which deletes its chats and documents for everyone. Other members can leave at any time.

### Memory

After each reply, durable facts about you are saved and used in future chats: preferences, your role, the tools you use. Sensitive data is never saved. Manage them in **Memory**.

### Skills

Type `/` in the composer to pick a skill, for example `/summarize <text>`. A skill stays active for the rest of that chat. Create, edit or install skills (a `SKILL.md` file or a `.zip`) in **Skills**. They use the Agent Skills `SKILL.md` format:

```markdown
---
name: summarize
description: Summarize text as five short bullet points
---
Summarize the text the user gives you as at most five short bullet points...
```

## Keyboard shortcuts

| Keys | Action |
|---|---|
| Enter / Shift+Enter | Send / new line |
| Ctrl+N | New chat |
| Ctrl+K | Search chats |
| Ctrl+, | Settings |
| Esc | Stop generating |

## Data storage

- **Running from this repo:** `data/` in the repo.
- **Otherwise:** `%APPDATA%\CokCopilot\` on Windows, `~/Library/Application Support/CokCopilot/` on macOS, `~/.config/CokCopilot/` on Linux.
- Set `COKCOPILOT_DATA_DIR` to use any other folder.

The data folder holds:

- `config.json`, `mcp.json` and `skills/`.
- `knowledge_index.json` with `knowledge/`: the index of the knowledge folder.
- `logs/`: MCP server output.
- `conversations/` and `memory.json`, in personal mode.

On a team server it also holds:

- `users.json`: users and their hashed access keys.
- `secret.key`: signs sign-in cookies. Deleting it signs everyone out.
- `users/<id>/`: each user's `conversations/`, `memory.json`, `prefs.json` and own documents (`knowledge_files/`).
- `groups.json` with `groups/<id>/`: each group's members, `conversations/` and documents (`knowledge_files/`).

The desktop app keeps only its server address and sign-in, in `%APPDATA%\CokCopilot Desktop\`.

## Privacy & security

- **Your data stays with you.** Everything is stored in the data folder. The only outbound traffic goes to your model API and to the MCP servers you configure.
- **The model API key** lives in `config.json` and is never sent to the browser, not even to admins. Don't share that file.
- **Personal mode** answers only `127.0.0.1`/`localhost` (which also blocks DNS-rebinding attacks).
- **Team server:**
  - Every request needs a signed-in user.
  - Access keys are random 256-bit tokens, stored only as SHA-256 hashes.
  - Sessions are signed, HttpOnly, SameSite=Strict cookies, marked Secure on HTTPS.
  - Issuing a new key or blocking a user signs them out everywhere.
  - Users can only see their own chats, memory and documents. Changing the AI connection, MCP servers, team documents, skills and users needs an admin.
- **Cross-site protection:** requests from other websites are refused (checked with the `Origin` and `Sec-Fetch-Site` headers), so a malicious page can't use your session or your local app.
- **Uploads** are size-limited (25 MB per file; 200 MB of personal documents per user). Oversized "zip bomb" Word files and skill zips are rejected, and zip paths can't escape the skill folder.
- **MCP tools** run on the machine hosting the backend, with that account's permissions, for every user. Add only servers you trust. Each tool call shows its arguments and needs approval unless an admin allowed that tool.
- **The desktop app** stays on the configured server (other links open in your browser), asks no device permissions other than the clipboard and notifications (for group invitations), and warns before connecting without HTTPS.

## Development

See [BUILD.md](BUILD.md).

## License

MIT
