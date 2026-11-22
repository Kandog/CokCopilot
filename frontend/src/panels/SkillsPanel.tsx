import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Pencil, Plus, Sparkles, Trash2, Upload } from "lucide-react";
import type { Skill } from "../api";
import { ApiError, api } from "../api";
import { Modal, errorText, useToast } from "../components/ui";

interface Draft {
  name: string;
  description: string;
  instructions: string;
  original?: string;
}

export function SkillsPanel(props: { isAdmin: boolean; onClose: () => void; onChanged: () => void }) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  const load = () => api.skills().then((s) => { setSkills(s); props.onChanged(); }).catch((e) => toast(errorText(e), true));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const install = async (file: File) => {
    try {
      const r = await api.installSkill(file);
      toast(`Installed /${r.name}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && confirm(`${e.message}. Replace it?`)) {
        const r = await api.installSkill(file, true);
        toast(`Updated /${r.name}`);
      } else if (!(e instanceof ApiError && e.status === 409)) {
        toast(errorText(e), true);
      }
    }
    load();
  };

  const save = async () => {
    if (!draft) return;
    setErr("");
    try {
      const r = await api.saveSkill({ name: draft.name, description: draft.description, instructions: draft.instructions,
        original_name: draft.original });
      toast(`Saved /${r.name}`);
      setDraft(null);
      load();
    } catch (e) {
      setErr(errorText(e));
    }
  };

  const edit = async (name: string) => {
    try {
      const s = await api.skill(name);
      setDraft({ name: s.name, description: s.description, instructions: s.instructions ?? "", original: s.name });
    } catch (e) {
      toast(errorText(e), true);
    }
  };

  if (draft) {
    return (
      <Modal title={draft.original ? `Edit /${draft.original}` : "New skill"} icon={<Sparkles size={20} />} onClose={props.onClose}
        footer={<>
          <button className="btn" onClick={() => setDraft(null)}><ArrowLeft size={15} /> Back</button>
          <button className="btn primary" onClick={save}>Save skill</button>
        </>}>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="sk-name">Name</label>
            <input id="sk-name" className="input" value={draft.name} placeholder="summarize"
              onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <span className="hint">Used as /{draft.name.trim().toLowerCase().replace(/[^\p{L}\p{N}_-]+/gu, "-") || "name"}</span>
          </div>
          <div className="field">
            <label htmlFor="sk-desc">Description</label>
            <input id="sk-desc" className="input" value={draft.description} placeholder="What it does, in one line"
              onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
          </div>
        </div>
        <div className="field">
          <label htmlFor="sk-body">Instructions</label>
          <textarea id="sk-body" className="textarea mono" style={{ minHeight: 260 }} value={draft.instructions}
            placeholder="Summarize the text the user gives you as at most five short bullet points…"
            onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} />
        </div>
        {err && <div className="error-text">{err}</div>}
      </Modal>
    );
  }

  return (
    <Modal title="Skills" subtitle="Reusable instructions — type / in the message box to use one" icon={<Sparkles size={20} />}
      onClose={props.onClose}>
      {!props.isAdmin && <div className="readonly-note">Your admin manages skills. Type / in the message box to use one.</div>}
      <div className="row wrap" style={props.isAdmin ? undefined : { display: "none" }}>
        <button className="btn primary" onClick={() => setDraft({ name: "", description: "", instructions: "" })}>
          <Plus size={15} /> New skill
        </button>
        <input ref={fileRef} type="file" hidden accept=".md,.zip"
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) install(f); }} />
        <button className="btn" onClick={() => fileRef.current?.click()}><Upload size={15} /> Install SKILL.md or .zip</button>
      </div>
      <div className="list">
        {skills.length === 0 && <div className="list-empty">No skills installed yet.</div>}
        {skills.map((s) => (
          <div key={s.name} className="list-item">
            <Sparkles size={17} color="var(--accent)" />
            <div className="grow">
              <div className="name">/{s.name}</div>
              <div className="sub">{s.description || "No description"}</div>
            </div>
            {props.isAdmin && <>
            <button className="icon-btn sm" aria-label={`Edit ${s.name}`} title="Edit" onClick={() => edit(s.name)}><Pencil size={15} /></button>
            <button className="icon-btn sm" aria-label={`Delete ${s.name}`} title="Move to Recycle Bin"
              onClick={() => confirm(`Remove /${s.name}?`) && api.deleteSkill(s.name).then(load)}><Trash2 size={15} /></button>
            </>}
          </div>
        ))}
      </div>
      <p className="hint" style={{ margin: 0 }}>
        Skills use the Agent Skills <code>SKILL.md</code> format, so skills shared online install as they are. Small text files in a
        skill's folder are sent to the model with its instructions.
      </p>
    </Modal>
  );
}
