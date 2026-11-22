import { useMemo, useState } from "react";
import {
  BarChart3, BookOpen, Brain, Download, LogOut, MoreHorizontal, PanelLeftClose, Pencil, Plug, Search, Settings, Sparkles, SquarePen,
  Trash2, Users, UsersRound,
} from "lucide-react";
import type { ConversationSummary, Group, Me } from "../api";
import { api } from "../api";
import { useClickOutside } from "./ui";

export type PanelName = "settings" | "knowledge" | "memory" | "skills" | "mcp" | "users" | "groups" | "usage";

function groupOf(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.floor((today.getTime() - new Date(d).setHours(0, 0, 0, 0)) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  if (days < 30) return "Previous 30 days";
  return d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function ItemMenu(props: {
  convo: ConversationSummary;
  canRename: boolean;
  canDelete: boolean;
  onRename: () => void;
  onDelete: () => void;
}) {
  const gid = props.convo.group_id;
  const [open, setOpen] = useState(false);
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false));
  return (
    <div ref={ref} style={{ position: "relative" }} onClick={(e) => e.stopPropagation()}>
      <span
        role="button"
        tabIndex={0}
        className="icon-btn sm more"
        aria-label="Conversation options"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => e.key === "Enter" && setOpen(!open)}
      >
        <MoreHorizontal size={16} />
      </span>
      {open && (
        <div className="popover down-right menu">
          {props.canRename && (
            <button className="pop-item" onClick={() => (setOpen(false), props.onRename())}>
              <Pencil size={15} /> Rename
            </button>
          )}
          <a className="pop-item" href={api.exportUrl(props.convo.id, "md", gid)} onClick={() => setOpen(false)}>
            <Download size={15} /> Export Markdown
          </a>
          <a className="pop-item" href={api.exportUrl(props.convo.id, "json", gid)} onClick={() => setOpen(false)}>
            <Download size={15} /> Export JSON
          </a>
          {props.canDelete && <>
            <div className="pop-sep" />
            <button className="pop-item danger" onClick={() => (setOpen(false), props.onDelete())}>
              <Trash2 size={15} /> Delete
            </button>
          </>}
        </div>
      )}
    </div>
  );
}

export function Sidebar(props: {
  open: boolean;
  conversations: ConversationSummary[];
  currentId: string | null;
  liveId: string | null;
  mcpCount: number;
  me: Me | null;
  /** Server mode: the groups the user is in, the one being shown (null = their own chats), invites waiting. */
  groups: Group[];
  space: string | null;
  inviteCount: number;
  onSpace: (groupId: string | null) => void;
  onLogout: () => void;
  onToggle: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onPanel: (p: PanelName) => void;
}) {
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<{ id: string; title: string } | null>(null);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out: [string, ConversationSummary[]][] = [];
    for (const c of props.conversations) {
      if (q && !c.title.toLowerCase().includes(q)) continue;
      const g = groupOf(c.updated_at);
      const last = out[out.length - 1];
      if (last && last[0] === g) last[1].push(c);
      else out.push([g, [c]]);
    }
    return out;
  }, [props.conversations, query]);

  const server = props.me?.mode === "server";
  const group = props.groups.find((g) => g.id === props.space) ?? null;
  const mine = (c: ConversationSummary) => !c.group_id || c.user_id === props.me?.id;

  const commit = () => {
    if (editing && editing.title.trim()) props.onRename(editing.id, editing.title.trim());
    setEditing(null);
  };

  return (
    <aside className={`sidebar${props.open ? "" : " collapsed"}`} aria-hidden={!props.open}>
      <div className="sidebar-top">
        <div className="brand">
          <div className="logo" />
          CokCopilot
        </div>
        <button className="icon-btn" onClick={props.onToggle} aria-label="Close sidebar" title="Close sidebar">
          <PanelLeftClose size={18} />
        </button>
      </div>
      {server && (props.groups.length > 0 || props.space) && (
        <div className="space-switch">
          <UsersRound size={16} color="var(--muted)" />
          <select className="select" aria-label="Show chats from" value={props.space ?? ""}
            onChange={(e) => props.onSpace(e.target.value || null)}>
            <option value="">My chats</option>
            {props.groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
        </div>
      )}
      <button className="new-chat" onClick={props.onNew}>
        <SquarePen size={17} /> {group ? "New group chat" : "New chat"} <kbd>Ctrl N</kbd>
      </button>
      <label className="search">
        <Search size={15} />
        <input id="convo-search" placeholder="Search chats" value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      <nav className="convo-list scroll" aria-label="Conversations">
        {groups.length === 0 && (
          <div className="empty-list">
            {query ? "No chats match your search." : group ? `Chats in ${group.name} will show up here. Everyone in the group can read them.`
              : "Your chats will show up here."}
          </div>
        )}
        {groups.map(([label, items]) => (
          <div key={label}>
            <div className="group-label">{label}</div>
            {items.map((c) =>
              editing?.id === c.id ? (
                <div key={c.id} className="convo-item active">
                  <input
                    autoFocus
                    value={editing.title}
                    onChange={(e) => setEditing({ ...editing, title: e.target.value })}
                    onBlur={commit}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commit();
                      if (e.key === "Escape") setEditing(null);
                    }}
                  />
                </div>
              ) : (
                <div
                  key={c.id}
                  role="button"
                  tabIndex={0}
                  className={`convo-item${c.id === props.currentId ? " active" : ""}`}
                  onClick={() => props.onSelect(c.id)}
                  onKeyDown={(e) => e.key === "Enter" && props.onSelect(c.id)}
                  onDoubleClick={() => mine(c) && setEditing({ id: c.id, title: c.title })}
                  title={c.user_name ? `${c.title} — ${c.user_name}` : c.title}
                >
                  <span className="title">{c.title}</span>
                  {c.group_id && c.user_id !== props.me?.id && <span className="author">{c.user_name}</span>}
                  {c.id === props.liveId && <span className="live" aria-label="Generating" />}
                  <ItemMenu
                    convo={c}
                    canRename={mine(c)}
                    canDelete={mine(c) || !!group?.is_owner}
                    onRename={() => setEditing({ id: c.id, title: c.title })}
                    onDelete={() => props.onDelete(c.id)}
                  />
                </div>
              ),
            )}
          </div>
        ))}
      </nav>
      <div className="sidebar-nav">
        <button className="nav-item" onClick={() => props.onPanel("mcp")}>
          <Plug size={17} /> MCP servers
          {props.mcpCount > 0 && <span className="badge accent">{props.mcpCount}</span>}
        </button>
        <button className="nav-item" onClick={() => props.onPanel("knowledge")}>
          <BookOpen size={17} /> Knowledge base
        </button>
        <button className="nav-item" onClick={() => props.onPanel("skills")}>
          <Sparkles size={17} /> Skills
        </button>
        <button className="nav-item" onClick={() => props.onPanel("memory")}>
          <Brain size={17} /> Memory
        </button>
        {server && (
          <button className="nav-item" onClick={() => props.onPanel("groups")}>
            <UsersRound size={17} /> Groups
            {props.inviteCount > 0 && <span className="badge accent" title="Invitations waiting">{props.inviteCount}</span>}
          </button>
        )}
        {props.me?.mode === "server" && props.me.is_admin && (
          <button className="nav-item" onClick={() => props.onPanel("users")}>
            <Users size={17} /> Users
          </button>
        )}
        {props.me?.is_admin && (
          <button className="nav-item" onClick={() => props.onPanel("usage")}>
            <BarChart3 size={17} /> Usage
          </button>
        )}
        <button className="nav-item" onClick={() => props.onPanel("settings")}>
          <Settings size={17} /> Settings <kbd style={{ marginLeft: "auto" }}>Ctrl ,</kbd>
        </button>
        {props.me?.mode === "server" && (
          <div className="user-chip">
            <span className="avatar-sm">{props.me.name.slice(0, 1).toUpperCase()}</span>
            <span className="grow" title={props.me.name}>
              {props.me.name}{props.me.is_admin && <span className="hint"> · admin</span>}
            </span>
            <button className="icon-btn sm" onClick={props.onLogout} title="Sign out" aria-label="Sign out">
              <LogOut size={15} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
