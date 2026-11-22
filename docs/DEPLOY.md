# Deploying CokCopilot for a team

```
 users ──HTTPS──▶ IIS (your certificate) ──HTTP, local only──▶ python -m cokcopilot serve
   │                                                              │
   ├─ browser: https://chat.example.com                           ├─ users.json  (access keys, hashed)
   └─ CokCopilot-portable.exe (no install)                        ├─ users/<id>/ (each user's chats, memory, prefs, own documents)
                                                                  └─ config.json, mcp.json, skills/, team documents  (shared, admin-managed)
```

- **The server** is the Python backend in *server mode*. Every request needs a signed-in user, and it speaks plain HTTP on `127.0.0.1`. IIS handles HTTPS and the public address.
- **Users** sign in with a personal **access key**, or with their **Microsoft work account** once an admin turns on single sign-on (section 3a). Each user has their own chats, memory, preferences (theme, default model, knowledge-only mode, tools on/off) and **knowledge base**.
- **Personal documents** are uploaded in the app (Knowledge base → *My documents*). They are stored in `users/<id>/knowledge_files/`, and only that user can see or search them. Limits: 25 MB per file, 200 MB per user.
- **Admins** also manage the shared setup: the AI connection (the API key stays on the server and is never sent to browsers), MCP servers, the **team documents** (everyone can search them), skills and users.
- **The desktop app** is a portable `.exe` with nothing to install. On first start it asks for the server address and then shows that server's UI.

> The backend is FastAPI (ASGI), not Flask (WSGI). Don't host it with `wfastcgi`, which can't stream replies. Use one of the two IIS options below.

## 1. Prepare the server (Windows)

1. Install Python 3.11+ and [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`).
2. Copy the project to the server, e.g. `C:\CokCopilot`, with the UI already built (`frontend\dist`). Build it on any machine with `cd frontend && npm install && npm run build`.
3. Install the backend packages:

   ```bat
   cd C:\CokCopilot\backend
   uv sync --no-dev
   ```

4. Create the first admin. This prints the admin's access key once:

   ```bat
   .venv\Scripts\python -m cokcopilot users --data-dir C:\CokCopilotData add "Your Name" --admin
   ```

   If you skip this, the first `serve` start creates an `admin` user and prints its key to the console or log.
5. Give the IIS app pool identity (e.g. `IIS AppPool\CokCopilot`) **Modify** rights on `C:\CokCopilotData`.

## 2a. IIS with HttpPlatformHandler (recommended)

IIS starts the Python server, gives it a port, and restarts it if it stops.

1. Install the **HttpPlatformHandler** module for IIS (from Microsoft's IIS downloads).
2. Copy [`deploy/iis/web.config`](../deploy/iis/web.config) into the site folder, for example `C:\CokCopilot\site`. Then edit:
   - `processPath`: the path to `backend\.venv\Scripts\python.exe`.
   - `--data-dir`: your data folder.
   - `--allowed-origin`: the public address users open, e.g. `https://chat.example.com`.
   - `COKCOPILOT_FRONTEND_DIST`: the path to `frontend\dist`.
3. In IIS Manager, create a site for that folder with an **HTTPS binding and your certificate**. Use an app pool with **No Managed Code**.
4. **Keep one worker process**, the default: leave "Maximum Worker Processes" at 1. Live replies and tool approvals are held in that process.
5. Browse to `https://chat.example.com` and sign in with the admin key.

The `web.config` also sets a long `requestTimeout` and turns off dynamic compression. Both are needed for streamed replies.

## 2b. IIS as a reverse proxy (URL Rewrite + ARR)

Use this if you'd rather run the Python server yourself, for example as a Windows service with NSSM:

```bat
C:\CokCopilot\backend\.venv\Scripts\python.exe -m cokcopilot serve --port 8765 --data-dir C:\CokCopilotData --allowed-origin https://chat.example.com
```

In IIS:

1. Install **URL Rewrite** and **Application Request Routing**, and enable the proxy (ARR → Server Proxy Settings → *Enable proxy*).
2. In the same dialog, set **Response buffer threshold** to `0`, so replies stream instead of arriving all at once.
3. Add a reverse-proxy rule on the HTTPS site that forwards everything to `http://127.0.0.1:8765/`.

`--allowed-origin` matters here: ARR changes the `Host` header, and the server uses the origin to block cross-site requests. Alternatively, keep the original host with:

```bat
%windir%\system32\inetsrv\appcmd.exe set config -section:system.webServer/proxy -preserveHostHeader:true /commit:apphost
```

## 3. Users and access keys

**In the app:** sidebar → **Users** (admins only). You can:

- add a user (the key is shown once — send it privately);
- change someone's role;
- block or unblock sign-in;
- issue a new key (the old one stops working and the user is signed out);
- remove a user.

**On the server:**

```bat
python -m cokcopilot users --data-dir C:\CokCopilotData list
```

```bat
python -m cokcopilot users --data-dir C:\CokCopilotData add "Alice"
```

```bat
python -m cokcopilot users --data-dir C:\CokCopilotData reset-key "Alice"
```

The command also has `disable`, `enable` and `remove`. Changes made here take effect immediately, without a restart.

How it works:

- Keys look like `cok_…`. Only their SHA-256 hash is stored, in `users.json`.
- The web UI signs in once and then uses a 30-day HttpOnly session cookie, signed with `secret.key` in the data folder. Deleting `secret.key` signs everyone out.
- Scripts can call the API with `Authorization: Bearer cok_…`.

## 3a. Sign in with Microsoft (Azure AD / Entra ID single sign-on)

Users can sign in with their company Microsoft account instead of an access key. **Who may use the app is still decided in the app:** a Microsoft account only gets in if an admin has added a user with that work email. Removing or blocking the user in the Users panel stops their access, whatever Azure says.

**1. Register the app in Azure** (Azure portal → Microsoft Entra ID → App registrations → New registration):

- Name: `CokCopilot`. Supported account types: **Accounts in this organizational directory only**.
- Redirect URI: platform **Web**, value `https://chat.example.com/api/auth/sso/callback` (your public address; the Users panel shows the exact value to copy).
- After creating it, note the **Application (client) ID** and **Directory (tenant) ID** on the Overview page.
- **Certificates & secrets → New client secret.** Copy the secret's *Value* (not its ID). Note its expiry date: sign-in stops working when it expires, so renew it before then and paste the new value into the app.
- The default permissions (`openid`, `profile`, `email` / `User.Read`) are enough. No admin consent is needed for these in most tenants.
- Optional: to allow only some people, set *Enterprise applications → CokCopilot → Properties → Assignment required* to **Yes** and assign users or groups there.

**2. Turn it on in CokCopilot** (sidebar → **Users** → *Sign in with Microsoft*):

1. Paste the tenant ID, client ID and client secret. Click **Check tenant** to confirm the server can reach Microsoft.
2. Give each user their **work email** (the address they sign in to Microsoft with, usually their UPN). Existing users: click *add email* under their name. On the command line: `python -m cokcopilot users set-email "Alice" alice@company.com`.
3. Switch **Sign in with Microsoft** on.

How it works:

- The sign-in page shows **Sign in with Microsoft**. The first time someone signs in, their Microsoft account is linked to their app user (by its permanent object ID), so later email changes in Azure don't matter. Changing the email in the app unlinks it.
- **Access keys while SSO is on:** by default only **admins** can still use their key (a small "Admin? Sign in with an access key" link), so a wrong Azure setting or an expired secret can't lock everyone out. Turn on *Users may also sign in with access keys* to keep keys working for everyone (e.g. for scripts using `Authorization: Bearer`).
- Turning SSO on signs out users who were signed in with a key (unless keys are allowed). Turning it off brings back access-key sign-in for everyone.
- Only accounts from your own tenant are accepted (`common` / multi-tenant isn't supported). The client secret is stored in `sso.json` in the data folder and never sent to browsers: protect that folder like `users.json`.
- **Desktop app:** a build from this version or later shows the Microsoft sign-in inside the app. Older builds open it in the browser, which can't pass the sign-in back, so update the desktop app before switching SSO on. If your company federates sign-in to its own server (ADFS, Okta…), add that host to `cokcopilot.json`: `{ "serverUrl": "https://chat.example.com", "authHosts": ["adfs.example.com"] }`.
- If IIS changes the host name (reverse proxy without `preserveHostHeader`), pass `--allowed-origin https://chat.example.com` so the server builds the right redirect URI, or set the Redirect URI field explicitly.

## 3b. Usage dashboard

Admins see who uses the assistant and how much: sidebar → **Usage**.

- Replies, input and output tokens per user, per day (chart) and per model, for the last 7, 30 or 90 days. Click a user to see only their numbers. **Export CSV** downloads the per-user table.
- Token counts come from the AI provider. If it doesn't report them, the app estimates (about 4 characters per token) and marks those numbers with `*`.
- Counted: every reply in personal and group chats (all tool rounds together) and the background memory updates. Not counted: document indexing (embeddings).
- Stored as one line per reply in `usage/<YYYY-MM>.jsonl` in the data folder: user, time, model and token counts only, never message text. Delete old month files to clear history.

## 3c. Shared folders as knowledge bases

Instead of uploading documents, a user (for **My documents**) or a group's owner (for the group) can link a **shared folder**. Every supported document in it and in **all its subfolders** is indexed, and changes are picked up automatically: the server watches the folder and rescans it about once a minute (network shares don't always report changes).

**Admin setup:** Settings → Knowledge → **Shared folder roots**, one per line, e.g. `\fileserver\departments`. Users can link any folder inside these roots; nothing outside them, and never CokCopilot's own data folder. With no roots, linking is off.

- Users pick the folder in Knowledge base → **Use a shared folder** (the server lists the folders, since the browser can't see the server's disks), or type the path. **Unlink** goes back to their uploaded documents, which are kept.
- While linked, files are added, changed and removed **in the folder** (Explorer, SharePoint sync, any tool). The app doesn't upload into or delete from a shared folder.
- Limits: the first 5,000 documents per linked folder; symbolic links and junctions are skipped, so a link can't lead outside the folder. If the share is briefly unreachable, the existing index is kept.
- The **IIS app pool identity** (or the service account) reads the folders, so it needs read access to the shares. On a domain, run the app pool as a domain account (or grant the server's computer account `DOMAIN\SERVER$` read access).

> **Permissions:** the app reads with its own account, not the user's. Anyone who can link a folder under a root can search everything under it that the service account can read, even files their own Windows account can't open. Only list roots whose contents every CokCopilot user may see.

## 4. The desktop app for users

Build it on a Windows machine with Node.js:

```bash
cd desktop && npm install && npm run dist
```

This produces two files in `desktop/release/`:

- `CokCopilot-<version>-portable.exe`: one file. Double-click to run, nothing to install.
- `CokCopilot-<version>-win.zip`: the same app as a folder. It starts faster than the single file, which unpacks itself on each launch.

**Preset the server for your group** by putting a `cokcopilot.json` next to the exe (or inside the unzipped folder):

```json
{ "serverUrl": "https://chat.example.com" }
```

Users then go straight to the sign-in screen. Without the file, the app asks for the address on first start. **File → Change server…** switches servers later.

The desktop app keeps its settings and sign-in in `%APPDATA%\CokCopilot Desktop`. Links in answers open in the user's normal browser.

The app shows the server's own UI, so new server features, such as **Groups**, appear in it as soon as the server is updated, without a new desktop build. A desktop build from this version or later also shows a Windows notification when someone invites the user to a group while the app is in the background. Older builds show only the in-app message.

The build isn't code-signed, so Windows SmartScreen may warn on first run. Sign it with your organisation's certificate (`CSC_LINK` / `CSC_KEY_PASSWORD`; see the electron-builder docs) to avoid that. macOS and Linux builds (`npm run dist:mac`, `npm run dist:linux`) must be made on those systems.

## 5. Security checklist

- [ ] Users reach the server **only through HTTPS** (IIS). `serve` listens on `127.0.0.1` by default. If you pass `--host 0.0.0.0`, it warns, because access keys would cross the network unencrypted.
- [ ] Back up the data folder (`users.json`, `users/`, `config.json`, `mcp.json`, `skills/`, `sso.json`, `usage/`).
- [ ] With SSO on: renew the Azure client secret before it expires, and keep at least one admin access key somewhere safe.
- [ ] Shared folder roots contain only documents every user may read (the service account reads them for everyone).
- [ ] MCP servers run on the server with the app pool's permissions, for every user. Add only tools you're happy for all users to use. Leave "Run tools without asking" off, so each user approves each call.
- [ ] Removing a user keeps their chats in `users/<id>/`. Delete that folder if you need to.
