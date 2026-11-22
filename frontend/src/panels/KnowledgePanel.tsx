import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, CircleAlert, FileText, FolderOpen, FolderSync, Link2Off, Loader2, RefreshCw, Trash2, Upload } from "lucide-react";
import type { Group, KBScope, KnowledgeState, Me } from "../api";
import { api } from "../api";
import { Modal, Switch, errorText, useToast } from "../components/ui";
import { FolderPicker } from "./FolderPicker";

export function KnowledgePanel(props: {
  me: Me;
  groups: Group[];
  initialGroup: string | null;
  onClose: () => void;
  ragOn: boolean;
  onToggleRag: (v: boolean) => void;
}) {
  const server = props.me.mode === "server";
  // Server mode: each user's own documents, the team's shared ones, and each group's. Local mode: one folder.
  // A tab is "mine", "team", or a group id.
  const [tab, setTab] = useState<string>(props.initialGroup ?? (server ? "mine" : "team"));
  const scope: KBScope = tab === "mine" || tab === "team" ? tab : "group";
  const groupId = scope === "group" ? tab : undefined;
  const group = props.groups.find((g) => g.id === groupId);
  const [kb, setKb] = useState<KnowledgeState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const scopeRef = useRef(tab);
  scopeRef.current = tab;
  const toast = useToast();

  const load = useCallback(async () => {
    const asked = tab;
    try {
      const k = await api.knowledge(scope, groupId);
      // Ignore a reply that arrives after the user switched tabs.
      if (scopeRef.current !== asked) return;
      setKb(k);
      setLoadError("");
    } catch (e) {
      if (scopeRef.current === asked) setLoadError(errorText(e));
    }
  }, [tab, scope, groupId]);
  useEffect(() => {
    setKb(null);
    setLoadError("");
    setPicking(false);
    load();
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [load]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    try {
      const r = await fn();
      if (ok) toast(ok);
      else if (r && typeof r === "object" && "summary" in r) toast(String((r as { summary: string }).summary));
      await load();
    } catch (e) {
      toast(errorText(e), true);
    } finally {
      setBusy(false);
    }
  };

  const linked = !!kb?.linked;
  // Linked to a shared folder: files are managed in that folder, not uploaded or deleted here.
  const canManage = !!kb?.can_manage && !linked;
  const personal = server && scope !== "team"; // the user's own, or their group's: managed in the app
  // Unlinking needs no allowed roots (an admin may have removed them); for a group only its owner may do it.
  const canUnlink = linked && (scope === "mine" || !!group?.is_owner);
  const link = (folder: string) => act(async () => {
    await api.linkKnowledgeFolder(scope, folder, groupId);
    setPicking(false);
  }, folder ? "Folder linked — indexing…" : "Back to uploaded documents");
  const subtitle = !server
    ? kb?.folder || "Documents the assistant can answer from"
    : group
      ? `Everyone in ${group.name} can see and change these. Chats in the group answer from them.`
    : personal
      ? "Only you can see and search these"
      : props.me.is_admin && kb?.folder ? kb.folder : "Shared with everyone on this server";

  return (
    <Modal title="Knowledge base" subtitle={subtitle} icon={<BookOpen size={20} />} onClose={props.onClose} wide>
      <div className="section">
        <div className="toggle-row">
          <div className="text">
            <b>Answer only from the Knowledge Base</b>
            <span className="hint">
              {server
                ? "When on, replies use only your documents (in a group chat, the group's) and the team documents, and cite them."
                : "When on, replies use only your documents and cite them. When off, the folder is still indexed."}
            </span>
          </div>
          <Switch checked={props.ragOn} onChange={props.onToggleRag} label="Knowledge-only mode" />
        </div>
      </div>

      {server && (
        <div className="tabs" role="tablist">
          {[["mine", "My documents"], ["team", "Team documents"], ...props.groups.map((g) => [g.id, g.name])].map(([s, label]) => (
            <button key={s} role="tab" aria-selected={tab === s} className={`tab${tab === s ? " on" : ""}`}
              onClick={() => setTab(s)}>{label}</button>
          ))}
        </div>
      )}

      {kb && !kb.can_manage && (
        <div className="readonly-note">Your admin manages team documents. You can ask about them in any chat.</div>
      )}
      {kb && linked && (
        <div className="linked-note">
          <FolderSync size={17} />
          <span className="path" title={kb.linked_folder}>{kb.linked_folder}</span>
          {canUnlink && (
            <button className="btn sm" disabled={busy}
              onClick={() => confirm(`Stop using ${kb.linked_folder}? ${group ? `${group.name}'s` : "Your"} uploaded documents come back. Files in the folder aren't touched.`) && link("")}>
              <Link2Off size={14} /> Unlink
            </button>
          )}
        </div>
      )}
      {kb && linked && (
        <span className="hint">
          Synced with this folder and all its subfolders. Add, change or remove files there; changes show up within about a minute.
          {kb.limit_reached && " Only the first 5,000 documents are indexed; link a smaller folder to include everything."}
        </span>
      )}
      {picking && <FolderPicker busy={busy} onPick={(p) => link(p)} onCancel={() => setPicking(false)} />}
      <div className="row wrap" style={canManage || linked ? undefined : { display: "none" }}>
        <input ref={fileRef} type="file" multiple hidden disabled={linked} accept=".pdf,.docx,.txt,.md,.csv,.json,.xml,.html"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = "";
            if (files.length) act(async () => {
              const r = await api.uploadKnowledge(scope, files, groupId);
              r.errors.forEach((x) => toast(x, true));
            }, `Added ${files.length} file(s) — indexing…`);
          }} />
        <button className="btn primary" onClick={() => fileRef.current?.click()} disabled={busy}
          style={linked ? { display: "none" } : undefined}>
          <Upload size={15} /> Add documents
        </button>
        {kb?.can_link && !picking && (
          <button className="btn" onClick={() => setPicking(true)} disabled={busy}
            title="Index a shared folder (and all its subfolders) instead of uploading files">
            <FolderSync size={15} /> {linked ? "Change folder" : "Use a shared folder"}
          </button>
        )}
        {!server && (
          <button className="btn" onClick={() => act(() => api.openKnowledgeFolder())}><FolderOpen size={15} /> Open folder</button>
        )}
        {(!personal || linked) && (
          <button className="btn" onClick={() => act(() => api.syncKnowledge(scope, false, undefined, groupId))} disabled={busy}>
            <RefreshCw size={15} /> Rescan
          </button>
        )}
        <button className="btn ghost" onClick={() => act(() => api.syncKnowledge(scope, true, undefined, groupId))} disabled={busy}>
          Re-index all
        </button>
        <span className="spacer" />
        {kb && (
          <span className="hint">
            {kb.syncing ? <><Loader2 size={13} className="spin" /> {kb.progress || "Syncing…"}</> :
              `${kb.files.length} files · ${kb.embedding_method === "api" ? "API embeddings" : "local keyword index"}`}
          </span>
        )}
      </div>
      {personal && !linked && <span className="hint">PDF, Word, Markdown, text, CSV, JSON, XML or HTML · up to 25 MB each, 200 MB in total.</span>}

      <div className="list">
        {kb && kb.files.length === 0 && (
          <div className="list-empty">
            {linked ? (kb.syncing ? "Indexing the folder…" : "No supported documents in this folder yet (PDF, Word, Markdown, text, CSV, JSON, XML, HTML).")
              : group ? `No documents in ${group.name} yet. Anyone in the group can add them.`
              : personal ? "You haven't added any documents yet. Add files to ask questions about them."
              : canManage ? "No documents yet. Add PDF, Word, Markdown or text files" + (server ? "." : " — or drop them into the folder with any app.")
              : "No team documents yet."}
          </div>
        )}
        {!kb && !loadError && <div className="list-empty"><Loader2 size={16} className="spin" /></div>}
        {!kb && loadError && (
          <div className="list-empty">
            <div className="error-text">Couldn't load documents: {loadError}</div>
            <button className="btn sm" style={{ marginTop: 10 }} onClick={() => { setLoadError(""); load(); }}>Try again</button>
          </div>
        )}
        {kb?.files.map((f) => (
          <div key={f.id} className="list-item">
            {f.status === "error" ? <CircleAlert size={18} color="var(--danger)" /> : <FileText size={18} color="var(--muted)" />}
            <div className="grow">
              <div className="name" title={f.rel_path}>{f.rel_path}</div>
              <div className="sub" title={f.error}>
                {f.status === "error" ? f.error : f.status === "indexing" ? "Indexing…" : `${f.chunk_count} excerpts`}
              </div>
            </div>
            <span className={`badge ${f.status === "indexed" ? "ok" : f.status === "error" ? "err" : "warn"}`}>{f.status}</span>
            {canManage && <>
              <button className="icon-btn sm" title="Re-index" aria-label={`Re-index ${f.filename}`}
                onClick={() => act(() => api.syncKnowledge(scope, true, f.id, groupId))}><RefreshCw size={15} /></button>
              <button className="icon-btn sm" title={server ? "Delete" : "Move to Recycle Bin"} aria-label={`Delete ${f.filename}`}
                onClick={() => confirm(group ? `Delete "${f.rel_path}" for everyone in ${group.name}? This can't be undone.`
                  : server ? `Delete "${f.rel_path}"? This can't be undone.` : `Move "${f.rel_path}" to the Recycle Bin?`) &&
                  act(() => api.deleteKnowledgeFile(scope, f.id, groupId), "Removed")}><Trash2 size={15} /></button>
            </>}
          </div>
        ))}
      </div>
    </Modal>
  );
}
