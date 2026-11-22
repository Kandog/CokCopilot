import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Code2, Eye, Lightbulb, PanelLeftOpen, Plug, SquarePen, UsersRound } from "lucide-react";
import type {
  Attachment, Conversation, ConversationSummary, GroupList, McpServer, Me, Settings, Skill, ToolCall,
} from "./api";
import { ApiError, api, streamChat } from "./api";
import { Login } from "./components/Login";
import { Composer } from "./components/Composer";
import { AssistantMessage, UserMessage } from "./components/MessageView";
import { Sidebar, type PanelName } from "./components/Sidebar";
import { errorText, useToast } from "./components/ui";
import { GroupsPanel } from "./panels/GroupsPanel";
import { KnowledgePanel } from "./panels/KnowledgePanel";
import { McpPanel } from "./panels/McpPanel";
import { MemoryPanel } from "./panels/MemoryPanel";
import { SettingsPanel } from "./panels/SettingsPanel";
import { SkillsPanel } from "./panels/SkillsPanel";
import { UsagePanel } from "./panels/UsagePanel";
import { UsersPanel } from "./panels/UsersPanel";

interface Live {
  convoId: string | null;
  streamId: string | null;
  text: string;
  toolCalls: ToolCall[];
  notices: string[];
  abort: AbortController;
}

const SUGGESTIONS = [
  { icon: <Lightbulb size={17} />, title: "Explain a concept", text: "Explain how vector embeddings work, with a simple analogy." },
  { icon: <Code2 size={17} />, title: "Write some code", text: "Write a TypeScript function that debounces another function, with tests." },
  { icon: <BookOpen size={17} />, title: "Ask your documents", text: "Summarize what my knowledge base says about " },
  { icon: <Plug size={17} />, title: "Use your tools", text: "Use the available tools to " },
];

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Working late?" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function useTheme(theme: Settings["theme"] | undefined, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = theme === "dark" || (theme !== "light" && mq.matches);
      document.documentElement.dataset.theme = dark ? "dark" : "light";
    };
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [theme, enabled]);
}

/**
 * A system notification when the page may show one without asking: always in the desktop app,
 * and in a browser only if the user already allowed this site. Never prompts.
 */
function notify(title: string, body: string, onClick: () => void) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted" || document.hasFocus()) return;
  try {
    const n = new Notification(title, { body, icon: "/icon.png" });
    n.onclick = () => { window.focus(); onClick(); n.close(); };
  } catch {
    /* some browsers only allow notifications from a service worker */
  }
}

/** Checks who is signed in (server mode) and shows the sign-in screen or the app. */
export default function Root() {
  const [me, setMe] = useState<Me | null | "loading" | "signed-out">("loading");
  const [error, setError] = useState("");
  // While signed out the system theme applies; once signed in, App applies the user's choice.
  useTheme(undefined, typeof me !== "object" || me === null);

  const check = useCallback(() => {
    api.me().then(setMe).catch((e) => {
      if (e instanceof ApiError && e.status === 401) setMe("signed-out");
      else setError(`Can't reach the CokCopilot server: ${errorText(e)}`);
    });
  }, []);

  useEffect(() => {
    check();
    const onOut = () => setMe("signed-out");
    window.addEventListener("cok:signed-out", onOut);
    return () => window.removeEventListener("cok:signed-out", onOut);
  }, [check]);

  if (me === "signed-out") return <Login onSignedIn={setMe} />;
  if (me === "loading" || me === null) {
    return (
      <div className="login-wrap">
        {error ? (
          <div className="login-card">
            <div className="error-text">{error}</div>
            <button className="btn" onClick={() => { setError(""); check(); }}>Try again</button>
          </div>
        ) : <div className="typing"><i /><i /><i /></div>}
      </div>
    );
  }
  return (
    <App key={me.id} me={me} onLogout={async () => {
      await api.logout().catch(() => {});
      setMe("signed-out");
    }} />
  );
}

function App({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const toast = useToast();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [newModel, setNewModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [streamError, setStreamError] = useState<{ convoId: string | null; text: string } | null>(null);
  const [panel, setPanel] = useState<PanelName | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 860);
  const [draft, setDraft] = useState({ text: "", nonce: 0 });
  // Server mode: the group whose chats the sidebar shows (null = the user's own chats).
  const [space, setSpace] = useState<string | null>(null);
  const [groupList, setGroupList] = useState<GroupList>({ groups: [], invites: [] });
  const seenInvites = useRef<Set<string> | null>(null);
  const spaceRef = useRef<string | null>(null);
  spaceRef.current = space;
  const server = me.mode === "server";
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const currentRef = useRef<Conversation | null>(null);
  currentRef.current = current;

  useTheme(settings?.theme);

  const refreshConversations = useCallback(() => {
    const asked = spaceRef.current;
    return api.conversations(asked).then((list) => {
      if (spaceRef.current === asked) setConversations(list); // ignore replies for a space the user left
    }).catch(() => {});
  }, []);
  const refreshGroups = useCallback(() => {
    if (!server) return Promise.resolve();
    return api.groups().then((r) => {
      setGroupList(r);
      // Tell the user about invitations that arrived since the last check (not the ones there at sign-in).
      const ids = new Set(r.invites.map((g) => g.id));
      const fresh = seenInvites.current ? r.invites.filter((g) => !seenInvites.current!.has(g.id)) : [];
      seenInvites.current = ids;
      for (const g of fresh) {
        const text = `${g.owner_name} invited you to the group ${g.name}`;
        toast(`${text}. Open Groups to join.`);
        notify("Group invitation", text, () => setPanel("groups"));
      }
      // Left, removed or deleted: back to the user's own chats.
      if (spaceRef.current && !r.groups.some((g) => g.id === spaceRef.current)) setSpace(null);
    }).catch(() => {});
  }, [server, toast]);
  const refreshModels = useCallback(() => api.models().then((r) => setModels(r.models)).catch(() => setModels([])), []);
  const refreshSkills = useCallback(() => api.skills().then(setSkills).catch(() => {}), []);
  const refreshMcp = useCallback(() => api.mcp().then((r) => setMcpServers(r.servers)).catch(() => {}), []);

  useEffect(() => {
    api.settings().then((s) => {
      setSettings(s);
      if (me.is_admin && !s.api_key_set && s.base_url.includes("openai.com")) setPanel("settings");
    }).catch((e) => toast(`Can't reach the backend: ${errorText(e)}`, true));
    refreshModels();
    refreshSkills();
    refreshMcp();
    refreshGroups();
    const t = setInterval(refreshMcp, 8000);
    const g = setInterval(refreshGroups, 20000);
    return () => { clearInterval(t); clearInterval(g); };
  }, [refreshModels, refreshSkills, refreshMcp, refreshGroups, toast, me.is_admin]);

  // The sidebar's chats: the user's own, or a group's. Group chats are polled so new ones from others show up.
  useEffect(() => {
    setConversations([]);
    refreshConversations();
    if (!space) return;
    const t = setInterval(refreshConversations, 10000);
    return () => clearInterval(t);
  }, [space, refreshConversations]);

  const group = groupList.groups.find((g) => g.id === space) ?? null;

  // Keep the view pinned to the bottom while streaming unless the user scrolled up.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottom.current && (current?.messages.length || live)) el.scrollTop = el.scrollHeight;
  }, [current?.messages.length, live?.text, live?.toolCalls.length, streamError]);

  const busy = !!live;
  const model = current ? current.model || settings?.default_model || "" : newModel || settings?.default_model || "";

  const newChat = useCallback(() => {
    setCurrent(null);
    setStreamError(null);
    setNewModel("");
    if (window.innerWidth <= 860) setSidebarOpen(false);
  }, []);

  const switchSpace = (id: string | null) => {
    setSpace(id);
    setCurrent(null);
    setStreamError(null);
    setNewModel("");
  };

  const select = async (id: string) => {
    if (window.innerWidth <= 860) setSidebarOpen(false);
    try {
      setCurrent(await api.conversation(id, spaceRef.current));
      setStreamError(null);
      stickToBottom.current = true;
    } catch (e) {
      toast(errorText(e), true);
      refreshConversations();
    }
  };

  const updateSettings = async (patch: Partial<Settings>) => {
    try {
      setSettings(await api.saveSettings(patch));
    } catch (e) {
      toast(errorText(e), true);
    }
  };

  const run = async (req: Parameters<typeof streamChat>[0], editId?: string) => {
    const abort = new AbortController();
    const startConvo = currentRef.current?.id ?? null;
    setLive({ convoId: startConvo, streamId: null, text: "", toolCalls: [], notices: [], abort });
    setStreamError(null);
    stickToBottom.current = true;
    let convoId = startConvo;
    try {
      await streamChat(req, (ev) => {
        switch (ev.type) {
          case "start": {
            convoId = ev.conversation.id;
            setLive((l) => l && { ...l, convoId, streamId: ev.stream_id });
            if ((ev.conversation.group_id || null) === spaceRef.current) {
              setConversations((list) => [ev.conversation, ...list.filter((c) => c.id !== ev.conversation.id)]);
            }
            // Show the chat only if the user is still looking at where it started (they may have
            // switched chats while waiting). Decided here, not inside the state updater: React may
            // run updaters twice (StrictMode in dev), and the ref changes between those runs.
            if ((currentRef.current?.id ?? null) !== startConvo) break;
            const userMessage = ev.user_message;
            const summary = ev.conversation;
            setCurrent((cur) => {
              const base: Conversation = cur && cur.id === summary.id ? cur : {
                ...summary, system_prompt: "", messages: [],
              };
              let messages = base.messages;
              if (req.regenerate) {
                messages = [...messages];
                while (messages.length && messages[messages.length - 1].role === "assistant") messages.pop();
              } else if (editId) {
                const i = messages.findIndex((m) => m.id === editId);
                messages = i >= 0 ? messages.slice(0, i) : messages;
              }
              if (userMessage) messages = [...messages, userMessage];
              return { ...base, title: summary.title, model: summary.model, messages };
            });
            break;
          }
          case "text":
            setLive((l) => l && { ...l, text: l.text + ev.text });
            break;
          case "notice":
            setLive((l) => l && { ...l, notices: [...l.notices, ev.text] });
            break;
          case "tool_call":
            setLive((l) => {
              if (!l) return l;
              const i = l.toolCalls.findIndex((c) => c.id === ev.call.id);
              const toolCalls = i >= 0 ? l.toolCalls.map((c, j) => (j === i ? ev.call : c)) : [...l.toolCalls, ev.call];
              return { ...l, toolCalls };
            });
            break;
          case "error":
            setStreamError({ convoId, text: ev.error });
            break;
          case "done":
            if ((ev.conversation.group_id || null) === spaceRef.current) {
              setConversations((list) => [ev.conversation, ...list.filter((c) => c.id !== ev.conversation.id)]);
            }
            setCurrent((cur) => cur && cur.id === ev.conversation.id && ev.message
              ? { ...cur, messages: [...cur.messages, ev.message] } : cur);
            setLive(null);
            break;
          case "memory":
            toast(`Remembered: ${ev.added.join("; ")}`);
            break;
        }
      }, abort.signal);
    } catch (e) {
      if (!abort.signal.aborted) setStreamError({ convoId, text: errorText(e) });
    } finally {
      setLive((l) => (l?.abort === abort ? null : l));
    }
  };

  const send = (text: string, attachments: Attachment[], editId?: string) => {
    if (busy) return;
    run({
      conversation_id: current?.id,
      content: text,
      attachments,
      model: current ? undefined : newModel || undefined,
      edit_message_id: editId,
      group_id: (current ? current.group_id : space) || undefined,
    }, editId);
  };

  const stop = () => {
    if (!live) return;
    if (live.streamId) api.stop(live.streamId).catch(() => live.abort.abort());
    else live.abort.abort();
  };

  const decide = (callId: string, approved: boolean, always: boolean) => {
    if (live?.streamId) api.approve(live.streamId, callId, approved, always).catch((e) => toast(errorText(e), true));
    if (always) setTimeout(refreshMcp, 300);
  };

  const changeModel = async (m: string) => {
    if (!current) return setNewModel(m);
    try {
      await api.patchConversation(current.id, { model: m }, current.group_id);
      setCurrent({ ...current, model: m });
    } catch (e) {
      toast(errorText(e), true);
    }
  };

  const rename = async (id: string, title: string) => {
    try {
      const s = await api.patchConversation(id, { title }, conversations.find((c) => c.id === id)?.group_id);
      setConversations((list) => list.map((c) => (c.id === id ? s : c)));
      if (current?.id === id) setCurrent({ ...current, title: s.title });
    } catch (e) {
      toast(errorText(e), true);
    }
  };

  const remove = async (id: string) => {
    const c = conversations.find((x) => x.id === id);
    const others = c?.group_id && c.user_id !== me.id ? ` It was started by ${c.user_name}, and` : "";
    if (!confirm(`Delete this chat?${others ? `${others} everyone in the group loses it.` : ""} This can't be undone.`)) return;
    try {
      await api.deleteConversation(id, c?.group_id);
      setConversations((list) => list.filter((c) => c.id !== id));
      if (current?.id === id) newChat();
    } catch (e) {
      toast(errorText(e), true);
    }
  };

  const toggleServer = async (name: string, enabled: boolean) => {
    try {
      await api.setMcpEnabled(name, enabled);
    } catch (e) {
      toast(errorText(e), true);
    }
    refreshMcp();
  };

  // Shortcuts: Ctrl+N new chat, Ctrl+, settings, Ctrl+K search, Esc stop.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "n") { e.preventDefault(); newChat(); }
      else if (mod && e.key === ",") { e.preventDefault(); setPanel("settings"); }
      else if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault(); setSidebarOpen(true);
        setTimeout(() => document.getElementById("convo-search")?.focus(), 50);
      } else if (e.key === "Escape" && live && !panel) stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const liveHere = live && (live.convoId === (current?.id ?? null) || (!current && !live.convoId));
  const messages = current?.messages ?? [];
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const connectedServers = mcpServers.filter((s) => s.status === "connected").length;
  const showWelcome = messages.length === 0 && !liveHere;
  // Group members read each other's chats; only whoever started one continues it.
  const readOnly = !!current?.group_id && current.user_id !== me.id;
  const currentGroup = current?.group_id ? groupList.groups.find((g) => g.id === current.group_id) : group;

  return (
    <div className="app">
      <Sidebar
        open={sidebarOpen}
        conversations={conversations}
        currentId={current?.id ?? null}
        liveId={live?.convoId ?? null}
        mcpCount={connectedServers}
        me={me}
        groups={groupList.groups}
        space={space}
        inviteCount={groupList.invites.length}
        onSpace={switchSpace}
        onLogout={onLogout}
        onToggle={() => setSidebarOpen(false)}
        onNew={newChat}
        onSelect={select}
        onRename={rename}
        onDelete={remove}
        onPanel={(p) => { setPanel(p); if (p === "skills") refreshSkills(); if (p === "groups") refreshGroups(); }}
      />
      <main className="main">
        <header className="topbar">
          {!sidebarOpen && (
            <>
              <button className="icon-btn" onClick={() => setSidebarOpen(true)} aria-label="Open sidebar" title="Open sidebar">
                <PanelLeftOpen size={18} />
              </button>
              <button className="icon-btn" onClick={newChat} aria-label="New chat" title="New chat (Ctrl+N)">
                <SquarePen size={18} />
              </button>
            </>
          )}
          <div className="convo-title">{current?.title ?? ""}</div>
          <span className="spacer" />
          {currentGroup && (
            <span className="badge" title="Everyone in this group can read its chats">
              <UsersRound size={12} /> {currentGroup.name}{current?.user_name ? ` · ${current.user_id === me.id ? "you" : current.user_name}` : ""}
            </span>
          )}
          {settings?.rag_enabled && <span className="badge accent"><BookOpen size={12} /> Knowledge only</span>}
        </header>

        <div className="chat-scroll scroll" ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          }}>
          {showWelcome ? (
            <div className="welcome">
              <div className="logo lg" />
              <h1>{group ? `New chat in ${group.name}` : `${greeting()}! How can I help?`}</h1>
              {group && (
                <p>Everyone in {group.name} can read this chat. Answers use the group's documents and the team's.</p>
              )}
              <p>
                {connectedServers > 0 ? `${connectedServers} MCP server${connectedServers > 1 ? "s" : ""} connected · ` : ""}
                {skills.length > 0 ? `${skills.length} skills — type / to use one` : "Ask anything, attach files, or connect tools"}
              </p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s.title} className="suggestion" onClick={() => setDraft({ text: s.text, nonce: Date.now() })}>
                    <span className="s-icon">{s.icon}</span>
                    <span><b>{s.title}</b><span>{s.text}</span></span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="chat-inner">
              {messages.map((m) =>
                m.role === "user" ? (
                  <UserMessage key={m.id} message={m} busy={busy || readOnly} onEdit={(t) => send(t, [], m.id)} />
                ) : m.role === "assistant" ? (
                  <AssistantMessage key={m.id} content={m.content} toolCalls={m.tool_calls}
                    isLast={!liveHere && m === lastAssistant && m === messages[messages.length - 1]} busy={busy || readOnly}
                    onRegenerate={() => run({ conversation_id: current!.id, regenerate: true, group_id: current!.group_id })} />
                ) : null,
              )}
              {liveHere && live && (
                <AssistantMessage content={live.text} toolCalls={live.toolCalls} notices={live.notices} streaming
                  onDecide={decide} canAlwaysAllow={me.is_admin} />
              )}
              {streamError && streamError.convoId === (current?.id ?? null) && !liveHere && (
                <div className="error-banner">{streamError.text}</div>
              )}
            </div>
          )}
        </div>

        {readOnly ? (
          <div className="readonly-note group-note">
            <Eye size={15} /> {current?.user_name} started this chat, so only they can continue it.
            <button className="btn sm" onClick={newChat}><SquarePen size={14} /> New group chat</button>
          </div>
        ) : <Composer
          busy={busy}
          canStop={!!liveHere}
          skills={skills}
          model={model}
          models={models}
          ragOn={!!settings?.rag_enabled}
          mcpEnabled={!!settings?.mcp_enabled}
          mcpServers={mcpServers}
          isAdmin={me.is_admin}
          draft={draft}
          onSend={(t, a) => send(t, a)}
          onStop={stop}
          onModel={changeModel}
          onRefreshModels={refreshModels}
          onToggleRag={(v) => updateSettings({ rag_enabled: v })}
          onToggleMcp={(v) => updateSettings({ mcp_enabled: v })}
          onToggleServer={toggleServer}
          onOpenPanel={(p) => { setPanel(p); if (p === "skills") refreshSkills(); }}
        />}
      </main>

      {panel === "settings" && settings && (
        <SettingsPanel settings={settings} me={me} onClose={() => setPanel(null)}
          onSaved={(s) => { setSettings(s); refreshModels(); }} />
      )}
      {panel === "knowledge" && (
        <KnowledgePanel me={me} groups={groupList.groups} initialGroup={currentGroup?.id ?? null}
          onClose={() => setPanel(null)} ragOn={!!settings?.rag_enabled}
          onToggleRag={(v) => updateSettings({ rag_enabled: v })} />
      )}
      {panel === "memory" && (
        <MemoryPanel onClose={() => setPanel(null)} enabled={!!settings?.memory_enabled}
          onToggle={(v) => updateSettings({ memory_enabled: v })} />
      )}
      {panel === "skills" && <SkillsPanel isAdmin={me.is_admin} onClose={() => setPanel(null)} onChanged={refreshSkills} />}
      {panel === "mcp" && <McpPanel isAdmin={me.is_admin} onClose={() => setPanel(null)} onChanged={setMcpServers} />}
      {panel === "users" && me.is_admin && <UsersPanel meId={me.id} onClose={() => setPanel(null)} />}
      {panel === "usage" && me.is_admin && <UsagePanel onClose={() => setPanel(null)} />}
      {panel === "groups" && server && (
        <GroupsPanel me={me} groups={groupList} onChanged={refreshGroups} onClose={() => setPanel(null)}
          onOpenGroup={(id) => { switchSpace(id); setPanel(null); setSidebarOpen(true); }} />
      )}
    </div>
  );
}
