import { useState } from "react";
import { CheckCircle2, Eye, EyeOff, FolderOpen, Loader2, Settings as SettingsIcon } from "lucide-react";
import type { Me, Settings } from "../api";
import { api } from "../api";
import { Modal, ToggleRow, errorText, useToast } from "../components/ui";

type Tab = "connection" | "chat" | "knowledge" | "tools" | "appearance" | "general";

const PREF_FIELDS = ["theme", "default_model", "rag_enabled", "memory_enabled", "mcp_enabled", "disable_thinking"] as const;

export function SettingsPanel(props: { settings: Settings; me: Me; onClose: () => void; onSaved: (s: Settings) => void }) {
  const isAdmin = props.me.is_admin;
  const isLocal = props.me.mode === "local";
  const tabs: Tab[] = isAdmin ? ["connection", "chat", "knowledge", "tools", "appearance"] : ["general", "appearance"];
  const [s, setS] = useState<Settings>(props.settings);
  const [tab, setTab] = useState<Tab>(tabs[0]);
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; text: string; models?: string[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS((cur) => ({ ...cur, [k]: v }));

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      const { models } = await api.models({ base_url: s.base_url, api_key: s.api_key });
      setTest({ ok: true, text: `Connected — ${models.length} models available.`, models });
    } catch (e) {
      setTest({ ok: false, text: errorText(e) });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const body: Partial<Settings> = isAdmin ? s : Object.fromEntries(PREF_FIELDS.map((k) => [k, s[k]]));
      const saved = await api.saveSettings(body);
      props.onSaved(saved);
      toast("Settings saved");
      props.onClose();
    } catch (e) {
      toast(errorText(e), true);
    } finally {
      setSaving(false);
    }
  };

  const pickFolder = async () => {
    try {
      const { path } = await api.pickFolder(s.knowledge_folder || props.settings.effective_knowledge_folder);
      if (path) set("knowledge_folder", path);
    } catch (e) {
      toast(errorText(e), true);
    }
  };

  const num = (k: keyof Settings, v: string) => set(k, (v === "" ? 0 : Number(v)) as never);

  return (
    <Modal
      title="Settings"
      icon={<SettingsIcon size={20} />}
      onClose={props.onClose}
      footer={
        <>
          <button className="btn" onClick={props.onClose}>Cancel</button>
          <button className="btn primary" onClick={save} disabled={saving}>
            {saving && <Loader2 size={15} className="spin" />} Save
          </button>
        </>
      }
    >
      <div className="tabs" role="tablist">
        {tabs.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab${tab === t ? " on" : ""}`} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === "connection" && (
        <>
          <div className="field">
            <label htmlFor="base_url">Base URL</label>
            <input id="base_url" className="input" value={s.base_url} onChange={(e) => set("base_url", e.target.value)}
              placeholder="https://api.openai.com/v1" />
            <span className="hint">Any OpenAI-compatible API: OpenAI, Azure, OpenRouter, Ollama, LM Studio, vLLM…</span>
          </div>
          <div className="field">
            <label htmlFor="api_key">API key</label>
            <div className="row">
              <input id="api_key" className="input" type={showKey ? "text" : "password"} value={s.api_key}
                autoComplete="off" onChange={(e) => set("api_key", e.target.value)}
                placeholder={props.settings.api_key_set ? "Saved — type a new key to replace it" : "Leave empty for local servers"} />
              <button className="icon-btn" onClick={() => setShowKey(!showKey)} aria-label={showKey ? "Hide key" : "Show key"}>
                {showKey ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </div>
            <span className="hint">{isLocal ? "Stored only on this computer, in config.json." : "Stored on the server, never sent to browsers. Users share this connection."}</span>
          </div>
          <div className="row">
            <button className="btn" onClick={runTest} disabled={testing || !s.base_url}>
              {testing ? <Loader2 size={15} className="spin" /> : <CheckCircle2 size={15} />} Test connection
            </button>
          </div>
          {test && <div className={test.ok ? "badge ok" : "error-text"} style={{ width: "fit-content" }}>{test.text}</div>}
          <div className="grid-2">
            <div className="field">
              <label htmlFor="default_model">Default model</label>
              <input id="default_model" className="input" list="model-options" value={s.default_model}
                onChange={(e) => set("default_model", e.target.value)} />
              <datalist id="model-options">{test?.models?.map((m) => <option key={m} value={m} />)}</datalist>
            </div>
            <div className="field">
              <label htmlFor="embedding_model">Embedding model</label>
              <input id="embedding_model" className="input" value={s.embedding_model}
                onChange={(e) => set("embedding_model", e.target.value)} />
              <span className="hint">Used for the knowledge base. Falls back to local keyword search if unavailable.</span>
            </div>
          </div>
        </>
      )}

      {tab === "chat" && (
        <>
          <div className="field">
            <label htmlFor="system_prompt">System prompt</label>
            <textarea id="system_prompt" className="textarea" value={s.system_prompt}
              onChange={(e) => set("system_prompt", e.target.value)} />
          </div>
          <div className="grid-2">
            <div className="field">
              <label htmlFor="temperature">Temperature ({s.temperature.toFixed(2)})</label>
              <input id="temperature" type="range" min={0} max={2} step={0.05} value={s.temperature}
                onChange={(e) => num("temperature", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="top_p">Top-P ({s.top_p.toFixed(2)})</label>
              <input id="top_p" type="range" min={0} max={1} step={0.05} value={s.top_p}
                onChange={(e) => num("top_p", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="max_tokens">Max tokens</label>
              <input id="max_tokens" className="input" type="number" min={1} value={s.max_tokens}
                onChange={(e) => num("max_tokens", e.target.value)} />
            </div>
          </div>
          <div className="section">
            <ToggleRow title="Disable thinking" checked={s.disable_thinking} onChange={(v) => set("disable_thinking", v)}
              hint="Ask reasoning models (Qwen3, DeepSeek…) to skip their thinking phase and hide <think> blocks." />
            <ToggleRow title="Memory" checked={s.memory_enabled} onChange={(v) => set("memory_enabled", v)}
              hint="Learn durable facts about you (preferences, role, tools) and use them in future chats." />
          </div>
        </>
      )}

      {tab === "knowledge" && (
        <>
          <div className="section">
            <ToggleRow title="Answer only from the Knowledge Base" checked={s.rag_enabled} onChange={(v) => set("rag_enabled", v)}
              hint="Replies use only matching document excerpts and end with a References list. MCP tools are off in this mode." />
          </div>
          <div className="field">
            <label htmlFor="kfolder">Knowledge folder</label>
            <div className="row">
              <input id="kfolder" className="input" value={s.knowledge_folder}
                placeholder={props.settings.effective_knowledge_folder} onChange={(e) => set("knowledge_folder", e.target.value)} />
              {isLocal && <button className="btn" onClick={pickFolder}><FolderOpen size={15} /> Browse</button>}
            </div>
            <span className="hint">Every supported document in this folder (and subfolders) is indexed and kept in sync automatically.</span>
          </div>
          {!isLocal && isAdmin && (
            <div className="field">
              <label htmlFor="kroots">Shared folder roots</label>
              <textarea id="kroots" className="input mono" rows={3} value={s.knowledge_roots}
                placeholder={"\\\\fileserver\\departments\n D:\\Shares\\Projects"}
                onChange={(e) => set("knowledge_roots", e.target.value)} />
              <span className="hint">
                One folder per line. Users (for their own documents) and group owners can link any folder inside these as a
                knowledge base. The server reads them with its own account, so anyone may link anything under a root that
                account can read: only list roots whose contents every user is allowed to see. Empty = linking is off.
              </span>
            </div>
          )}
          <div className="field">
            <label htmlFor="minscore">Minimum relevance ({s.rag_min_score.toFixed(2)})</label>
            <input id="minscore" type="range" min={0} max={1} step={0.05} value={s.rag_min_score}
              onChange={(e) => num("rag_min_score", e.target.value)} />
            <span className="hint">How closely an excerpt must match the question (API embeddings only).</span>
          </div>
        </>
      )}

      {tab === "tools" && (
        <div className="section">
          <ToggleRow title="Use MCP tools" checked={s.mcp_enabled} onChange={(v) => set("mcp_enabled", v)}
            hint="Offer tools from connected MCP servers to the model." />
          <ToggleRow title="Run tools without asking" checked={s.mcp_auto_approve} onChange={(v) => set("mcp_auto_approve", v)}
            hint="Off (recommended): you approve each tool call unless you chose “Always allow” for that tool." />
          <div className="field">
            <label htmlFor="rounds">Max tool rounds per reply</label>
            <input id="rounds" className="input" type="number" min={1} max={50} value={s.mcp_max_tool_rounds}
              style={{ maxWidth: 140 }} onChange={(e) => num("mcp_max_tool_rounds", e.target.value)} />
          </div>
        </div>
      )}

      {tab === "general" && (
        <>
          <div className="field">
            <label htmlFor="my_model">My default model</label>
            <input id="my_model" className="input" value={s.default_model} onChange={(e) => set("default_model", e.target.value)} />
            <span className="hint">Used for new chats. You can also switch models from the message box.</span>
          </div>
          <div className="section">
            <ToggleRow title="Memory" checked={s.memory_enabled} onChange={(v) => set("memory_enabled", v)}
              hint="Learn durable facts about you (preferences, role, tools) and use them in your future chats. Only you see your memory." />
            <ToggleRow title="Answer only from the Knowledge Base" checked={s.rag_enabled} onChange={(v) => set("rag_enabled", v)}
              hint="Replies use only the shared documents and cite them. MCP tools are off in this mode." />
            <ToggleRow title="Use MCP tools" checked={s.mcp_enabled} onChange={(v) => set("mcp_enabled", v)}
              hint="Let the assistant call the tools your admin set up. You approve each call." />
            <ToggleRow title="Disable thinking" checked={s.disable_thinking} onChange={(v) => set("disable_thinking", v)}
              hint="Ask reasoning models to skip their thinking phase for faster answers." />
          </div>
        </>
      )}

      {tab === "appearance" && (
        <div className="field">
          <span className="label">Theme</span>
          <div className="tabs">
            {(["system", "light", "dark"] as const).map((t) => (
              <button key={t} className={`tab${s.theme === t ? " on" : ""}`} onClick={() => set("theme", t)}>
                {t[0].toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}
