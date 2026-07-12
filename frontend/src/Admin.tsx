import { useCallback, useEffect, useState } from "react";
import { api, toast } from "./api";
import { MembersRoster } from "./Roster";
import type { AdminGroup, AdminUser, Me, Overview } from "./types";

function OverviewTiles() {
  const [stats, setStats] = useState<Overview | null>(null);
  useEffect(() => { api<Overview>("/api/admin/overview").then(setStats).catch(() => {}); }, []);
  if (!stats) return null;
  const tiles: [string, number][] = [
    ["Members", stats.users], ["Classes", stats.groups], ["Families", stats.families],
    ["Requests", stats.requests], ["Prayers", stats.prayers],
  ];
  return (
    <div className="stat-row">
      {tiles.map(([label, n]) => (
        <div key={label} className="stat-tile">
          <span className="stat-n">{n}</span>
          <span className="stat-label">{label}</span>
        </div>
      ))}
    </div>
  );
}

function GroupsAdmin({ meId }: { meId: string }) {
  const [groups, setGroups] = useState<AdminGroup[] | null>(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState("");
  const load = useCallback(() => {
    api<AdminGroup[]>("/api/admin/groups")
      .then(g => { setGroups(g); setErr(""); })
      .catch((e: Error) => setErr(e.message));
  }, []);
  useEffect(load, [load]);
  if (err) return <p className="error">{err}</p>;
  if (!groups) return <p className="hint small">Loading classes…</p>;
  if (groups.length === 0) return <p className="hint small">No classes yet.</p>;
  return (
    <div>
      {groups.map(g => (
        <div key={g.id} className="admin-group">
          <button className="picker-card" aria-expanded={open === g.id}
            onClick={() => setOpen(open === g.id ? "" : g.id)}>
            {g.name}
            <span className="rel"> · {g.member_count} member{g.member_count === 1 ? "" : "s"}</span>
            <span className="admin-leaders">
              {g.leaders.length > 0 ? `Admin: ${g.leaders.join(", ")}` : "No admin designated"}
            </span>
          </button>
          {open === g.id && (
            <div className="admin-roster">
              <MembersRoster base={`/api/admin/groups/${g.id}`} meId={meId} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function UsersAdmin({ meId }: { meId: string }) {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [err, setErr] = useState("");
  const [busyId, setBusyId] = useState("");
  const load = useCallback(() => {
    api<AdminUser[]>("/api/admin/users")
      .then(u => { setUsers(u); setErr(""); })
      .catch((e: Error) => setErr(e.message));
  }, []);
  useEffect(load, [load]);

  const setSiteAdmin = async (u: AdminUser, grant: boolean) => {
    if (!grant && !window.confirm(`Remove site admin from ${u.display_name}?`)) return;
    setBusyId(u.id);
    try {
      await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ is_site_admin: grant }) });
      toast(grant ? `${u.display_name} is now a site admin` : `${u.display_name} is no longer a site admin`);
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not update");
    } finally {
      setBusyId("");
    }
  };

  if (err) return <p className="error">{err}</p>;
  if (!users) return <p className="hint small">Loading members…</p>;
  return (
    <ul className="queue">
      {users.map(u => (
        <li key={u.id} className="queue-row">
          <span className="queue-who">
            {u.display_name}{u.id === meId && <span className="rel"> (you)</span>}
            <span className="rel"> {u.email}</span>
            {u.is_site_admin && <span className="chip lead">site admin</span>}
          </span>
          <span className="queue-actions">
            {u.is_site_admin
              ? <button className="mini deny" disabled={busyId === u.id} onClick={() => setSiteAdmin(u, false)}>Revoke</button>
              : <button className="mini" disabled={busyId === u.id} onClick={() => setSiteAdmin(u, true)}>Make site admin</button>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function AdminView({ me }: { me: Me }) {
  return (
    <div>
      <section className="settings-section">
        <h3 className="eyebrow">Site overview</h3>
        <OverviewTiles />
      </section>
      <section className="settings-section">
        <h3 className="eyebrow">Classes</h3>
        <p className="hint small">Tap a class to designate its admin.</p>
        <GroupsAdmin meId={me.id} />
      </section>
      <section className="settings-section">
        <h3 className="eyebrow">All members</h3>
        <UsersAdmin meId={me.id} />
      </section>
    </div>
  );
}
