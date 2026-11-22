import { useCallback, useEffect, useState } from "react";
import { FileJson, Loader2, Pencil, Plug, Plus, RotateCw, Trash2, Wrench } from "lucide-react";
import type { McpServer, McpServerConfig } from "../api";
import { api } from "../api";
import { Modal, Switch, errorText, useToast } from "../components/ui";

interface Form {
  name: string;
  type: McpServerConfig["type"];
  command: string;
  args: string;
  env: string;
  cwd: string;
  url: string;
  headers: string;
  original?: string;
  base?: McpServerConfig;
}

const EMPTY: Form = { name: "", type: "stdio", command: "", args: "", env: "", cwd: "", url: "", headers: "" };

const EXAMPLE = `{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/me/Documents"]
    },
    "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] }
  }
}`;

function toPairs(obj: Record<string, string> | undefined, sep: string) {
  return Object.entries(obj ?? {}).map(([k, v]) => `${k}${sep}${v}`).join("\n");
}
function fromPairs(text: string, sep: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const i = line.indexOf(sep);
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + sep.length).trim();
  }
  return out;
}

function toForm(s: McpServer): Form {
  const c = s.config;
  return {
    name: s.name, type: c.type, command: c.command ?? "", args: (c.args ?? []).join("\n"), env: toPairs(c.env, "="),
    cwd: c.cwd ?? "", url: c.url ?? "", headers: toPairs(c.headers, ": "), original: s.name, base: c,
  };
}

function fromForm(f: Form): McpServerConfig {
  const base: McpServerConfig = { ...(f.base ?? { type: f.type }) };
  delete base.command; delete base.args; delete base.env; delete base.cwd; delete base.url; delete base.headers;
  const c: McpServerConfig = { ...base, type: f.type };
  if (f.type === "stdio") {
    c.command = f.command.trim();
    c.args = f.args.split("\n").map((a) => a.trim()).filter(Boolean);
    const env = fromPairs(f.env, "=");
    if (Object.keys(env).length) c.env = env;
    if (f.cwd.trim()) c.cwd = f.cwd.trim();
  } else {
    c.url = f.url.trim();
    const h = fromPairs(f.headers, ":");
    if (Object.keys(h).length) c.headers = h;
  }
  return c;
}

const STATUS_LABEL: Record<McpServer["status"], string> = {
  connected: "Connected", connecting: "Connecting…", disabled: "Disabled", error: "Error",
};

export function McpPanel(props: { isAdmin: boolean; onClose: () => void; onChanged: (servers: McpServer[]) => void }) {
  const isAdmin = props.isAdmin;
  const [servers, setServers] = useState<McpServer[]>([]);
  const [configPath, setConfigPath] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [importText, setImportText] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const toast = useToast();
  const { onChanged } = props;

  const load = useCallback(async () => {
    try {
      const r = await api.mcp();
      setServers(r.servers);
      setConfigPath(r.config_path);
      onChanged(r.servers);
      setSel((cur) => cur ?? r.servers[0]?.name ?? null);
    } catch (e) {
      toast(errorText(e), true);
    }
  }, [onChanged, toast]);

  useEffect(() => {
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [load]);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setErr("");
    try {
      await fn();
      await load();
      return true;
    } catch (e) {
      setErr(errorText(e));
      return false;
    } finally {
      setBusy("");
    }
  };

  const current = servers.find((s) => s.name === sel);

  const saveForm = async () => {
    if (!form) return;
    const ok = await run("save", async () => {
      const r = await api.saveMcpServer(form.name.trim(), fromForm(form), form.original);
      setSel(r.name);
    });
    if (ok) setForm(null);
  };

  const doImport = async () => {
    if (importText == null) return;
    const ok = await run("import", async () => {
      const r = await api.importMcp(importText);
      toast(`Added ${r.added.join(", ")}`);
      setSel(r.added[0] ?? null);
    });
    if (ok) setImportText(null);
  };

  let right: JSX.Element;
  if (importText != null) {
    right = (
      <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>Import from JSON</h3>
        <span className="hint">Paste an <code>mcpServers</code> config from Claude Desktop, VS Code, Cursor or a server's README.</span>
        <textarea className="textarea mono" style={{ minHeight: 260 }} value={importText} placeholder={EXAMPLE}
          onChange={(e) => setImportText(e.target.value)} />
        {err && <div className="error-text">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="btn" onClick={() => { setImportText(null); setErr(""); }}>Cancel</button>
          <button className="btn primary" onClick={doImport} disabled={!importText.trim() || !!busy}>
            {busy === "import" && <Loader2 size={15} className="spin" />} Import & connect
          </button>
        </div>
      </div>
    );
  } else if (form) {
    right = (
      <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>{form.original ? `Edit ${form.original}` : "Add MCP server"}</h3>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="mcp-name">Name</label>
            <input id="mcp-name" className="input" value={form.name} placeholder="filesystem"
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="field">
            <span className="label">Transport</span>
            <div className="tabs">
              {(["stdio", "http", "sse"] as const).map((t) => (
                <button key={t} className={`tab${form.type === t ? " on" : ""}`} onClick={() => setForm({ ...form, type: t })}>
                  {t === "stdio" ? "Local (stdio)" : t === "http" ? "HTTP" : "SSE"}
                </button>
              ))}
            </div>
          </div>
        </div>
        {form.type === "stdio" ? (
          <>
            <div className="field">
              <label htmlFor="mcp-cmd">Command</label>
              <input id="mcp-cmd" className="input mono" value={form.command} placeholder="npx  /  uvx  /  python  /  C:\tools\server.exe"
                onChange={(e) => setForm({ ...form, command: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="mcp-args">Arguments <span className="hint">(one per line)</span></label>
              <textarea id="mcp-args" className="textarea mono" value={form.args}
                placeholder={"-y\n@modelcontextprotocol/server-filesystem\nC:/Users/me/Documents"}
                onChange={(e) => setForm({ ...form, args: e.target.value })} />
            </div>
            <div className="grid-2">
              <div className="field">
                <label htmlFor="mcp-env">Environment <span className="hint">(KEY=value per line)</span></label>
                <textarea id="mcp-env" className="textarea mono" style={{ minHeight: 70 }} value={form.env}
                  onChange={(e) => setForm({ ...form, env: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="mcp-cwd">Working folder <span className="hint">(optional)</span></label>
                <input id="mcp-cwd" className="input mono" value={form.cwd} onChange={(e) => setForm({ ...form, cwd: e.target.value })} />
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="field">
              <label htmlFor="mcp-url">Server URL</label>
              <input id="mcp-url" className="input mono" value={form.url} placeholder="https://example.com/mcp"
                onChange={(e) => setForm({ ...form, url: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="mcp-headers">Headers <span className="hint">(Name: value per line)</span></label>
              <textarea id="mcp-headers" className="textarea mono" style={{ minHeight: 70 }} value={form.headers}
                placeholder="Authorization: Bearer …" onChange={(e) => setForm({ ...form, headers: e.target.value })} />
            </div>
          </>
        )}
        <span className="hint">Local servers run as programs on this computer with your permissions — only add servers you trust.</span>
        {err && <div className="error-text">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="btn" onClick={() => { setForm(null); setErr(""); }}>Cancel</button>
          <button className="btn primary" onClick={saveForm} disabled={!form.name.trim() || !!busy}>
            {busy === "save" && <Loader2 size={15} className="spin" />} Save & connect
          </button>
        </div>
      </div>
    );
  } else if (current) {
    const c = current.config;
    right = (
      <div style={{ display: "grid", gap: 14, alignContent: "start", minWidth: 0 }}>
        <div className="row">
          <span className={`dot ${current.status}`} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 16 }}>{current.name}</div>
            {isAdmin && (
              <div className="hint mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {c.type === "stdio" ? [c.command, ...(c.args ?? [])].join(" ") : c.url}
              </div>
            )}
          </div>
          {isAdmin && (
            <Switch checked={!c.disabled} label="Enabled"
              onChange={(v) => run("toggle", () => api.setMcpEnabled(current.name, v))} />
          )}
        </div>
        <div className="row wrap">
          <span className={`badge ${current.status === "connected" ? "ok" : current.status === "error" ? "err" : current.status === "connecting" ? "warn" : ""}`}>
            {STATUS_LABEL[current.status]}
          </span>
          {current.server_label && <span className="badge">{current.server_label}</span>}
          <span className="badge">{c.type}</span>
          <span className="spacer" />
          {isAdmin && <>
          <button className="btn sm" onClick={() => run("restart", () => api.restartMcpServer(current.name))} disabled={!!busy}>
            {busy === "restart" ? <Loader2 size={14} className="spin" /> : <RotateCw size={14} />} Reconnect
          </button>
          <button className="btn sm" onClick={() => setForm(toForm(current))}><Pencil size={14} /> Edit</button>
          <button className="btn sm danger" onClick={() => confirm(`Remove the ${current.name} server?`) &&
            run("delete", async () => { await api.deleteMcpServer(current.name); setSel(null); })}>
            <Trash2 size={14} /> Remove
          </button>
          </>}
        </div>
        {current.error && <div className="error-banner">{current.error}</div>}
        {err && <div className="error-text">{err}</div>}
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Tools ({current.tools.length})</div>
          <div className="list">
            {current.tools.length === 0 && (
              <div className="list-empty">{current.status === "connected" ? "This server has no tools." : "Connect the server to see its tools."}</div>
            )}
            {current.tools.map((t) => (
              <div key={t.name} className="list-item" style={{ alignItems: "flex-start" }}>
                <Wrench size={16} color="var(--muted)" style={{ marginTop: 3 }} />
                <div className="grow">
                  <div className="name mono" style={{ fontSize: 13 }}>{t.name}{t.read_only && <span className="badge" style={{ marginLeft: 8 }}>read-only</span>}</div>
                  <div className="sub" style={{ whiteSpace: "normal" }}>{t.description || t.title || "No description"}</div>
                  {isAdmin && <label className="hint row" style={{ gap: 6, marginTop: 4, cursor: "pointer", width: "fit-content" }}>
                    <input type="checkbox" checked={t.auto_approve}
                      onChange={(e) => run("tool", () => api.setMcpTool(current.name, t.name, { auto_approve: e.target.checked }))} />
                    Run without asking
                  </label>}
                </div>
                {isAdmin ? (
                  <Switch checked={t.enabled} label={`Enable ${t.name}`}
                    onChange={(v) => run("tool", () => api.setMcpTool(current.name, t.name, { enabled: v }))} />
                ) : <span className={`badge${t.enabled ? " ok" : ""}`}>{t.enabled ? "on" : "off"}</span>}
              </div>
            ))}
          </div>
        </div>
        {current.log_tail && (
          <details>
            <summary className="hint" style={{ cursor: "pointer" }}>Server log</summary>
            <pre className="log">{current.log_tail}</pre>
          </details>
        )}
      </div>
    );
  } else {
    right = (
      <div className="list-empty" style={{ alignSelf: "center" }}>
        <Plug size={28} style={{ marginBottom: 8 }} /><br />
        {isAdmin ? "Connect MCP servers to give the assistant tools — files, web fetch, databases, GitHub and more."
          : "No tool servers are set up yet. Ask your admin."}
        <div className="row" style={{ justifyContent: "center", marginTop: 14, ...(isAdmin ? {} : { display: "none" }) }}>
          <button className="btn primary" onClick={() => setForm({ ...EMPTY })}><Plus size={15} /> Add server</button>
          <button className="btn" onClick={() => setImportText("")}><FileJson size={15} /> Import JSON</button>
        </div>
      </div>
    );
  }

  return (
    <Modal title="MCP servers"
      subtitle={isAdmin && configPath ? <>Model Context Protocol tools · config in <code>{configPath}</code></>
        : "Tools the assistant can use. Your admin manages them; you approve each call."}
      icon={<Plug size={20} />} onClose={props.onClose} wide>
      <div className="mcp-layout">
        <div className="mcp-servers">
          <div className="row" style={{ marginBottom: 6, ...(isAdmin ? {} : { display: "none" }) }}>
            <button className="btn sm primary" onClick={() => { setForm({ ...EMPTY }); setImportText(null); setErr(""); }}>
              <Plus size={14} /> Add
            </button>
            <button className="btn sm" onClick={() => { setImportText(""); setForm(null); setErr(""); }}>
              <FileJson size={14} /> Import JSON
            </button>
          </div>
          {servers.map((s) => (
            <button key={s.name} className={`server-item${s.name === sel && !form && importText == null ? " sel" : ""}`}
              onClick={() => { setSel(s.name); setForm(null); setImportText(null); setErr(""); }}>
              <span className={`dot ${s.status}`} />
              <span className="grow">
                <div className="name">{s.name}</div>
                <div className="sub">{s.status === "connected" ? `${s.tools.length} tools` : STATUS_LABEL[s.status]}</div>
              </span>
            </button>
          ))}
        </div>
        {right}
      </div>
    </Modal>
  );
}
