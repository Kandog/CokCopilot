# CokCopilot — First-time Setup

1. Install Python 3.11+ with [uv](https://docs.astral.sh/uv/), and Node.js 18+.
2. Double-click `start.bat`. The first run builds the UI and installs the Python packages.
3. In the app, open **Settings** (Ctrl+,), enter the Base URL and API key, click **Test connection**, and pick a default model.
4. Optional: add tools under **MCP servers** and documents under **Knowledge base**.

## Common API providers

| Provider | Base URL |
|---|---|
| OpenAI | `https://api.openai.com/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Azure OpenAI | `https://{resource}.openai.azure.com/openai/v1` |
| Ollama (local) | `http://localhost:11434/v1` |
| LM Studio (local) | `http://localhost:1234/v1` |

For local servers, leave the API key empty.

## Troubleshooting

- **"Frontend not built"**: run `npm install && npm run build` in `frontend/`.
- **API connection fails**: check the Base URL (it usually ends in `/v1`) and the key. Test connection shows the provider's error.
- **An MCP server shows "error"**: open it in the MCP panel to see the message and the server log. `npx` and `uvx` servers need Node.js or uv on your PATH. The first run can take a while because the package downloads.
- **Tools are never used**: the model must support function calling, and Knowledge-only mode must be off.

See [README.md](README.md) for features and [BUILD.md](BUILD.md) for development.
