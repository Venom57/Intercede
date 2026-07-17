import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { AdminView } from "./Admin";
import { api, flushPrayQueue, recordPrayed, toast, TOAST_EVENT, UNAUTHED_EVENT } from "./api";
import { AuthView, JoinWizard } from "./Onboard";
import { SettingsView } from "./Settings";
import { CATEGORIES, type GroupT, type Me, type Req, type UpdateT, type Wall } from "./types";

/* -------------------------------- routing -------------------------------- */

type Tab = "wall" | "pray" | "praise" | "add" | "settings" | "admin";
const TABS: Tab[] = ["wall", "pray", "praise", "add", "settings", "admin"];

function readTab(): Tab {
  const t = window.location.hash.replace(/^#\//, "");
  return (TABS as string[]).includes(t) ? (t as Tab) : "wall";
}

function useTab(): [Tab, (t: Tab) => void] {
  const [tab, setTab] = useState<Tab>(readTab);
  useEffect(() => {
    const on = () => setTab(readTab());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [tab, (t: Tab) => { window.location.hash = `/${t}`; }];
}

/* ------------------------- wall cache (per group) ------------------------- */

const wallCache = new Map<string, Wall>();

/** Reflect an optimistic prayed toggle into every cached wall (immutably). */
function patchCachedPrayed(reqId: string, delta: 1 | -1) {
  const patch = (r: Req): Req =>
    r.id === reqId ? { ...r, prayed_today: delta > 0, prayer_count: r.prayer_count + delta } : r;
  for (const [g, w] of wallCache) {
    wallCache.set(g, {
      urgent: w.urgent.map(patch),
      families: w.families.map(f => ({
        ...f,
        requests: f.requests.map(patch),
        members: f.members.map(m => ({ ...m, requests: m.requests.map(patch) })),
      })),
    });
  }
}

function useWall(gid: string) {
  const [wall, setWall] = useState<Wall | null>(() => wallCache.get(gid) ?? null);
  const [err, setErr] = useState("");
  const reload = useCallback(() => {
    api<Wall>(`/api/groups/${gid}/wall`)
      .then(w => { wallCache.set(gid, w); setWall(w); setErr(""); })
      .catch((e: Error) => setErr(e.message));
  }, [gid]);
  useEffect(reload, [reload]); // cached copy shows instantly; this revalidates
  return { wall, err, reload };
}

/* ----------------------------- shared widgets ----------------------------- */

function SkeletonCards({ n = 3 }: { n?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="card skeleton">
          <div className="sk-line w60" /><div className="sk-line w90" /><div className="sk-line w40" />
        </div>
      ))}
    </div>
  );
}

function ErrorRetry({ msg, onRetry }: { msg: string; onRetry: () => void }) {
  return (
    <div className="load-error" role="alert">
      <p className="error">{msg}</p>
      <button className="mini" onClick={onRetry}>Try again</button>
    </div>
  );
}

function Toaster() {
  const [msg, setMsg] = useState("");
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const on = (e: Event) => {
      setMsg((e as CustomEvent<string>).detail);
      clearTimeout(timer);
      timer = setTimeout(() => setMsg(""), 3200);
    };
    window.addEventListener(TOAST_EVENT, on);
    return () => { window.removeEventListener(TOAST_EVENT, on); clearTimeout(timer); };
  }, []);
  return <div className={`toast ${msg ? "show" : ""}`} role="status" aria-live="polite">{msg}</div>;
}

/* ------------------------------ request card ------------------------------ */

function UpdatesThread({ req, canPost }: { req: Req; canPost: boolean }) {
  const [updates, setUpdates] = useState<UpdateT[] | null>(null);
  const [draft, setDraft] = useState("");
  useEffect(() => {
    api<UpdateT[]>(`/api/requests/${req.id}/updates`).then(setUpdates).catch(() => setUpdates([]));
  }, [req.id]);
  const post = async () => {
    try {
      await api(`/api/requests/${req.id}/updates`, { method: "POST", body: JSON.stringify({ body: draft }) });
      setUpdates(null); // refetch
      setDraft("");
      const fresh = await api<UpdateT[]>(`/api/requests/${req.id}/updates`);
      setUpdates(fresh);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not post update");
    }
  };
  if (!updates) return <p className="hint small">Loading updates…</p>;
  return (
    <div className="updates">
      {updates.length === 0 && <p className="hint small">No updates yet.</p>}
      {updates.map(u => (
        <p key={u.id} className="update">
          {u.body}
          <span className="update-meta">{u.author} · {new Date(u.created_at).toLocaleDateString()}</span>
        </p>
      ))}
      {canPost && (
        <form className="update-form" onSubmit={e => { e.preventDefault(); post(); }}>
          <textarea value={draft} onChange={e => setDraft(e.target.value)} maxLength={4000} rows={1}
                    placeholder="Share an update…" aria-label="New update" />
          <button className="mini" disabled={!draft.trim()} type="submit">Post</button>
        </form>
      )}
    </div>
  );
}

function AnswerForm({ req, onDone }: { req: Req; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api(`/api/requests/${req.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "answered", ...(note.trim() ? { answer_note: note.trim() } : {}) }),
      });
      toast("Answered — moved to the praise wall");
      onDone();
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not update");
      setBusy(false);
    }
  };
  return (
    <form className="card-subform" onSubmit={submit}>
      <textarea value={note} onChange={e => setNote(e.target.value)} rows={2} maxLength={4000}
                placeholder="How was it answered? (optional)" aria-label="Answer note" />
      <button className="mini gold" disabled={busy} type="submit">Move to praise wall</button>
    </form>
  );
}

function EditForm({ req, onSaved, onCancel }: { req: Req; onSaved: () => void; onCancel: () => void }) {
  const [title, setTitle] = useState(req.title);
  const [body, setBody] = useState(req.body);
  const [urgent, setUrgent] = useState(req.is_urgent);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api(`/api/requests/${req.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: title.trim(), body, is_urgent: urgent }),
      });
      toast("Request updated");
      onSaved();
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not save");
      setBusy(false);
    }
  };
  return (
    <form className="card-subform" onSubmit={submit}>
      <input value={title} onChange={e => setTitle(e.target.value)} maxLength={120} aria-label="Title" />
      <textarea value={body} onChange={e => setBody(e.target.value)} rows={3} maxLength={4000}
                placeholder="Details (optional)" aria-label="Details" />
      <label className="row">
        <input type="checkbox" checked={urgent} onChange={e => setUrgent(e.target.checked)} /> Urgent
      </label>
      <div className="settings-row">
        <button className="mini" disabled={busy || !title.trim()} type="submit">Save</button>
        <button className="mini deny" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function RequestCard({ req, meId, role, onRemoved }: {
  req: Req; meId: string; role: string; onRemoved: () => void;
}) {
  const [prayed, setPrayed] = useState(req.prayed_today);
  const [count, setCount] = useState(req.prayer_count);
  const [showUpdates, setShowUpdates] = useState(false);
  const [pane, setPane] = useState<"none" | "edit" | "answer">("none");
  const canEdit = req.created_by === meId || role === "leader" || role === "steward";

  const pray = async () => {
    if (prayed) return;
    setPrayed(true);
    setCount(c => c + 1);
    patchCachedPrayed(req.id, 1); // keep cached walls consistent across tab switches
    try {
      const result = await recordPrayed(req.id);
      if (result === "queued") toast("Offline — your prayer will be recorded when you reconnect");
    } catch (e) {
      setPrayed(false);
      setCount(c => c - 1);
      patchCachedPrayed(req.id, -1);
      toast(e instanceof Error ? e.message : "Could not record — try again");
    }
  };

  const remove = async () => {
    if (!window.confirm(`Remove "${req.title}" from the wall?`)) return;
    try {
      await api(`/api/requests/${req.id}`, { method: "DELETE" });
      toast("Request removed");
      onRemoved();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not remove");
    }
  };

  const privacyLabel = req.privacy === "family_only" ? "family only"
    : req.privacy === "leaders_only" ? "leaders only" : null;

  return (
    <article className={`card ${req.is_urgent ? "urgent" : ""}`}>
      <div className="card-head">
        <h4>{req.title}</h4>
        {privacyLabel && <span className="chip">{privacyLabel}</span>}
        {req.is_urgent && <span className="flame" aria-label="urgent">urgent</span>}
      </div>
      {req.body && <p className="card-body">{req.body}</p>}
      {req.verse && (
        <blockquote className="verse">
          <p>{req.verse.text}</p>
          <cite>{req.verse.reference} ({req.verse.translation})</cite>
        </blockquote>
      )}
      <div className="card-foot">
        <button className={`pray-btn ${prayed ? "did" : ""}`} onClick={pray}>
          {prayed ? "Prayed today" : "I prayed"}
        </button>
        <span className="count" aria-live="polite">{count} prayers</span>
        <span className="card-tools">
          <button className="link small" aria-expanded={showUpdates} onClick={() => setShowUpdates(!showUpdates)}>
            Updates
          </button>
          {canEdit && (
            <button className="link small" aria-expanded={pane === "edit"}
              onClick={() => setPane(pane === "edit" ? "none" : "edit")}>Edit</button>
          )}
          {canEdit && (
            <button className="link small gold" aria-expanded={pane === "answer"}
              onClick={() => setPane(pane === "answer" ? "none" : "answer")}>Answered</button>
          )}
        </span>
      </div>
      {pane === "edit" && (
        <>
          <EditForm req={req} onSaved={onRemoved} onCancel={() => setPane("none")} />
          <button className="link small deny" onClick={remove}>Remove from the wall</button>
        </>
      )}
      {pane === "answer" && <AnswerForm req={req} onDone={onRemoved} />}
      {showUpdates && <UpdatesThread req={req} canPost={canEdit} />}
    </article>
  );
}

/* ---------------------------------- wall ---------------------------------- */

function WallView({ gid, meId, role }: { gid: string; meId: string; role: string }) {
  const { wall, err, reload } = useWall(gid);
  if (err && !wall) return <ErrorRetry msg={err} onRetry={reload} />;
  if (!wall) return <SkeletonCards />;
  const empty = wall.families.every(f => f.requests.length === 0 && f.members.every(m => m.requests.length === 0));
  const card = (r: Req) => <RequestCard key={r.id} req={r} meId={meId} role={role} onRemoved={reload} />;
  return (
    <div>
      {wall.urgent.length > 0 && (
        <section className="urgent-strip">
          <h3 className="eyebrow">Urgent</h3>
          {wall.urgent.map(card)}
        </section>
      )}
      {empty && <p className="hint">No active requests yet. Add the first one from the + tab.</p>}
      {wall.families.map(f => {
        const total = f.requests.length + f.members.reduce((n, m) => n + m.requests.length, 0);
        if (total === 0) return null;
        return (
          <section key={f.id} className="family">
            <h3 className="family-name">{f.family_name}</h3>
            {f.requests.filter(r => !r.is_urgent).map(card)}
            {f.members.map(m => m.requests.length > 0 && (
              <div key={m.id} className="member-block">
                <h4 className="member-name">
                  {m.display_name}
                  {m.relationship_label && <span className="rel"> · {m.relationship_label}</span>}
                </h4>
                {m.requests.filter(r => !r.is_urgent).map(card)}
              </div>
            ))}
          </section>
        );
      })}
    </div>
  );
}

/* ------------------------------ prayer session ---------------------------- */

function SessionView({ gid }: { gid: string }) {
  const { wall, err, reload } = useWall(gid);
  const [i, setI] = useState(0);
  const queue = useMemo(() => {
    if (!wall) return null;
    const all = [
      ...wall.urgent,
      ...wall.families.flatMap(f => [...f.requests, ...f.members.flatMap(m => m.requests)]),
    ];
    const seen = new Set<string>();
    const uniq = all.filter(r => !seen.has(r.id) && seen.add(r.id));
    // requests not yet prayed for today come first
    return [...uniq.filter(r => !r.prayed_today), ...uniq.filter(r => r.prayed_today)];
  }, [wall]);
  if (err && !wall) return <ErrorRetry msg={err} onRetry={reload} />;
  if (!queue) return <SkeletonCards n={1} />;
  const cur = queue[i];
  if (!cur) return <p className="hint session-done">You have prayed through every request. Amen.</p>;
  const advance = () => {
    if (!cur.prayed_today) patchCachedPrayed(cur.id, 1); // keep the Wall tab in sync
    recordPrayed(cur.id).catch(() => { /* best effort inside the flow */ });
    setI(i + 1);
  };
  return (
    <div className="session">
      <p className="session-progress" aria-live="polite">{i + 1} of {queue.length}</p>
      <h2 className="session-title">{cur.title}</h2>
      {cur.body && <p className="session-body">{cur.body}</p>}
      {cur.verse && (
        <blockquote className="verse session-verse">
          <p>{cur.verse.text}</p>
          <cite>{cur.verse.reference} ({cur.verse.translation})</cite>
        </blockquote>
      )}
      <button className="primary session-next" onClick={advance}>Amen — next</button>
    </div>
  );
}

/* -------------------------------- praise wall ----------------------------- */

function PraiseView({ gid }: { gid: string }) {
  const [items, setItems] = useState<{ id: string; title: string; answer_note: string | null; answered_at: string | null }[] | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    api<typeof items>(`/api/groups/${gid}/praise-wall`)
      .then(items => { setItems(items); setErr(""); })
      .catch((e: Error) => setErr(e.message));
  }, [gid]);
  useEffect(load, [load]);
  if (err && !items) return <ErrorRetry msg={err} onRetry={load} />;
  if (!items) return <SkeletonCards />;
  if (items.length === 0) return <p className="hint">Answered prayers will glow here.</p>;
  return (
    <div>
      {items.map(p => (
        <article key={p.id} className="card answered">
          <h4>{p.title}</h4>
          {p.answer_note && <p className="card-body">{p.answer_note}</p>}
          {p.answered_at && <p className="count">{new Date(p.answered_at).toLocaleDateString()}</p>}
        </article>
      ))}
    </div>
  );
}

/* -------------------------------- new request ----------------------------- */

function NewRequestView({ gid, onDone }: { gid: string; onDone: () => void }) {
  const { wall, err: loadErr, reload } = useWall(gid);
  const [subject, setSubject] = useState<{ type: "family" | "member"; id: string; label: string } | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [cat, setCat] = useState("general");
  const [urgent, setUrgent] = useState(false);
  const [privacy, setPrivacy] = useState("group");
  const [err, setErr] = useState("");
  if (loadErr && !wall) return <ErrorRetry msg={loadErr} onRetry={reload} />;
  if (!wall) return <SkeletonCards />;
  if (!subject) {
    return (
      <div>
        <h3 className="eyebrow">Who is this request for?</h3>
        {wall.families.map(f => (
          <div key={f.id} className="picker-family">
            <button className="picker-card" onClick={() => setSubject({ type: "family", id: f.id, label: f.family_name })}>
              {f.family_name} <span className="rel">whole family</span>
            </button>
            {f.members.map(m => (
              <button key={m.id} className="picker-card member"
                onClick={() => setSubject({ type: "member", id: m.id, label: m.display_name })}>
                {m.display_name}{m.relationship_label && <span className="rel"> · {m.relationship_label}</span>}
              </button>
            ))}
          </div>
        ))}
        {wall.families.length === 0 && <p className="hint">No families yet — ask your leader to add one, or create yours from the join link.</p>}
      </div>
    );
  }
  const submit = async () => {
    setErr("");
    try {
      await api(`/api/groups/${gid}/requests`, {
        method: "POST",
        body: JSON.stringify({
          subject_type: subject.type, subject_id: subject.id,
          title, body, category_slug: cat, is_urgent: urgent, privacy,
        }),
      });
      toast("Request added to the wall");
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong");
    }
  };
  return (
    <form className="form" onSubmit={e => { e.preventDefault(); submit(); }}>
      <p className="hint">For <strong>{subject.label}</strong> · <button className="link" type="button" onClick={() => setSubject(null)}>change</button></p>
      <label>Title
        <input value={title} onChange={e => setTitle(e.target.value)} maxLength={120} placeholder="What should we pray for?" />
      </label>
      <label>Details (optional)
        <textarea value={body} onChange={e => setBody(e.target.value)} rows={3} />
      </label>
      <label>Category
        <select value={cat} onChange={e => setCat(e.target.value)}>
          {CATEGORIES.map(([slug, name]) => <option key={slug} value={slug}>{name}</option>)}
        </select>
      </label>
      <label>Who can see this
        <select value={privacy} onChange={e => setPrivacy(e.target.value)}>
          <option value="group">Whole group</option>
          <option value="family_only">Our family only</option>
          <option value="leaders_only">Leaders only</option>
        </select>
      </label>
      <label className="row">
        <input type="checkbox" checked={urgent} onChange={e => setUrgent(e.target.checked)} /> Urgent
      </label>
      {err && <p className="error" role="alert">{err}</p>}
      <button className="primary" disabled={!title.trim()} type="submit">Add request</button>
    </form>
  );
}

/* ---------------------------------- shell --------------------------------- */

const GID_KEY = "intercede.gid";

export default function App() {
  const joinCode = useMemo(() => {
    const match = window.location.pathname.match(/^\/join\/([A-Za-z0-9]+)/);
    return match ? match[1] : null;
  }, []);
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined = checking
  const [groups, setGroups] = useState<GroupT[] | null>(null);
  const [gid, setGid] = useState<string | null>(() => localStorage.getItem(GID_KEY));
  const [tab, setTab] = useTab();
  const [newGroupName, setNewGroupName] = useState("");

  const loadGroups = useCallback(() => {
    api<GroupT[]>("/api/groups").then(gs => {
      setGroups(gs);
      const active = gs.filter(g => g.status === "active");
      setGid(g => (g && active.some(x => x.id === g)) ? g : (active[0]?.id ?? null));
    }).catch(() => setGroups([]));
  }, []);

  const onAuthed = useCallback(() => {
    api<Me>("/api/me").then(setMe).catch(() => setMe(null));
    loadGroups();
    flushPrayQueue().then(n => { if (n > 0) toast(`${n} queued prayer${n === 1 ? "" : "s"} delivered`); });
  }, [loadGroups]);

  useEffect(() => {
    api<Me>("/api/me").then(u => { setMe(u); loadGroups(); }).catch(() => setMe(null));
    const onUnauthed = () => { setMe(null); setGroups(null); wallCache.clear(); };
    window.addEventListener(UNAUTHED_EVENT, onUnauthed);
    const onOnline = () => {
      flushPrayQueue().then(n => { if (n > 0) toast(`${n} queued prayer${n === 1 ? "" : "s"} delivered`); });
    };
    window.addEventListener("online", onOnline);
    return () => {
      window.removeEventListener(UNAUTHED_EVENT, onUnauthed);
      window.removeEventListener("online", onOnline);
    };
  }, [loadGroups]);

  useEffect(() => { if (gid) localStorage.setItem(GID_KEY, gid); }, [gid]);

  const signOut = async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch { /* cookie may already be gone */ }
    setMe(null); setGroups(null); wallCache.clear();
  };

  if (joinCode) return <><JoinWizard code={joinCode} /><Toaster /></>;
  if (me === undefined) return <SkeletonCards />;
  if (!me) return <><AuthView onAuthed={onAuthed} /><Toaster /></>;
  if (!groups) return <SkeletonCards />;
  const activeGroups = groups.filter(g => g.status === "active");
  const pendingGroups = groups.filter(g => g.status === "pending");
  if (activeGroups.length === 0 && pendingGroups.length > 0) {
    return (
      <div className="join">
        <h1 className="join-title brand">Intercede</h1>
        <p className="join-lede">
          Your request to join <strong>{pendingGroups[0].name}</strong> is waiting for the
          leader's approval. You'll be let in as soon as they see it.
        </p>
        <button className="primary" onClick={loadGroups}>Check again</button>
        <button className="link" onClick={signOut}>Sign out</button>
        <Toaster />
      </div>
    );
  }
  if (activeGroups.length === 0) {
    return (
      <div className="join">
        <h1 className="join-title brand">Intercede</h1>
        <p className="join-lede">You are not in a group yet. Scan your group's QR poster to join, or start a new group.</p>
        <form className="form" onSubmit={e => {
          e.preventDefault();
          api("/api/groups", { method: "POST", body: JSON.stringify({ name: newGroupName }) }).then(loadGroups);
        }}>
          <label>Group name<input value={newGroupName} onChange={e => setNewGroupName(e.target.value)} placeholder="Tuesday Night Study" /></label>
          <button className="primary" disabled={!newGroupName.trim()} type="submit">Create group</button>
        </form>
        <button className="link" onClick={signOut}>Sign out</button>
        <Toaster />
      </div>
    );
  }
  const group = activeGroups.find(g => g.id === gid) ?? activeGroups[0];
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Intercede</span>
        <span className="group-name">{group.name}</span>
        <button className="gear" aria-label="Settings"
          aria-current={tab === "settings" || tab === "admin" || undefined}
          onClick={() => setTab("settings")}>⚙</button>
      </header>
      <main className="content">
        {tab === "wall" && <WallView key={`w${group.id}`} gid={group.id} meId={me.id} role={group.role} />}
        {tab === "pray" && <SessionView key={`s${group.id}`} gid={group.id} />}
        {tab === "praise" && <PraiseView key={`p${group.id}`} gid={group.id} />}
        {tab === "add" && <NewRequestView key={`a${group.id}`} gid={group.id} onDone={() => setTab("wall")} />}
        {tab === "settings" && (
          <SettingsView me={me} groups={activeGroups} gid={group.id}
            onSwitch={id => { setGid(id); setTab("wall"); }}
            onLoggedOut={() => { setMe(null); setGroups(null); wallCache.clear(); }}
            reloadGroups={loadGroups}
            onOpenAdmin={() => setTab("admin")} />
        )}
        {tab === "admin" && (me.is_site_admin
          ? <AdminView me={me} />
          : <p className="hint">Site admin access is required.</p>)}
      </main>
      <nav className="tabs" aria-label="Main">
        {([["wall", "Wall"], ["pray", "Pray"], ["praise", "Praise"], ["add", "+ Add"]] as const).map(([t, label]) => (
          <button key={t} className={tab === t ? "on" : ""} aria-current={tab === t ? "page" : undefined}
            onClick={() => setTab(t)}>{label}</button>
        ))}
      </nav>
      <Toaster />
    </div>
  );
}
