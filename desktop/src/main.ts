/**
 * CokCopilot desktop app.
 *
 * A small, portable shell around the CokCopilot web UI. On first start it asks
 * for the server address (e.g. https://chat.example.com) and then shows that
 * server's UI, so users always get the version their server runs. Sign-in
 * uses the server's normal login (access key, or "Sign in with Microsoft" when
 * the server has single sign-on on); the session cookie is kept in the app's own storage.
 *
 * The server address can be preset for a whole group by putting a
 * `cokcopilot.json` file next to the .exe:  {"serverUrl": "https://chat.example.com"}
 */
import { app, BrowserWindow, Menu, dialog, ipcMain, session, shell } from "electron";
import * as fs from "fs";
import * as path from "path";

const PARTITION = "persist:cokcopilot";
const CONFIG_NAME = "cokcopilot.json";

interface Config {
  serverUrl?: string;
  /** Extra sign-in hosts the window may open, e.g. a company ADFS server (`adfs.example.com`). */
  authHosts?: string[];
}

// Microsoft Entra ID (Azure AD) sign-in pages. Shown inside the app during "Sign in with Microsoft",
// so the session cookie lands in the app and not in the user's browser.
const MICROSOFT_AUTH_HOSTS = [
  "login.microsoftonline.com",
  "login.microsoft.com",
  "login.live.com",
  "autologon.microsoftazuread-sso.com",
  "device.login.microsoftonline.com",
];

function isAuthHost(url: string): boolean {
  try {
    const u = new URL(url);
    if (u.protocol !== "https:") return false;
    const extra = (loadConfig().authHosts ?? []).map((h) => h.toLowerCase());
    return [...MICROSOFT_AUTH_HOSTS, ...extra].includes(u.hostname.toLowerCase());
  } catch {
    return false;
  }
}

let win: BrowserWindow | null = null;

// -- config -------------------------------------------------------------------------

function exeDir(): string {
  // Portable builds unpack to a temp folder; this env var points at the real .exe location.
  return process.env.PORTABLE_EXECUTABLE_DIR || path.dirname(app.getPath("exe"));
}

function readJson(file: string): Config {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8")) as Config;
  } catch {
    return {};
  }
}

function userConfigPath() {
  return path.join(app.getPath("userData"), "config.json");
}

function loadConfig(): Config {
  const preset = readJson(path.join(exeDir(), CONFIG_NAME));
  const mine = readJson(userConfigPath());
  return { ...preset, ...mine };
}

function saveConfig(cfg: Config) {
  fs.mkdirSync(path.dirname(userConfigPath()), { recursive: true });
  fs.writeFileSync(userConfigPath(), JSON.stringify(cfg, null, 2));
}

function normalizeUrl(raw: string): string {
  let s = raw.trim();
  if (!/^https?:\/\//i.test(s)) s = `https://${s}`;
  const u = new URL(s);
  if (u.protocol !== "https:" && u.protocol !== "http:") throw new Error("Use an http(s) address.");
  return u.origin + u.pathname.replace(/\/+$/, "");
}

function serverOrigin(): string | null {
  const url = loadConfig().serverUrl;
  try {
    return url ? new URL(url).origin : null;
  } catch {
    return null;
  }
}

// -- windows ------------------------------------------------------------------------

function showConnect(message = "") {
  if (!win) return;
  win.loadFile(path.join(__dirname, "..", "static", "connect.html"), { query: message ? { message } : {} });
}

function showServer() {
  const url = loadConfig().serverUrl;
  if (!win) return;
  if (!url) return showConnect();
  win.loadURL(`${url}/`);
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 720,
    minHeight: 520,
    title: "CokCopilot",
    backgroundColor: "#f8f6f2",
    autoHideMenuBar: true,
    webPreferences: {
      partition: PARTITION,
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // Links to other sites open in the user's browser, never inside the app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  // Stay on the configured server: other sites (including redirects) open in the user's browser.
  const keepOnServer = (event: Electron.Event, url: string) => {
    if (url.startsWith("file:") || isAuthHost(url)) return;
    const origin = serverOrigin();
    let target = "";
    try {
      target = new URL(url).origin;
    } catch {
      /* invalid URL: block */
    }
    if (!origin || target !== origin) {
      event.preventDefault();
      if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    }
  };
  win.webContents.on("will-navigate", keepOnServer);
  win.webContents.on("will-redirect", keepOnServer);
  win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
    if (!isMainFrame || code === -3 /* aborted */ || url.startsWith("file:")) return;
    showConnect(`Couldn't open ${url} (${desc}). Check the address and your network, then try again.`);
  });

  showServer();
}

function buildMenu() {
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: "File",
      submenu: [
        { label: "Change server…", click: () => showConnect() },
        { label: "Reload", accelerator: "CmdOrCtrl+R", click: () => win?.webContents.reload() },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        { role: "zoomIn" }, { role: "zoomOut" }, { role: "resetZoom" }, { type: "separator" },
        { role: "togglefullscreen" }, { role: "toggleDevTools" },
      ],
    },
    {
      label: "Help",
      submenu: [{
        label: "About CokCopilot",
        click: () => dialog.showMessageBox({
          type: "info",
          title: "About CokCopilot",
          message: `CokCopilot ${app.getVersion()}`,
          detail: `Server: ${loadConfig().serverUrl ?? "not set"}`,
        }),
      }],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// -- IPC for the connect page -------------------------------------------------------

ipcMain.handle("cok:get-server", () => loadConfig().serverUrl ?? "");

ipcMain.handle("cok:connect", async (event, raw: string) => {
  if (!event.senderFrame?.url.startsWith("file:")) throw new Error("not allowed");
  let url: string;
  try {
    url = normalizeUrl(raw);
  } catch {
    return { ok: false, error: "That doesn't look like a web address." };
  }
  const parsed = new URL(url);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname);
  if (parsed.protocol === "http:" && !local) {
    const { response } = await dialog.showMessageBox({
      type: "warning",
      buttons: ["Cancel", "Connect anyway"],
      defaultId: 0,
      cancelId: 0,
      title: "Not a secure connection",
      message: "This server address doesn't use HTTPS.",
      detail: "Your access key and chats would travel over the network unencrypted. Ask your admin for the https:// address.",
    });
    if (response !== 1) return { ok: false, error: "Use the server's https:// address." };
  }
  try {
    const res = await session.fromPartition(PARTITION).fetch(`${url}/api/health`);
    const data = (await res.json()) as { app?: string; mode?: string };
    if (data.app !== "cokcopilot") return { ok: false, error: "That address answers, but it isn't a CokCopilot server." };
  } catch (e) {
    return { ok: false, error: `Couldn't reach ${url}: ${e instanceof Error ? e.message : String(e)}` };
  }
  saveConfig({ ...readJson(userConfigPath()), serverUrl: url });
  setTimeout(showServer, 50);
  return { ok: true };
});

// -- app lifecycle --------------------------------------------------------------------

// Keep the desktop app's own data apart from the Python app's %APPDATA%\CokCopilot (config.json etc.).
app.setPath("userData", path.join(app.getPath("appData"), "CokCopilot Desktop"));

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(() => {
    // The web UI needs clipboard writes (Copy buttons) and notifications (group invitations);
    // nothing else (camera, location…). Only pages from the configured server get them.
    const ses = session.fromPartition(PARTITION);
    const allowed = new Set(["clipboard-sanitized-write", "notifications"]);
    const fromServer = (url: string | undefined) => {
      try {
        return !!url && new URL(url).origin === serverOrigin();
      } catch {
        return false;
      }
    };
    ses.setPermissionRequestHandler((wc, permission, cb) => cb(allowed.has(permission) && fromServer(wc?.getURL())));
    ses.setPermissionCheckHandler((_wc, permission, origin) => allowed.has(permission) && fromServer(origin));
    // Windows shows notifications only for apps with an AppUserModelID.
    if (process.platform === "win32") app.setAppUserModelId("com.cokcopilot.desktop");
    buildMenu();
    createWindow();
  });

  app.on("window-all-closed", () => app.quit());
}
