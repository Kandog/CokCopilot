import { useEffect, useState } from "react";
import { Check, Copy, KeyRound, Mail, Plus, ShieldCheck, Trash2, Users } from "lucide-react";
import type { UserInfo } from "../api";
import { api } from "../api";
import { Modal, Switch, copyText, errorText, useToast } from "../components/ui";
import { SsoSection } from "./SsoSection";

export function UsersPanel(props: { onClose: () => void; meId: string }) {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const [issued, setIssued] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const toast = useToast();

  const load = () => api.users().then(setUsers).catch((e) => toast(errorText(e), true));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (e) {
      toast(errorText(e), true);
    }
    load();
  };

  const add = () => act(async () => {
    const r = await api.addUser(name, role, email.trim());
    setIssued({ name: r.user.name, key: r.key });
    setName("");
    setEmail("");
    setRole("user");
  });

  return (
    <Modal title="Users" subtitle="Who can use CokCopilot, and how they sign in" icon={<Users size={20} />}
      onClose={props.onClose} wide>
      <SsoSection />
      {issued && (
        <div className="section" style={{ borderColor: "var(--accent)" }}>
          <h3><KeyRound size={16} /> Access key for {issued.name}</h3>
          <span className="hint">
            Copy it now and send it to them privately. It won't be shown again. (Not needed if they sign in with Microsoft.)
          </span>
          <div className="row">
            <input className="input mono" readOnly value={issued.key} onFocus={(e) => e.target.select()} />
            <button className="btn" onClick={async () => {
              await copyText(issued.key);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}>{copied ? <Check size={15} /> : <Copy size={15} />} Copy</button>
            <button className="btn ghost" onClick={() => setIssued(null)}>Done</button>
          </div>
        </div>
      )}
      <div className="row wrap">
        <input className="input" style={{ flex: 1, minWidth: 180 }} placeholder="Name, e.g. Alice" value={name}
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && add()} />
        <input className="input" style={{ flex: 1, minWidth: 200 }} type="email" placeholder="Work email, for Microsoft sign-in"
          value={email} onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && name.trim() && add()} aria-label="Work email" />
        <div className="tabs">
          {(["user", "admin"] as const).map((r) => (
            <button key={r} className={`tab${role === r ? " on" : ""}`} onClick={() => setRole(r)}>
              {r === "user" ? "User" : "Admin"}
            </button>
          ))}
        </div>
        <button className="btn primary" disabled={!name.trim()} onClick={add}><Plus size={15} /> Add user</button>
      </div>
      <span className="hint">
        Admins can change the AI connection, MCP servers, knowledge base, skills and users. Users chat with their own
        history and memory.
      </span>
      <div className="list">
        {users.map((u) => (
          <div key={u.id} className="list-item">
            <div className="grow">
              <div className="name">{u.name}{u.id === props.meId && <span className="badge" style={{ marginLeft: 8 }}>you</span>}</div>
              <div className="sub">
                <button className="link-btn" title="Email they sign in with through Microsoft"
                  onClick={() => {
                    const next = prompt(`Work email for ${u.name} (used to sign in with Microsoft). Leave empty to remove.`, u.email);
                    if (next !== null && next.trim().toLowerCase() !== u.email) act(() => api.updateUser(u.id, { email: next.trim() }));
                  }}>
                  <Mail size={12} style={{ verticalAlign: -1 }} /> {u.email || "add email"}
                </button>
                {u.sso_linked && <span title="Has signed in with Microsoft"> · <ShieldCheck size={12} style={{ verticalAlign: -1 }} /> linked</span>}
                {" "}· key {u.key_hint}… · added {u.created_at ? new Date(u.created_at).toLocaleDateString() : "—"}
              </div>
            </div>
            <select className="select" style={{ width: 110 }} value={u.role} aria-label={`Role of ${u.name}`}
              onChange={(e) => act(() => api.updateUser(u.id, { role: e.target.value as UserInfo["role"] }))}>
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
            <span className="hint" style={{ width: 60, textAlign: "right" }}>{u.disabled ? "Blocked" : "Active"}</span>
            <Switch checked={!u.disabled} label={`Allow ${u.name} to sign in`}
              onChange={(v) => act(() => api.updateUser(u.id, { disabled: !v }))} />
            <button className="btn sm" title="Issue a new key; the old one stops working"
              onClick={() => confirm(`Issue a new key for ${u.name}? Their current key stops working and they're signed out.`) &&
                act(async () => setIssued({ name: u.name, key: (await api.resetUserKey(u.id)).key }))}>
              <KeyRound size={14} /> New key
            </button>
            <button className="icon-btn sm" aria-label={`Remove ${u.name}`} title="Remove user"
              onClick={() => confirm(`Remove ${u.name}? They can't sign in any more. Their chats stay on the server.`) &&
                act(() => api.deleteUser(u.id))}>
              <Trash2 size={15} />
            </button>
          </div>
        ))}
      </div>
    </Modal>
  );
}
