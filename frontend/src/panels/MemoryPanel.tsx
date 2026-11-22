import { useEffect, useState } from "react";
import { Brain, Plus, Trash2 } from "lucide-react";
import type { MemoryEntry } from "../api";
import { api } from "../api";
import { Modal, Switch, errorText, useToast } from "../components/ui";

export function MemoryPanel(props: { onClose: () => void; enabled: boolean; onToggle: (v: boolean) => void }) {
  const [items, setItems] = useState<MemoryEntry[]>([]);
  const [draft, setDraft] = useState("");
  const toast = useToast();
  const load = () => api.memory().then(setItems).catch((e) => toast(errorText(e), true));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const add = async () => {
    if (!draft.trim()) return;
    try {
      await api.addMemory(draft.trim());
      setDraft("");
      load();
    } catch (e) {
      toast(errorText(e), true);
    }
  };

  return (
    <Modal title="Memory" subtitle="What CokCopilot remembers about you across chats" icon={<Brain size={20} />} onClose={props.onClose}>
      <div className="section">
        <div className="toggle-row">
          <div className="text">
            <b>Learn from conversations</b>
            <span className="hint">After each reply, durable facts (preferences, role, tools you use) are saved here. Nothing sensitive.</span>
          </div>
          <Switch checked={props.enabled} onChange={props.onToggle} label="Memory" />
        </div>
      </div>
      <div className="row">
        <input className="input" placeholder="Add something to remember, e.g. “I prefer TypeScript examples”" value={draft}
          onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        <button className="btn primary" onClick={add} disabled={!draft.trim()}><Plus size={15} /> Add</button>
      </div>
      <div className="list">
        {items.length === 0 && <div className="list-empty">Nothing remembered yet.</div>}
        {items.map((m) => (
          <div key={m.id} className="list-item">
            <div className="grow" style={{ whiteSpace: "normal" }}>{m.text}</div>
            <span className="hint">{m.created_at ? new Date(m.created_at).toLocaleDateString() : ""}</span>
            <button className="icon-btn sm" aria-label="Forget" title="Forget"
              onClick={() => api.deleteMemory(m.id).then(load)}><Trash2 size={15} /></button>
          </div>
        ))}
      </div>
      {items.length > 0 && (
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="btn danger" onClick={() => confirm("Forget everything in memory? This can't be undone.") &&
            api.clearMemory().then(load)}>Forget all</button>
        </div>
      )}
    </Modal>
  );
}
