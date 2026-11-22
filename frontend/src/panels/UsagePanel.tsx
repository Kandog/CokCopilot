import { useEffect, useMemo, useState } from "react";
import { BarChart3, Download, Loader2, X } from "lucide-react";
import type { UsageSummary } from "../api";
import { api } from "../api";
import { Modal, errorText } from "../components/ui";

const RANGES = [7, 30, 90] as const;
type SortKey = "name" | "requests" | "prompt_tokens" | "completion_tokens" | "total_tokens" | "last_active";

const fmt = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`
    : n >= 10_000 ? `${Math.round(n / 1000)}k`
    : n.toLocaleString();

/** "2026-11-19" as a local date (new Date() would read it as UTC midnight and may show the day before). */
const day = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString();

function when(iso: string) {
  if (!iso) return "—";
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  return days <= 0 ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : days === 1 ? "yesterday" : d.toLocaleDateString();
}

/** Admins: who uses the assistant, how much, and with which models. */
export function UsagePanel(props: { onClose: () => void }) {
  const [days, setDays] = useState<number>(30);
  const [userId, setUserId] = useState<string>("");
  const [data, setData] = useState<UsageSummary | null>(null);
  const [error, setError] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "total_tokens", desc: true });

  useEffect(() => {
    let live = true;
    setError("");
    api.usage(days, userId || undefined)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(errorText(e)));
    return () => { live = false; };
  }, [days, userId]);

  const users = useMemo(() => {
    const list = [...(data?.users ?? [])];
    const { key, desc } = sort;
    list.sort((a, b) => {
      const x = a[key], y = b[key];
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      return desc ? -c : c;
    });
    return list;
  }, [data, sort]);

  const t = data?.totals;
  const picked = data?.users.find((u) => u.user_id === userId);
  const maxDay = Math.max(1, ...(data?.daily.map((d) => d.total_tokens) ?? [1]));
  const maxUser = Math.max(1, ...(data?.users.map((u) => u.total_tokens) ?? [1]));
  const estimated = !!data?.users.some((u) => u.estimated);

  const th = (key: SortKey, label: string) => (
    <th onClick={() => setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== "name" }))}
      aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : undefined}>
      {label}{sort.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
    </th>
  );

  return (
    <Modal title="Usage" icon={<BarChart3 size={20} />} onClose={props.onClose} wide
      subtitle={data ? `${day(data.since)} – ${day(data.until)}` : "Requests and tokens by user"}>
      <div className="row wrap">
        <div className="tabs" role="tablist" aria-label="Time range">
          {RANGES.map((r) => (
            <button key={r} role="tab" aria-selected={days === r} className={`tab${days === r ? " on" : ""}`}
              onClick={() => setDays(r)}>Last {r} days</button>
          ))}
        </div>
        {picked && (
          <span className="badge accent">
            {picked.name}
            <button className="icon-btn sm" style={{ width: 18, height: 18 }} aria-label="Show everyone" title="Show everyone"
              onClick={() => setUserId("")}><X size={12} /></button>
          </span>
        )}
        <span className="spacer" />
        <a className="btn" href={api.usageExportUrl(days)} download><Download size={15} /> Export CSV</a>
      </div>

      {error && <div className="error-text">{error}</div>}
      {!data && !error && <div className="list-empty"><Loader2 size={16} className="spin" /></div>}

      {data && t && (
        <>
          <div className="stat-grid">
            <div className="stat"><span className="v">{fmt(t.requests)}</span><span className="k">replies</span></div>
            <div className="stat"><span className="v">{fmt(t.total_tokens)}</span><span className="k">tokens in total</span></div>
            <div className="stat"><span className="v">{fmt(t.prompt_tokens)}</span><span className="k">input tokens</span></div>
            <div className="stat"><span className="v">{fmt(t.completion_tokens)}</span><span className="k">output tokens</span></div>
            {!userId && <div className="stat"><span className="v">{t.active_users}</span><span className="k">active users</span></div>}
            {userId && <div className="stat"><span className="v">{fmt(t.tool_calls)}</span><span className="k">tool calls</span></div>}
          </div>

          <div className="section">
            <h3>Tokens per day{picked ? ` · ${picked.name}` : ""}</h3>
            <div className="bars" role="img" aria-label="Tokens per day">
              {data.daily.map((d) => (
                <div key={d.date} className={`bar${d.total_tokens ? "" : " zero"}`}
                  style={{ height: `${(d.total_tokens / maxDay) * 100}%` }}
                  title={`${day(d.date)}: ${d.total_tokens.toLocaleString()} tokens, ${d.requests} replies`} />
              ))}
            </div>
            <div className="bars-axis">
              <span>{day(data.since)}</span>
              <span>peak {fmt(maxDay)} tokens/day</span>
              <span>{day(data.until)}</span>
            </div>
          </div>

          <div className="table-wrap">
            <table className="usage-table">
              <thead>
                <tr>
                  {th("name", "User")}{th("requests", "Replies")}{th("prompt_tokens", "Input")}
                  {th("completion_tokens", "Output")}{th("total_tokens", "Total tokens")}<th>Top model</th>{th("last_active", "Last active")}
                </tr>
              </thead>
              <tbody>
                {users.length === 0 && (
                  <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--muted)", cursor: "default" }}>
                    No usage in this period yet.
                  </td></tr>
                )}
                {users.map((u) => (
                  <tr key={u.user_id} className={u.user_id === userId ? "on" : ""} title="Show only this user"
                    onClick={() => setUserId(u.user_id === userId ? "" : u.user_id)}>
                    <td>{u.name}{u.removed && <span className="hint"> (removed)</span>}</td>
                    <td>{u.requests.toLocaleString()}</td>
                    <td>{u.prompt_tokens.toLocaleString()}</td>
                    <td>{u.completion_tokens.toLocaleString()}</td>
                    <td>
                      <span className="meter" style={{ width: `${Math.max(2, (u.total_tokens / maxUser) * 60)}px` }} />
                      {u.total_tokens.toLocaleString()}{u.estimated ? "*" : ""}
                    </td>
                    <td style={{ textAlign: "left" }}>{u.top_model || "—"}</td>
                    <td>{when(u.last_active)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {data.models.length > 0 && (
            <div className="section">
              <h3>By model{picked ? ` · ${picked.name}` : ""}</h3>
              <div className="list" style={{ border: 0 }}>
                {data.models.map((m) => (
                  <div key={m.model} className="list-item" style={{ padding: "6px 0" }}>
                    <div className="grow"><div className="name">{m.model}</div></div>
                    <span className="hint">{m.requests.toLocaleString()} replies</span>
                    <span style={{ width: 110, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                      {m.total_tokens.toLocaleString()} tokens
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <span className="hint">
            Counts replies in personal and group chats, plus background memory updates (tokens only).
            {estimated && " * Some numbers are estimates: the AI provider didn't report token counts, so about 4 characters count as one token."}
          </span>
        </>
      )}
    </Modal>
  );
}
