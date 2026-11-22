import { useState } from "react";
import { Ban, Check, ChevronDown, ChevronRight, CircleAlert, Loader2, ShieldQuestion, Wrench } from "lucide-react";
import type { ToolCall } from "../api";

const STATUS: Record<ToolCall["status"], { label: string; icon: JSX.Element }> = {
  pending: { label: "Preparing", icon: <Loader2 size={14} className="spin" /> },
  awaiting_approval: { label: "Needs approval", icon: <ShieldQuestion size={14} /> },
  running: { label: "Running", icon: <Loader2 size={14} className="spin" /> },
  done: { label: "Done", icon: <Check size={14} color="var(--ok)" /> },
  error: { label: "Failed", icon: <CircleAlert size={14} color="var(--danger)" /> },
  denied: { label: "Declined", icon: <Ban size={14} /> },
};

export function ToolCallCard(props: {
  call: ToolCall;
  onDecide?: (approved: boolean, always: boolean) => void;
  canAlwaysAllow?: boolean;
}) {
  const { call } = props;
  const awaiting = call.status === "awaiting_approval";
  const [open, setOpen] = useState(false);
  const st = STATUS[call.status] ?? STATUS.done;
  const args = JSON.stringify(call.arguments ?? {}, null, 2);
  return (
    <div className={`tool-card${awaiting ? " awaiting" : ""}`}>
      <button className="tool-head" onClick={() => setOpen(!open)} aria-expanded={open || awaiting}>
        <Wrench size={15} color="var(--muted)" />
        <span>
          <span className="t-server">{call.server || "tool"} › </span>
          <span className="t-name">{call.tool}</span>
        </span>
        <span className="tool-status">
          {st.icon} {st.label}
          {open || awaiting ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>
      {(open || awaiting) && (
        <div className="tool-body">
          <div className="label">Arguments</div>
          <pre>{args === "{}" ? "(none)" : args}</pre>
          {call.result && (
            <>
              <div className="label">Result</div>
              <pre>{call.result}</pre>
            </>
          )}
        </div>
      )}
      {awaiting && props.onDecide && (
        <div className="approve-row">
          <span className="hint">
            The assistant wants to run this tool from the <b>{call.server}</b> server. Check the arguments first.
          </span>
          <button className="btn primary sm" onClick={() => props.onDecide!(true, false)}>
            Allow once
          </button>
          {props.canAlwaysAllow && (
            <button className="btn sm" onClick={() => props.onDecide!(true, true)}>
              Always allow this tool
            </button>
          )}
          <button className="btn danger sm" onClick={() => props.onDecide!(false, false)}>
            Deny
          </button>
        </div>
      )}
    </div>
  );
}
