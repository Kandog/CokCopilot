import { useEffect, useState } from "react";
import { Check, Crown, LogOut, Mail, MessageSquare, Pencil, Plus, Trash2, UserPlus, UsersRound, X } from "lucide-react";
import type { Group, GroupList, Me, Person } from "../api";
import { api } from "../api";
import { Modal, errorText, useToast } from "../components/ui";

/** Create groups, invite people, answer invitations. Server mode only. */
export function GroupsPanel(props: {
  me: Me;
  groups: GroupList;
  onChanged: () => void;
  onOpenGroup: (id: string) => void;
  onClose: () => void;
}) {
  const toast = useToast();
  const [newName, setNewName] = useState("");
  const [people, setPeople] = useState<Person[]>([]);
  const [openId, setOpenId] = useState<string | null>(props.groups.groups[0]?.id ?? null);

  useEffect(() => { api.people().then(setPeople).catch(() => {}); }, []);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    try {
      await fn();
      if (ok) toast(ok);
    } catch (e) {
      toast(errorText(e), true);
    }
    props.onChanged();
  };

  const create = () => act(async () => {
    const g = await api.createGroup(newName);
    setNewName("");
    setOpenId(g.id);
  }, "Group created");

  const { groups, invites } = props.groups;

  return (
    <Modal title="Groups" subtitle="Share chats and documents with the people you invite" icon={<UsersRound size={20} />}
      onClose={props.onClose} wide>
      {invites.length > 0 && (
        <div className="section" style={{ borderColor: "var(--accent)" }}>
          <h3><Mail size={16} /> Invitations</h3>
          <div className="list">
            {invites.map((g) => (
              <div key={g.id} className="list-item">
                <div className="grow">
                  <div className="name">{g.name}</div>
                  <div className="sub">{g.owner_name} invited you · {g.members.length} member{g.members.length === 1 ? "" : "s"}</div>
                </div>
                <button className="btn primary sm" onClick={() => act(async () => {
                  await api.respondToInvite(g.id, true);
                  setOpenId(g.id);
                }, `You joined ${g.name}`)}><Check size={14} /> Join</button>
                <button className="btn sm" onClick={() => act(() => api.respondToInvite(g.id, false))}>Decline</button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="row">
        <input className="input" style={{ flex: 1 }} placeholder="New group name, e.g. Marketing" value={newName}
          maxLength={80} onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && newName.trim() && create()} />
        <button className="btn primary" disabled={!newName.trim()} onClick={create}><Plus size={15} /> Create group</button>
      </div>
      <span className="hint">
        Everyone in a group can read all of the group's chats and add documents to its knowledge base. Chats in a group
        answer from the group's documents and the team's, never from anyone's own documents or memory.
      </span>

      <div className="list">
        {groups.length === 0 && <div className="list-empty">You're not in any groups yet. Create one and invite people.</div>}
        {groups.map((g) => (
          <GroupRow key={g.id} group={g} me={props.me} people={people} open={openId === g.id}
            onToggle={() => setOpenId(openId === g.id ? null : g.id)}
            onOpenChats={() => props.onOpenGroup(g.id)} act={act} />
        ))}
      </div>
    </Modal>
  );
}

function GroupRow(props: {
  group: Group;
  me: Me;
  people: Person[];
  open: boolean;
  onToggle: () => void;
  onOpenChats: () => void;
  act: (fn: () => Promise<unknown>, ok?: string) => Promise<void>;
}) {
  const g = props.group;
  const [invitee, setInvitee] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const taken = new Set([...g.members.map((m) => m.id), ...(g.invites ?? []).map((i) => i.id)]);
  const candidates = props.people.filter((p) => !taken.has(p.id));
  const listId = `invite-${g.id}`;

  const invite = () => props.act(async () => {
    await api.inviteToGroup(g.id, invitee);
    setInvitee("");
  }, `Invited ${invitee.trim()}`);

  return (
    <div className="group-block">
      <div className="list-item" role="button" tabIndex={0} style={{ cursor: "pointer" }} onClick={props.onToggle}
        onKeyDown={(e) => e.key === "Enter" && props.onToggle()} aria-expanded={props.open}>
        <UsersRound size={18} color="var(--muted)" />
        <div className="grow">
          {renaming !== null ? (
            <input className="input" autoFocus value={renaming} maxLength={80} onClick={(e) => e.stopPropagation()}
              onChange={(e) => setRenaming(e.target.value)}
              onBlur={() => setRenaming(null)}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === "Escape") setRenaming(null);
                if (e.key === "Enter" && renaming.trim()) {
                  const name = renaming;
                  setRenaming(null);
                  props.act(() => api.renameGroup(g.id, name));
                }
              }} />
          ) : (
            <>
              <div className="name">{g.name}{g.is_owner && <span className="badge" style={{ marginLeft: 8 }}>owner</span>}</div>
              <div className="sub">
                {g.members.length} member{g.members.length === 1 ? "" : "s"}
                {g.invites && g.invites.length > 0 ? ` · ${g.invites.length} invited` : ""}
                {!g.is_owner && ` · owner ${g.owner_name}`}
              </div>
            </>
          )}
        </div>
        <button className="btn sm" onClick={(e) => { e.stopPropagation(); props.onOpenChats(); }}>
          <MessageSquare size={14} /> Open chats
        </button>
      </div>

      {props.open && (
        <div className="group-detail">
          <div className="label">Members</div>
          <div className="chips">
            {g.members.map((m) => (
              <span key={m.id} className="chip">
                {m.is_owner && <Crown size={12} />} {m.name}{m.id === props.me.id && " (you)"}
                {g.is_owner && !m.is_owner && (
                  <button className="chip-x" aria-label={`Remove ${m.name}`} title="Remove from group"
                    onClick={() => confirm(`Remove ${m.name} from ${g.name}? They lose access to its chats and documents.`) &&
                      props.act(() => api.removeGroupMember(g.id, m.id))}><X size={12} /></button>
                )}
              </span>
            ))}
            {(g.invites ?? []).map((i) => (
              <span key={i.id} className="chip pending" title="Invited — hasn't joined yet">
                <Mail size={12} /> {i.name}
                <button className="chip-x" aria-label={`Cancel invitation for ${i.name}`} title="Cancel invitation"
                  onClick={() => props.act(() => api.cancelGroupInvite(g.id, i.id))}><X size={12} /></button>
              </span>
            ))}
          </div>

          {g.is_owner && (
            <div className="row">
              <input className="input" style={{ flex: 1 }} list={listId} placeholder="Invite someone by name"
                value={invitee} onChange={(e) => setInvitee(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && invitee.trim() && invite()} />
              <datalist id={listId}>
                {candidates.map((p) => <option key={p.id} value={p.name} />)}
              </datalist>
              <button className="btn" disabled={!invitee.trim()} onClick={invite}><UserPlus size={15} /> Invite</button>
            </div>
          )}

          <div className="row wrap">
            {g.is_owner ? (
              <>
                <button className="btn sm" onClick={() => setRenaming(g.name)}><Pencil size={14} /> Rename</button>
                <span className="spacer" />
                <button className="btn sm danger" onClick={() =>
                  confirm(`Delete ${g.name}? All of its chats and documents are deleted for everyone. This can't be undone.`) &&
                  props.act(() => api.deleteGroup(g.id), "Group deleted")}>
                  <Trash2 size={14} /> Delete group
                </button>
              </>
            ) : (
              <>
                <span className="spacer" />
                <button className="btn sm danger" onClick={() =>
                  confirm(`Leave ${g.name}? You'll lose access to its chats and documents until you're invited again.`) &&
                  props.act(() => api.removeGroupMember(g.id, props.me.id), `You left ${g.name}`)}>
                  <LogOut size={14} /> Leave group
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
