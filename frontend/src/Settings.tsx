import { useEffect, useState } from "react";
import { api, toast } from "./api";
import { MembersRoster } from "./Roster";
import type { AuditEntry, GroupT, Me, QueueEntry } from "./types";

function JoinQueue({ gid }: { gid: string }) {
  const [queue, setQueue] = useState<QueueEntry[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api<QueueEntry[]>(`/api/groups/${gid}/join-queue`).then(setQueue)
      .catch((e: Error) => setErr(e.message));
  }, [gid]);
  const decide = async (mid: string, approve: boolean) => {
    try {
      await api(`/api/join-queue/${mid}`, { method: "PATCH", body: JSON.stringify({ approve }) });
      setQueue(q => (q ?? []).filter(e => e.membership_id !== mid));
      toast(approve ? "Member approved" : "Request denied");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not update");
    }
  };
  if (err) return <p className="error">{err}</p>;
  if (!queue) return <p className="hint small">Checking the door…</p>;
  if (queue.length === 0) return <p className="hint small">No one is waiting to join.</p>;
  return (
    <ul className="queue">
      {queue.map(e => (
        <li key={e.membership_id} className="queue-row">
          <span className="queue-who">{e.display_name} <span className="rel">{e.email}</span></span>
          <span className="queue-actions">
            <button className="mini" onClick={() => decide(e.membership_id, true)}>Approve</button>
            <button className="mini deny" onClick={() => decide(e.membership_id, false)}>Deny</button>
          </span>
        </li>
      ))}
    </ul>
  );
}

function AuditTrail({ gid }: { gid: string }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (open && !entries) api<AuditEntry[]>(`/api/groups/${gid}/audit`).then(setEntries).catch(() => setEntries([]));
  }, [open, entries, gid]);
  return (
    <div>
      <button className="link" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Hide recent activity" : "Show recent activity"}
      </button>
      {open && entries && (
        entries.length === 0 ? <p className="hint small">Nothing logged yet.</p> :
        <ul className="audit">
          {entries.slice(0, 20).map(a => (
            <li key={a.id}>
              <span className="audit-action">{a.action}</span>
              {a.detail && <span className="rel"> · {a.detail}</span>}
              <span className="audit-when">{new Date(a.created_at).toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function LeaderTools({ group, meId, reloadGroups }: { group: GroupT; meId: string; reloadGroups: () => void }) {
  const joinUrl = `${window.location.origin}/join/${group.invite_code}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(joinUrl);
      toast("Join link copied");
    } catch {
      toast("Could not copy — long-press the link instead");
    }
  };
  const rotate = async () => {
    if (!window.confirm("Rotate the invite code? Printed posters with the old QR will stop working.")) return;
    try {
      await api(`/api/groups/${group.id}/invite/rotate`, { method: "POST" });
      reloadGroups();
      toast("Invite code rotated");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not rotate");
    }
  };
  return (
    <section className="settings-section">
      <h3 className="eyebrow">Leader tools</h3>
      <p className="settings-invite"><a href={joinUrl}>{joinUrl}</a></p>
      <div className="settings-row">
        <button className="mini" onClick={copy}>Copy join link</button>
        <a className="mini btn-like" href={`/api/groups/${group.id}/invite/poster.svg`} target="_blank" rel="noreferrer">QR poster</a>
        <button className="mini deny" onClick={rotate}>Rotate code</button>
      </div>
      <h4 className="settings-sub">Waiting to join</h4>
      <JoinQueue gid={group.id} />
      <h4 className="settings-sub">Members &amp; roles</h4>
      <MembersRoster base={`/api/groups/${group.id}`} meId={meId} />
      <h4 className="settings-sub">Audit</h4>
      <AuditTrail gid={group.id} />
    </section>
  );
}

export function SettingsView({ me, groups, gid, onSwitch, onLoggedOut, reloadGroups, onOpenAdmin }: {
  me: Me; groups: GroupT[]; gid: string;
  onSwitch: (gid: string) => void; onLoggedOut: () => void; reloadGroups: () => void;
  onOpenAdmin: () => void;
}) {
  const group = groups.find(g => g.id === gid) ?? groups[0];
  const logout = async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch { /* cookie may already be gone */ }
    onLoggedOut();
  };
  return (
    <div>
      {groups.length > 1 && (
        <section className="settings-section">
          <h3 className="eyebrow">Your groups</h3>
          {groups.map(g => (
            <button key={g.id} className={`picker-card ${g.id === group.id ? "sel" : ""}`} onClick={() => onSwitch(g.id)}>
              {g.name} <span className="rel">{g.role}</span>
            </button>
          ))}
        </section>
      )}
      {group.role === "leader" && group.invite_code && <LeaderTools group={group} meId={me.id} reloadGroups={reloadGroups} />}
      {me.is_site_admin && (
        <section className="settings-section">
          <h3 className="eyebrow">Site administration</h3>
          <button className="mini" onClick={onOpenAdmin}>Open site admin</button>
        </section>
      )}
      <section className="settings-section">
        <h3 className="eyebrow">Account</h3>
        <p className="settings-account">{me.display_name} <span className="rel">{me.email}</span></p>
        <button className="mini deny" onClick={logout}>Sign out</button>
      </section>
    </div>
  );
}
