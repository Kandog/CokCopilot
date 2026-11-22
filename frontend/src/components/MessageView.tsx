import { useState } from "react";
import { Check, Copy, FileText, Pencil, RefreshCw } from "lucide-react";
import type { Message, ToolCall } from "../api";
import { Markdown } from "./Markdown";
import { ToolCallCard } from "./ToolCallCard";
import { copyText } from "./ui";

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="icon-btn sm"
      title="Copy"
      aria-label="Copy"
      onClick={async () => {
        await copyText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1400);
      }}
    >
      {done ? <Check size={15} /> : <Copy size={15} />}
    </button>
  );
}

function SkillText({ text, skill }: { text: string; skill?: string }) {
  if (skill && text.trimStart().startsWith("/")) {
    const m = /^(\s*\/\S+)([\s\S]*)$/.exec(text);
    if (m) return (<><span className="skill-tag">{m[1]}</span>{m[2]}</>);
  }
  return <>{text}</>;
}

export function UserMessage(props: { message: Message; busy: boolean; onEdit: (text: string) => void }) {
  const m = props.message;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(m.content);
  const names = m.attachments ?? [];
  const imageNames = names.filter((n) => /\.(png|jpe?g|gif|webp|bmp)$/i.test(n));
  const docNames = names.filter((n) => !imageNames.includes(n));
  return (
    <div className="msg user">
      {(m.images.length > 0 || docNames.length > 0 || (m.document_texts.length > 0 && names.length === 0)) && (
        <div className="attach-row">
          {m.images.map((img, i) => (
            <img key={i} className="attach-thumb" alt={imageNames[i] ?? "image"}
              src={img.startsWith("data:") ? img : `data:image/jpeg;base64,${img}`} />
          ))}
          {(docNames.length ? docNames : m.document_texts.map((_, i) => `Document ${i + 1}`)).map((n) => (
            <span key={n} className="attach-chip"><FileText size={14} /> {n}</span>
          ))}
        </div>
      )}
      {editing ? (
        <div className="edit-box">
          <textarea className="textarea" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (draft.trim()) { setEditing(false); props.onEdit(draft); }
              }
              if (e.key === "Escape") setEditing(false);
            }} />
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn sm" onClick={() => setEditing(false)}>Cancel</button>
            <button className="btn primary sm" disabled={!draft.trim() || props.busy}
              onClick={() => { setEditing(false); props.onEdit(draft); }}>Send</button>
          </div>
        </div>
      ) : (
        m.content && <div className="bubble"><SkillText text={m.content} skill={m.skill} /></div>
      )}
      {!editing && (
        <div className="msg-actions">
          <CopyButton text={m.content} />
          {!props.busy && (
            <button className="icon-btn sm" title="Edit and resend" aria-label="Edit"
              onClick={() => { setDraft(m.content); setEditing(true); }}>
              <Pencil size={15} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function AssistantMessage(props: {
  content: string;
  toolCalls?: ToolCall[];
  streaming?: boolean;
  notices?: string[];
  error?: string;
  isLast?: boolean;
  busy?: boolean;
  onRegenerate?: () => void;
  onDecide?: (callId: string, approved: boolean, always: boolean) => void;
  canAlwaysAllow?: boolean;
}) {
  const calls = props.toolCalls ?? [];
  const waitingForModel = props.streaming && !props.content && !calls.some((c) => c.status !== "done");
  return (
    <div className={`msg assistant${props.isLast ? " last" : ""}`}>
      <div className="avatar"><div className="logo" /></div>
      <div className="assistant-body">
        {props.notices?.map((n, i) => <div key={i} className="notice">{n}</div>)}
        {calls.length > 0 && (
          <div className="tools">
            {calls.map((c) => (
              <ToolCallCard key={c.id} call={c} canAlwaysAllow={props.canAlwaysAllow}
                onDecide={props.onDecide ? (a, al) => props.onDecide!(c.id, a, al) : undefined} />
            ))}
          </div>
        )}
        {props.content && <Markdown text={props.content} streaming={props.streaming} />}
        {waitingForModel && <div className="typing" aria-label="Thinking"><i /><i /><i /></div>}
        {props.error && <div className="error-banner">{props.error}</div>}
        {!props.streaming && props.content && (
          <div className="msg-actions">
            <CopyButton text={props.content} />
            {props.isLast && props.onRegenerate && !props.busy && (
              <button className="icon-btn sm" title="Regenerate" aria-label="Regenerate" onClick={props.onRegenerate}>
                <RefreshCw size={15} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
