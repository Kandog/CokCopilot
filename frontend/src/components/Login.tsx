import { useEffect, useState } from "react";
import { KeyRound, Loader2 } from "lucide-react";
import type { AuthOptions, Me } from "../api";
import { api } from "../api";
import { errorText } from "./ui";

/** Microsoft's four-square logo, drawn inline (no external image). */
function MicrosoftLogo() {
  return (
    <svg width="16" height="16" viewBox="0 0 21 21" aria-hidden="true">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

/** Error passed back by the server after a failed Microsoft sign-in (?sso_error=...); removed from the address. */
function takeSsoError(): string {
  const params = new URLSearchParams(window.location.search);
  const msg = params.get("sso_error") ?? "";
  if (msg) {
    params.delete("sso_error");
    const q = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (q ? `?${q}` : "") + window.location.hash);
  }
  return msg;
}

export function Login(props: { onSignedIn: (me: Me) => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(takeSsoError);
  const [opts, setOpts] = useState<AuthOptions | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [redirecting, setRedirecting] = useState(false);

  useEffect(() => {
    api.authOptions().then(setOpts).catch(() => setOpts({ mode: "server", sso: false, sso_label: "", keys: "all" }));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim()) return;
    setBusy(true);
    setErr("");
    try {
      await api.login(key.trim());
      props.onSignedIn(await api.me());
    } catch (x) {
      setErr(errorText(x));
    } finally {
      setBusy(false);
    }
  };

  const sso = !!opts?.sso;
  // With SSO on and keys limited to admins, the key box is tucked away behind a small link.
  const keyBox = opts && (!sso || opts.keys === "all" || showKey);

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="logo lg" />
        <h1>Sign in to CokCopilot</h1>
        {!opts && <Loader2 size={18} className="spin" />}
        {sso && (
          <>
            <p className="muted">Use your work account.</p>
            <button type="button" className="btn sso-btn" disabled={redirecting} style={{ width: "100%" }}
              onClick={() => { setRedirecting(true); window.location.href = "/api/auth/sso/start"; }}>
              {redirecting ? <Loader2 size={15} className="spin" /> : <MicrosoftLogo />}
              {opts.sso_label || "Sign in with Microsoft"}
            </button>
          </>
        )}
        {sso && keyBox && <div className="login-or"><span>or use an access key</span></div>}
        {keyBox && (
          <>
            {!sso && (
              <p className="muted">Enter the access key your admin gave you. It starts with <code>cok_</code>.</p>
            )}
            <label className="search login-input">
              <KeyRound size={16} />
              <input autoFocus={!sso} type="password" autoComplete="current-password" placeholder="cok_…" value={key}
                onChange={(e) => setKey(e.target.value)} aria-label="Access key" />
            </label>
          </>
        )}
        {err && <div className="error-text">{err}</div>}
        {keyBox && (
          <button className={`btn${sso ? "" : " primary"}`} type="submit" disabled={busy || !key.trim()} style={{ width: "100%" }}>
            {busy && <Loader2 size={15} className="spin" />} Sign in with key
          </button>
        )}
        {sso && !keyBox && (
          <button type="button" className="link-btn hint" onClick={() => setShowKey(true)}>Admin? Sign in with an access key</button>
        )}
        <p className="hint">You stay signed in on this device for 30 days.</p>
      </form>
    </div>
  );
}
