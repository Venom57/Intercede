import { useCallback, useEffect, useState } from "react";
import { api, toast } from "./api";
import type { MemberRow } from "./types";

/** Member roster with admin designation. `base` is the endpoint prefix that
    owns the roster: /api/groups/{gid} for leaders, /api/admin/groups/{gid}
    for the site admin. `meId` disables self-demotion foot-guns visually. */
export function MembersRoster({ base, meId }: { base: string; meId: string }) {
  const [rows, setRows] = useState<MemberRow[] | null>(null);
  const [err, setErr] = useState("");
  const [busyId, setBusyId] = useState("");
  const load = useCallback(() => {
    api<MemberRow[]>(`${base}/members`)
      .then(r => { setRows(r); setErr(""); })
      .catch((e: Error) => setErr(e.message));
  }, [base]);
  useEffect(load, [load]);

  const setRole = async (row: MemberRow, role: string) => {
    setBusyId(row.user_id);
    try {
      await api(`${base}/members/${row.user_id}`, { method: "PATCH", body: JSON.stringify({ role }) });
      toast(role === "leader" ? `${row.display_name} is now an admin` : `${row.display_name} is no longer an admin`);
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not change role");
    } finally {
      setBusyId("");
    }
  };

  const remove = async (row: MemberRow) => {
    if (!window.confirm(`Remove ${row.display_name} from the group? Their prayer entries will be retired.`)) return;
    setBusyId(row.user_id);
    try {
      await api(`${base}/members/${row.user_id}`, { method: "DELETE" });
      toast(`${row.display_name} removed`);
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not remove");
    } finally {
      setBusyId("");
    }
  };

  if (err) return <p className="error">{err}</p>;
  if (!rows) return <p className="hint small">Loading members…</p>;
  if (rows.length === 0) return <p className="hint small">No members yet.</p>;
  return (
    <ul className="queue">
      {rows.map(r => (
        <li key={r.membership_id} className="queue-row">
          <span className="queue-who">
            {r.display_name}{r.user_id === meId && <span className="rel"> (you)</span>}
            <span className="rel"> {r.email}</span>
            {r.status === "pending"
              ? <span className="chip pending">pending</span>
              : r.role !== "member" && <span className={`chip ${r.role === "leader" ? "lead" : ""}`}>{r.role === "leader" ? "admin" : r.role}</span>}
          </span>
          {r.status === "active" && (
            <span className="queue-actions">
              {r.role === "leader"
                ? <button className="mini deny" disabled={busyId === r.user_id} onClick={() => setRole(r, "member")}>Remove admin</button>
                : <button className="mini" disabled={busyId === r.user_id} onClick={() => setRole(r, "leader")}>Make admin</button>}
              {r.user_id !== meId && (
                <button className="mini deny" disabled={busyId === r.user_id} onClick={() => remove(r)}>Remove</button>
              )}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
