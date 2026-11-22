import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, BookOpen, ChevronDown, FileText, Loader2, Paperclip, Plug, Search, Sparkles, Square, X } from "lucide-react";
import type { Attachment, McpServer, Skill } from "../api";
import { api } from "../api";
import { Switch, errorText, useClickOutside, useToast } from "./ui";

const ACCEPT = ".png,.jpg,.jpeg,.gif,.webp,.bmp,.pdf,.docx,.txt,.md,.csv,.json,.xml,.html";

function ModelPicker(props: { value: string; models: string[]; onChange: (m: string) => void; onRefresh: () => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false));
  const list = useMemo(() => {
    const all = props.models.includes(props.value) || !props.value ? props.models : [props.value, ...props.models];
    return all.filter((m) => m.toLowerCase().includes(q.toLowerCase())).slice(0, 200);
  }, [props.models, props.value, q]);
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button className="model-pill" title="Model for this chat"
        onClick={() => { setOpen(!open); if (!open && props.models.length === 0) props.onRefresh(); }}>
        <span>{props.value || "Choose model"}</span> <ChevronDown size={14} />
      </button>
      {open && (
        <div className="popover up" style={{ left: "auto", right: 0, width: 320 }}>
          <div className="model-search">
            <label className="search" style={{ margin: 0 }}>
              <Search size={14} />
              <input autoFocus placeholder="Search or type a model id" value={q} onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && q.trim()) { props.onChange(list[0] ?? q.trim()); setOpen(false); setQ(""); }
                }} />
            </label>
          </div>
          {list.map((m) => (
            <button key={m} className={`pop-item${m === props.value ? " sel" : ""}`}
              onClick={() => { props.onChange(m); setOpen(false); setQ(""); }}>
              <span className="grow" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{m}</span>
            </button>
          ))}
          {list.length === 0 && q.trim() && (
            <button className="pop-item" onClick={() => { props.onChange(q.trim()); setOpen(false); setQ(""); }}>
              Use “{q.trim()}”
            </button>
          )}
          {props.models.length === 0 && !q && (
            <div className="hint" style={{ padding: 10 }}>No models listed. Check Settings → Connection, or type a model id.</div>
          )}
        </div>
      )}
    </div>
  );
}

function ToolsMenu(props: {
  servers: McpServer[];
  enabled: boolean;
  ragOn: boolean;
  isAdmin: boolean;
  onToggleAll: (v: boolean) => void;
  onToggleServer: (name: string, v: boolean) => void;
  onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false));
  const toolCount = props.servers
    .filter((s) => s.status === "connected")
    .reduce((n, s) => n + s.tools.filter((t) => t.enabled).length, 0);
  const active = props.enabled && !props.ragOn && toolCount > 0;
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button className={`chip${active ? " on" : ""}`} onClick={() => setOpen(!open)} title="MCP tools">
        <Plug size={15} /> <span className="chip-label">Tools</span> {toolCount > 0 && <span className="count">{toolCount}</span>}
      </button>
      {open && (
        <div className="popover up" style={{ width: 320 }}>
          <div className="pop-item check" style={{ cursor: "default" }}>
            <span className="grow"><b style={{ fontWeight: 600 }}>Use MCP tools</b>
              <div className="desc">{props.ragOn ? "Off while Knowledge-only mode is on" : "Let the assistant call tools"}</div>
            </span>
            <Switch checked={props.enabled} onChange={props.onToggleAll} label="Use MCP tools" />
          </div>
          <div className="pop-sep" />
          <div className="pop-title">Servers</div>
          {props.servers.length === 0 && <div className="hint" style={{ padding: "4px 10px 8px" }}>No MCP servers yet.</div>}
          {props.servers.map((s) => (
            <div key={s.name} className="pop-item check" style={{ cursor: "default" }}>
              <span className={`dot ${s.status}`} />
              <span className="grow">
                <div style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{s.name}</div>
                <div className="desc">
                  {s.status === "connected" ? `${s.tools.filter((t) => t.enabled).length} tools` : s.status}
                </div>
              </span>
              {props.isAdmin && (
                <Switch checked={!s.config.disabled} onChange={(v) => props.onToggleServer(s.name, v)} label={`Enable ${s.name}`} />
              )}
            </div>
          ))}
          <div className="pop-sep" />
          <button className="pop-item" onClick={() => { setOpen(false); props.onManage(); }}>
            <Plug size={15} /> {props.isAdmin ? "Manage MCP servers…" : "View MCP servers…"}
          </button>
        </div>
      )}
    </div>
  );
}

export function Composer(props: {
  busy: boolean;
  canStop: boolean;
  skills: Skill[];
  model: string;
  models: string[];
  ragOn: boolean;
  mcpEnabled: boolean;
  mcpServers: McpServer[];
  isAdmin: boolean;
  draft: { text: string; nonce: number };
  onSend: (text: string, attachments: Attachment[]) => void;
  onStop: () => void;
  onModel: (m: string) => void;
  onRefreshModels: () => void;
  onToggleRag: (v: boolean) => void;
  onToggleMcp: (v: boolean) => void;
  onToggleServer: (name: string, v: boolean) => void;
  onOpenPanel: (p: "mcp" | "skills" | "knowledge") => void;
}) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(0);
  const [pick, setPick] = useState(0);
  const [dragging, setDragging] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  useEffect(() => {
    if (props.draft.nonce) {
      setText(props.draft.text);
      taRef.current?.focus();
    }
  }, [props.draft]);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    const fit = () => {
      ta.style.height = "auto";
      if (ta.value) ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`;
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(ta.parentElement!);
    return () => ro.disconnect();
  }, [text]);

  useEffect(() => {
    taRef.current?.focus();
  }, []);

  // `/` at the start of the message (before any space) lists matching skills.
  const slash = /^\/(\S*)$/.exec(text);
  const skillMatches = useMemo(() => {
    if (!slash) return [];
    const q = slash[1].toLowerCase();
    const starts = props.skills.filter((s) => s.name.startsWith(q));
    return [...starts, ...props.skills.filter((s) => !s.name.startsWith(q) && s.name.includes(q))];
  }, [slash?.[1], props.skills]); // eslint-disable-line react-hooks/exhaustive-deps
  const showSkills = !!slash;

  const addFiles = async (files: File[]) => {
    if (!files.length) return;
    setUploading((n) => n + files.length);
    try {
      const res = await api.attach(files);
      res.filter((a) => a.error).forEach((a) => toast(`${a.name}: ${a.error}`, true));
      setAttachments((cur) => [...cur, ...res.filter((a) => !a.error)]);
    } catch (e) {
      toast(errorText(e), true);
    } finally {
      setUploading((n) => n - files.length);
    }
  };

  const canSend = !props.busy && uploading === 0 && (text.trim().length > 0 || attachments.length > 0);
  const send = () => {
    if (!canSend) return;
    props.onSend(text, attachments);
    setText("");
    setAttachments([]);
  };
  const choose = (name: string) => {
    setText(`/${name} `);
    setPick(0);
    taRef.current?.focus();
  };

  return (
    <div className="composer-wrap">
      <div
        className={`composer${dragging ? " dragging" : ""}`}
        onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragging(true); } }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(Array.from(e.dataTransfer.files)); }}
      >
        {showSkills && (
          <div className="popover up" style={{ width: "min(420px, 100%)" }}>
            <div className="pop-title">Skills</div>
            {skillMatches.map((s, i) => (
              <button key={s.name} className={`pop-item${i === pick ? " sel" : ""}`}
                onMouseDown={(e) => { e.preventDefault(); choose(s.name); }}>
                <Sparkles size={15} color="var(--accent)" />
                <span className="grow"><div>/{s.name}</div><div className="desc">{s.description}</div></span>
              </button>
            ))}
            {skillMatches.length === 0 && <div className="hint" style={{ padding: "4px 10px 8px" }}>No matching skills.</div>}
            <div className="pop-sep" />
            <button className="pop-item" onMouseDown={(e) => { e.preventDefault(); props.onOpenPanel("skills"); }}>
              {props.isAdmin ? "Manage skills…" : "View skills…"}
            </button>
          </div>
        )}
        {(attachments.length > 0 || uploading > 0) && (
          <div className="pending-attachments">
            {attachments.map((a, i) => (
              <span key={i} className="attach-chip">
                {a.kind === "image" ? <img src={a.data} alt="" /> : <FileText size={15} />}
                <span style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.name}</span>
                <button className="icon-btn sm" style={{ width: 20, height: 20 }} aria-label={`Remove ${a.name}`}
                  onClick={() => setAttachments(attachments.filter((_, j) => j !== i))}>
                  <X size={13} />
                </button>
              </span>
            ))}
            {uploading > 0 && <span className="attach-chip"><Loader2 size={14} className="spin" /> Reading…</span>}
          </div>
        )}
        <textarea
          ref={taRef}
          rows={1}
          value={text}
          placeholder="Message CokCopilot — type / for skills"
          onChange={(e) => { setText(e.target.value); setPick(0); }}
          onPaste={(e) => {
            const files = Array.from(e.clipboardData.files);
            if (files.length) { e.preventDefault(); addFiles(files); }
          }}
          onKeyDown={(e) => {
            if (showSkills && skillMatches.length) {
              if (e.key === "ArrowDown") { e.preventDefault(); setPick((pick + 1) % skillMatches.length); return; }
              if (e.key === "ArrowUp") { e.preventDefault(); setPick((pick - 1 + skillMatches.length) % skillMatches.length); return; }
              if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
                e.preventDefault();
                choose(skillMatches[Math.min(pick, skillMatches.length - 1)].name);
                return;
              }
            }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          aria-label="Message"
        />
        <div className="composer-bar">
          <input ref={fileRef} type="file" multiple accept={ACCEPT} hidden
            onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
          <button className="icon-btn" title="Attach images or documents" aria-label="Attach files"
            onClick={() => fileRef.current?.click()}>
            <Paperclip size={18} />
          </button>
          <ToolsMenu servers={props.mcpServers} enabled={props.mcpEnabled} ragOn={props.ragOn} isAdmin={props.isAdmin}
            onToggleAll={props.onToggleMcp} onToggleServer={props.onToggleServer} onManage={() => props.onOpenPanel("mcp")} />
          <button className={`chip${props.ragOn ? " on" : ""}`} onClick={() => props.onToggleRag(!props.ragOn)}
            title="Knowledge-only mode: answer only from your knowledge base">
            <BookOpen size={15} /> <span className="chip-label">Knowledge</span>
          </button>
          <span className="spacer" />
          <ModelPicker value={props.model} models={props.models} onChange={props.onModel} onRefresh={props.onRefreshModels} />
          {props.canStop ? (
            <button className="send-btn" onClick={props.onStop} title="Stop generating" aria-label="Stop">
              <Square size={14} fill="currentColor" />
            </button>
          ) : (
            <button className="send-btn" onClick={send} disabled={!canSend} title="Send (Enter)" aria-label="Send">
              <ArrowUp size={18} strokeWidth={2.4} />
            </button>
          )}
        </div>
      </div>
      <div className="composer-foot">
        {props.ragOn ? "Knowledge-only mode: answers come only from your knowledge base." :
          "AI can make mistakes. Check important info."} · Shift+Enter for a new line
      </div>
    </div>
  );
}
