import { useEffect, useState } from "react";
import { Check, Copy, Loader2, ShieldCheck } from "lucide-react";
import type { SsoConfig } from "../api";
import { api } from "../api";
import { Switch, ToggleRow, copyText, errorText, useToast } from "../components/ui";

/** Admin settings for signing in with Microsoft Entra ID (Azure AD). */
export function SsoSection() {
  const [cfg, setCfg] = useState<SsoConfig | null>(null);
  const [draft, setDraft] = useState<Partial<SsoConfig>>({});
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const toast = useToast();

  useEffect(() => { api.ssoConfig().then(setCfg).catch((e) => toast(errorText(e), true)); }, [toast]);
  if (!cfg) return <div className="section"><Loader2 size={16} className="spin" /></div>;

  const v = { ...cfg, ...draft };
  const set = <K extends keyof SsoConfig>(k: K, value: SsoConfig[K]) => setDraft((d) => ({ ...d, [k]: value }));
  const dirty = Object.keys(draft).length > 0;
  const redirect = v.redirect_uri || cfg.default_redirect_uri;

  const save = async (patch: Partial<SsoConfig> = {}) => {
    setBusy(true);
    try {
      const saved = await api.saveSsoConfig({ ...draft, ...patch });
      setCfg(saved);
      setDraft({});
      toast(saved.enabled ? "Single sign-on is on" : "Saved");
    } catch (e) {
      toast(errorText(e), true);
    } finally {
      setBusy(false);
    }
  };

  const check = async () => {
    setBusy(true);
    try {
      await api.checkSsoTenant(v.tenant_id);
      toast("Microsoft knows this tenant, and the server can reach it");
    } catch (e) {
      toast(errorText(e), true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="section">
      <div className="toggle-row">
        <div className="text">
          <b><ShieldCheck size={16} style={{ verticalAlign: -3 }} /> Sign in with Microsoft (single sign-on)</b>
          <span className="hint">
            People sign in with their company Microsoft account. Only users added below can get in: each is matched by
            the email you enter for them.
          </span>
        </div>
        <Switch checked={cfg.enabled} disabled={busy} label="Single sign-on"
          onChange={(on) => {
            if (on || confirm("Turn off single sign-on? Users will need access keys to sign in.")) save({ enabled: on });
          }} />
      </div>

      <div className="field">
        <label htmlFor="sso-redirect">Redirect URI — add this in Azure: App registration → Authentication → Web</label>
        <div className="row">
          <input id="sso-redirect" className="input mono" value={v.redirect_uri} placeholder={cfg.default_redirect_uri}
            onChange={(e) => set("redirect_uri", e.target.value)} />
          <button className="btn" onClick={async () => {
            await copyText(redirect);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}>{copied ? <Check size={15} /> : <Copy size={15} />} Copy</button>
        </div>
        <span className="hint">Leave empty to use the address shown. It must match the app registration exactly.</span>
      </div>
      <div className="row wrap">
        <div className="field" style={{ flex: 1, minWidth: 220 }}>
          <label htmlFor="sso-tenant">Directory (tenant) ID</label>
          <input id="sso-tenant" className="input mono" value={v.tenant_id} placeholder="00000000-0000-0000-0000-000000000000"
            onChange={(e) => set("tenant_id", e.target.value.trim())} />
        </div>
        <div className="field" style={{ flex: 1, minWidth: 220 }}>
          <label htmlFor="sso-client">Application (client) ID</label>
          <input id="sso-client" className="input mono" value={v.client_id} placeholder="00000000-0000-0000-0000-000000000000"
            onChange={(e) => set("client_id", e.target.value.trim())} />
        </div>
      </div>
      <div className="row wrap">
        <div className="field" style={{ flex: 1, minWidth: 220 }}>
          <label htmlFor="sso-secret">Client secret (value)</label>
          <input id="sso-secret" className="input" type="password" autoComplete="off" value={v.client_secret}
            placeholder={cfg.client_secret_set ? "Saved — leave empty to keep it" : "From Certificates & secrets"}
            onChange={(e) => set("client_secret", e.target.value)} />
        </div>
        <div className="field" style={{ flex: 1, minWidth: 220 }}>
          <label htmlFor="sso-label">Button text</label>
          <input id="sso-label" className="input" value={v.button_label} onChange={(e) => set("button_label", e.target.value)} />
        </div>
      </div>
      <ToggleRow title="Users may also sign in with access keys" checked={v.allow_access_keys}
        onChange={(x) => set("allow_access_keys", x)}
        hint="Off: while single sign-on is on, only admins can use access keys (so a wrong Azure setting can't lock you out)." />
      {cfg.problems.length > 0 && cfg.enabled === false && (cfg.tenant_id || cfg.client_id) && (
        <span className="hint">Still needed: {cfg.problems.join("; ")}.</span>
      )}
      <div className="row">
        <button className="btn primary" disabled={busy || !dirty} onClick={() => save()}>Save</button>
        <button className="btn" disabled={busy || !v.tenant_id} onClick={check}>Check tenant</button>
        {dirty && <button className="btn ghost" onClick={() => setDraft({})}>Discard</button>}
      </div>
    </div>
  );
}
