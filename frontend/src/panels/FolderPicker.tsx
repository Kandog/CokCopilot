import { useEffect, useState } from "react";
import { ChevronRight, CornerLeftUp, Folder, Loader2 } from "lucide-react";
import type { FolderListing } from "../api";
import { api } from "../api";
import { errorText } from "../components/ui";

/**
 * Pick a folder on the server (or a share it can reach), inside the roots the admin allows.
 * The browser can't see the server's disks, so the server lists the folders.
 */
export function FolderPicker(props: { busy: boolean; onPick: (path: string) => void; onCancel: () => void }) {
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const open = async (p: string) => {
    setLoading(true);
    setError("");
    try {
      const l = await api.browseFolders(p);
      setListing(l);
      setPath(l.path);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { open(""); }, []);

  return (
    <div className="section">
      <h3><Folder size={16} /> Choose a shared folder</h3>
      <span className="hint">
        Every document in the folder and all its subfolders is indexed and kept up to date. Add, change or remove files in
        the folder itself; the server checks for changes about once a minute.
      </span>
      <div className="row">
        <input className="input mono" value={path} placeholder="\\fileserver\share\folder" aria-label="Folder path"
          onChange={(e) => setPath(e.target.value)} onKeyDown={(e) => e.key === "Enter" && open(path)} />
        <button className="btn" onClick={() => open(path)} disabled={loading}>Go</button>
      </div>
      {error && <div className="error-text">{error}</div>}
      <div className="list scroll folder-list">
        {loading && <div className="list-empty"><Loader2 size={16} className="spin" /></div>}
        {!loading && listing && listing.parent !== null && (
          <button className="list-item nav-item" onClick={() => open(listing.parent ?? "")}>
            <CornerLeftUp size={16} /> <span className="grow">Up</span>
          </button>
        )}
        {!loading && listing?.dirs.map((d) => (
          <button key={d.path} className="list-item nav-item" onClick={() => open(d.path)} title={d.path}>
            <Folder size={16} color="var(--muted)" />
            <span className="grow name" style={{ textAlign: "left" }}>{d.name}</span>
            <ChevronRight size={15} color="var(--muted)" />
          </button>
        ))}
        {!loading && listing && listing.dirs.length === 0 && (
          <div className="list-empty">{listing.path ? "No subfolders." : "Your admin hasn't allowed any shared folders yet."}</div>
        )}
      </div>
      <div className="row">
        <button className="btn primary" disabled={props.busy || !listing?.path || path !== listing.path}
          onClick={() => props.onPick(listing!.path)}>
          Use this folder
        </button>
        <button className="btn ghost" onClick={props.onCancel}>Cancel</button>
        {listing?.path && <span className="hint" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{listing.path}</span>}
      </div>
    </div>
  );
}
