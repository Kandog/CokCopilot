// Types and HTTP helpers for the CokCopilot Python backend.

export type ToolStatus = "pending" | "awaiting_approval" | "running" | "done" | "error" | "denied";

export interface ToolCall {
  id: string;
  server: string;
  tool: string;
  arguments: Record<string, unknown>;
  result: string;
  status: ToolStatus;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
  images: string[];
  document_texts: string[];
  skill?: string;
  attachments?: string[];
  tool_calls?: ToolCall[];
}

export interface ConversationSummary {
  id: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  /** Group chats only: the group, and who started the chat (only they can continue it). */
  group_id?: string;
  user_id?: string;
  user_name?: string;
}

export interface Conversation extends Omit<ConversationSummary, "message_count"> {
  system_prompt: string;
  messages: Message[];
}

export interface Settings {
  base_url: string;
  api_key: string;
  default_model: string;
  embedding_model: string;
  system_prompt: string;
  temperature: number;
  max_tokens: number;
  top_p: number;
  theme: "system" | "light" | "dark";
  rag_enabled: boolean;
  rag_min_score: number;
  knowledge_folder: string;
  /** Server mode: folders (one per line) under which users and groups may link a shared folder. */
  knowledge_roots: string;
  memory_enabled: boolean;
  disable_thinking: boolean;
  mcp_enabled: boolean;
  mcp_auto_approve: boolean;
  mcp_max_tool_rounds: number;
  effective_knowledge_folder?: string;
  api_key_set?: boolean;
}

export interface Me {
  id: string;
  name: string;
  role: "admin" | "user";
  is_admin: boolean;
  mode: "local" | "server";
  version: string;
  email?: string;
  /** Single sign-on is turned on for this server. */
  sso?: boolean;
}

export interface AuthOptions {
  mode: "local" | "server";
  sso: boolean;
  sso_label: string;
  /** "admins": SSO is on and access keys only work for admins. */
  keys: "all" | "admins";
}

export interface SsoConfig {
  enabled: boolean;
  tenant_id: string;
  client_id: string;
  client_secret: string;
  client_secret_set: boolean;
  redirect_uri: string;
  default_redirect_uri: string;
  allow_access_keys: boolean;
  button_label: string;
  problems: string[];
}

export interface UsageRow {
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  tool_calls: number;
  errors: number;
  estimated: boolean;
  last_active: string;
  top_model: string;
}

export interface UsageSummary {
  days: number;
  since: string;
  until: string;
  user_id: string;
  totals: UsageRow & { active_users: number };
  users: (UsageRow & { user_id: string; name: string; removed: boolean })[];
  models: (UsageRow & { model: string })[];
  daily: { date: string; requests: number; total_tokens: number }[];
}

export interface FolderListing {
  path: string;
  /** null at the list of allowed roots; "" inside a root (go back to the roots). */
  parent: string | null;
  dirs: { name: string; path: string }[];
}

export interface UserInfo {
  id: string;
  name: string;
  role: "admin" | "user";
  key_hint: string;
  created_at: string;
  disabled: boolean;
  email: string;
  /** Has signed in with Microsoft at least once (bound to that account). */
  sso_linked: boolean;
}

export interface Person {
  id: string;
  name: string;
}

export interface GroupMember extends Person {
  is_owner: boolean;
}

export interface Group {
  id: string;
  name: string;
  owner_id: string;
  owner_name: string;
  created_at: string;
  is_owner: boolean;
  is_member: boolean;
  members: GroupMember[];
  /** Pending invitations; sent to the owner only. */
  invites?: Person[];
}

export interface GroupList {
  groups: Group[];
  invites: Group[];
}

export interface Attachment {
  name: string;
  kind?: "image" | "document";
  size?: number;
  data?: string;
  text?: string;
  error?: string;
}

export interface Skill {
  name: string;
  description: string;
  instructions?: string;
  files?: string[];
}

export interface MemoryEntry {
  id: string;
  text: string;
  created_at: string;
}

export interface KBFile {
  id: string;
  rel_path: string;
  filename: string;
  chunk_count: number;
  status: "indexed" | "indexing" | "error";
  error: string;
  size: number;
}

export type KBScope = "mine" | "team" | "group";

export interface KnowledgeState {
  scope: KBScope;
  personal_available: boolean;
  can_manage: boolean;
  folder: string;
  folder_exists: boolean;
  embedding_method: string;
  syncing: boolean;
  progress: string;
  last_sync: string;
  files: KBFile[];
  /** Mirrors a linked shared folder (server mode): files are added and removed in that folder. */
  linked: boolean;
  linked_folder: string;
  can_link: boolean;
  limit_reached: boolean;
}

export interface McpTool {
  name: string;
  title: string;
  description: string;
  enabled: boolean;
  auto_approve: boolean;
  read_only: boolean;
  input_schema: Record<string, unknown>;
}

export interface McpServerConfig {
  type: "stdio" | "http" | "sse";
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  cwd?: string;
  url?: string;
  headers?: Record<string, string>;
  disabled?: boolean;
  disabledTools?: string[];
  autoApprove?: string[];
}

export interface McpServer {
  name: string;
  config: McpServerConfig;
  status: "disabled" | "connecting" | "connected" | "error";
  error: string;
  server_label: string;
  tools: McpTool[];
  log_tail: string;
}

export type StreamEvent =
  | { type: "start"; stream_id: string; conversation: ConversationSummary; user_message: Message | null }
  | { type: "text"; text: string }
  | { type: "notice"; text: string }
  | { type: "tool_call"; call: ToolCall }
  | { type: "error"; error: string }
  | { type: "done"; message: Message | null; conversation: ConversationSummary }
  | { type: "memory"; added: string[] };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(`/api${path}`, init);
  if (res.status === 401 && !path.startsWith("/auth/")) signalSignedOut();
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

/** Fired when the server says the session is gone (key reset, disabled, expired). */
export function signalSignedOut() {
  window.dispatchEvent(new Event("cok:signed-out"));
}

const enc = encodeURIComponent;
const inGroup = (groupId: string | null | undefined, sep: "?" | "&") => (groupId ? `${sep}group_id=${enc(groupId)}` : "");

export const api = {
  me: () => request<Me>("GET", "/me"),
  login: (key: string) => request<Me>("POST", "/auth/login", { key }),
  authOptions: () => request<AuthOptions>("GET", "/auth/options"),
  ssoConfig: () => request<SsoConfig>("GET", "/admin/sso"),
  saveSsoConfig: (c: Partial<SsoConfig>) => request<SsoConfig>("PUT", "/admin/sso", c),
  checkSsoTenant: (tenant_id: string) => request<{ ok: boolean; issuer: string }>("POST", "/admin/sso/check", { tenant_id }),
  usage: (days: number, userId?: string) =>
    request<UsageSummary>("GET", `/usage?days=${days}${userId ? `&user_id=${enc(userId)}` : ""}`),
  usageExportUrl: (days: number) => `/api/usage/export?days=${days}`,
  logout: () => request("POST", "/auth/logout"),
  users: () => request<UserInfo[]>("GET", "/users"),
  addUser: (name: string, role: "admin" | "user", email = "") =>
    request<{ user: UserInfo; key: string }>("POST", "/users", { name, role, email }),
  resetUserKey: (id: string) => request<{ key: string }>("POST", `/users/${enc(id)}/reset-key`),
  updateUser: (id: string, patch: Partial<Pick<UserInfo, "role" | "disabled" | "name" | "email">>) =>
    request<UserInfo>("PATCH", `/users/${enc(id)}`, patch),
  deleteUser: (id: string) => request("DELETE", `/users/${enc(id)}`),
  people: () => request<Person[]>("GET", "/people"),

  groups: () => request<GroupList>("GET", "/groups"),
  createGroup: (name: string) => request<Group>("POST", "/groups", { name }),
  renameGroup: (id: string, name: string) => request<Group>("PATCH", `/groups/${enc(id)}`, { name }),
  deleteGroup: (id: string) => request("DELETE", `/groups/${enc(id)}`),
  inviteToGroup: (id: string, name: string) => request<Group>("POST", `/groups/${enc(id)}/invites`, { name }),
  cancelGroupInvite: (id: string, userId: string) =>
    request<Group>("DELETE", `/groups/${enc(id)}/invites/${enc(userId)}`),
  respondToInvite: (id: string, accept: boolean) => request<Group>("POST", `/groups/${enc(id)}/respond`, { accept }),
  removeGroupMember: (id: string, userId: string) => request("DELETE", `/groups/${enc(id)}/members/${enc(userId)}`),

  settings: () => request<Settings>("GET", "/settings"),
  saveSettings: (s: Partial<Settings>) => request<Settings>("PUT", "/settings", s),
  models: (overrides?: Partial<Settings>) => request<{ models: string[] }>("POST", "/models", overrides ?? null),

  // `groupId`: a group's chats instead of the user's own.
  conversations: (groupId?: string | null) => request<ConversationSummary[]>("GET", `/conversations${inGroup(groupId, "?")}`),
  conversation: (id: string, groupId?: string | null) =>
    request<Conversation>("GET", `/conversations/${enc(id)}${inGroup(groupId, "?")}`),
  patchConversation: (id: string, patch: Partial<Conversation>, groupId?: string | null) =>
    request<ConversationSummary>("PATCH", `/conversations/${enc(id)}${inGroup(groupId, "?")}`, patch),
  deleteConversation: (id: string, groupId?: string | null) =>
    request("DELETE", `/conversations/${enc(id)}${inGroup(groupId, "?")}`),
  exportUrl: (id: string, format: "md" | "json", groupId?: string | null) =>
    `/api/conversations/${enc(id)}/export?format=${format}${inGroup(groupId, "&")}`,

  stop: (streamId: string) => request("POST", `/streams/${enc(streamId)}/stop`),
  approve: (streamId: string, callId: string, approved: boolean, always = false) =>
    request("POST", `/streams/${enc(streamId)}/approve`, { call_id: callId, approved, always }),

  attach: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return request<Attachment[]>("POST", "/attachments", fd);
  },

  memory: () => request<MemoryEntry[]>("GET", "/memory"),
  addMemory: (text: string) => request<MemoryEntry>("POST", "/memory", { text }),
  deleteMemory: (id: string) => request("DELETE", `/memory/${enc(id)}`),
  clearMemory: () => request("DELETE", "/memory"),

  skills: () => request<Skill[]>("GET", "/skills"),
  skill: (name: string) => request<Skill>("GET", `/skills/${enc(name)}`),
  saveSkill: (s: { name: string; description: string; instructions: string; original_name?: string; overwrite?: boolean }) =>
    request<{ name: string }>("POST", "/skills", s),
  installSkill: (file: File, overwrite = false) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("overwrite", String(overwrite));
    return request<{ name: string }>("POST", "/skills/install", fd);
  },
  deleteSkill: (name: string) => request("DELETE", `/skills/${enc(name)}`),

  // `groupId` is required when scope is "group".
  knowledge: (scope: KBScope, groupId?: string) =>
    request<KnowledgeState>("GET", `/knowledge?scope=${scope}${inGroup(groupId, "&")}`),
  uploadKnowledge: (scope: KBScope, files: File[], groupId?: string) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("scope", scope);
    if (groupId) fd.append("group_id", groupId);
    return request<{ errors: string[] }>("POST", "/knowledge/upload", fd);
  },
  syncKnowledge: (scope: KBScope, reindex = false, fileId?: string, groupId?: string) =>
    request<{ summary: string }>("POST", "/knowledge/sync", { scope, reindex, file_id: fileId, group_id: groupId }),
  deleteKnowledgeFile: (scope: KBScope, id: string, groupId?: string) =>
    request("DELETE", `/knowledge/files/${enc(id)}?scope=${scope}${inGroup(groupId, "&")}`),
  browseFolders: (path = "") => request<FolderListing>("GET", `/knowledge/browse?path=${enc(path)}`),
  linkKnowledgeFolder: (scope: KBScope, folder: string, groupId?: string) =>
    request<KnowledgeState>("POST", "/knowledge/link", { scope, folder, group_id: groupId }),
  openKnowledgeFolder: () => request("POST", "/knowledge/open-folder"),
  pickFolder: (initial?: string) => request<{ path: string }>("POST", "/system/pick-folder", { initial }),

  mcp: () => request<{ servers: McpServer[]; config_path: string }>("GET", "/mcp"),
  saveMcpServer: (name: string, config: McpServerConfig, oldName?: string) =>
    request<McpServer>("PUT", `/mcp/servers/${enc(name)}`, { config, old_name: oldName }),
  importMcp: (json: string) => request<{ added: string[] }>("POST", "/mcp/import", { json }),
  deleteMcpServer: (name: string) => request("DELETE", `/mcp/servers/${enc(name)}`),
  restartMcpServer: (name: string) => request<McpServer>("POST", `/mcp/servers/${enc(name)}/restart`),
  setMcpEnabled: (name: string, enabled: boolean) =>
    request<McpServer>("POST", `/mcp/servers/${enc(name)}/enabled`, { enabled }),
  setMcpTool: (name: string, tool: string, flags: { enabled?: boolean; auto_approve?: boolean }) =>
    request<McpServer>("PATCH", `/mcp/servers/${enc(name)}/tools/${enc(tool)}`, flags),
};

export interface ChatRequest {
  conversation_id?: string;
  content?: string;
  attachments?: Attachment[];
  model?: string;
  regenerate?: boolean;
  edit_message_id?: string;
  group_id?: string;
}

/** POST /api/chat and call `onEvent` for each Server-Sent Event until the stream ends. */
export async function streamChat(req: ChatRequest, onEvent: (e: StreamEvent) => void, signal?: AbortSignal) {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
  if (res.status === 401) signalSignedOut();
  if (!res.ok || !res.body) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const data = block
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trimStart())
        .join("\n");
      if (data) onEvent(JSON.parse(data) as StreamEvent);
    }
  }
}
