# CokCopilot — Domain Model

## Glossary

| Term | Definition |
|------|-----------|
| **Conversation** | A single chat session with its own message history, system prompt, model selection, and title. Persisted locally. |
| **Message** | A single turn in a conversation — either from the user or the AI. Contains text, optional attached files, and metadata. |
| **Provider** | An OpenAI-compatible API endpoint defined by a base URL and API key. |
| **Model** | A specific AI model available at a provider, identified by model ID (e.g. `gpt-4o`, `llama3`). |
| **System Prompt** | Instructions that define the AI's behavior for a conversation. Has a global default (Settings) and per-conversation override. |
| **Streaming** | Real-time token-by-token delivery of AI responses via Server-Sent Events (SSE). |
| **Inline Image** | An image sent to the AI as base64-encoded data within the message, used for vision-capable models. |
| **Knowledge Folder** | A user-chosen folder (Settings / Knowledge dialog) whose documents, including subfolders, form the Knowledge Base. It is the source of truth: changes made by the app or any other tool are detected and re-indexed automatically. |
| **Extracted Text** | Text content pulled from documents (PDF, DOCX, etc.) via local parsing libraries, injected into prompts as context. |

## Key Concepts

- **Single Provider Model**: The app communicates with one OpenAI-compatible endpoint at a time. Users configure this in Settings.
- **Conversation Isolation**: Each conversation stores its own system prompt, model selection, and message history independently.
- **File Handling Split**: Images → base64 inline (provider handles). Documents → local text extraction → injected as prompt context.
- **Local-First**: All data (conversations, settings, API keys) stored locally. No cloud sync, no telemetry.

## Team server terms

| Term | Definition |
|------|-----------|
| **User** | Someone an admin added on a team server. Signs in with an access key or, when SSO is on, their Microsoft work account matched by **email**. The app, not Azure, decides who has access. |
| **SSO** | Single sign-on with Microsoft Entra ID (Azure AD), turned on and off by an admin. While on, access keys work only for admins unless the admin allows them for everyone. |
| **Usage** | One record per reply (and per background memory update): user, model and token counts. Shown to admins in the Usage dashboard. |
| **Shared folder root** | A folder an admin allows (e.g. `\fileserver\share`). Users and group owners may link any folder inside it as a **Linked folder**. |
| **Linked folder** | A shared folder used as a user's or group's knowledge base instead of uploaded documents. The folder is the source of truth; it and all subfolders are indexed and rescanned automatically. |
