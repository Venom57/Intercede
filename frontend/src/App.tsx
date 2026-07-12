import { useCallback, useEffect, useMemo, useState } from "react";

/* ---------------------------------- api ---------------------------------- */

async function api(path: string, opts: RequestInit = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}

/* --------------------------------- types --------------------------------- */

type Req = {
  id: string; title: string; body: string; category: string; status: string;
  privacy: string; is_urgent: boolean; prayer_count: number;
  subject_type: string; subject_id: string;
};
type MemberT = { id: string; display_name: string; relationship_label: string | null; requests: Req[] };
type FamilyT = { id: string; family_name: string; requests: Req[]; members: MemberT[] };
type Wall = { urgent: Req[]; families: FamilyT[] };
type GroupT = { id: string; name: string; role: string; invite_code: string | null };

const CATEGORIES: [string, string][] = [
  ["healing", "Healing"], ["grief", "Grief"], ["anxiety", "Anxiety"],
  ["provision", "Provision"], ["salvation", "Salvation"], ["family", "Family"],
  ["guidance", "Guidance"], ["travel", "Travel"], ["strength", "Strength"],
  ["thanksgiving", "Thanksgiving"], ["general", "General"],
];

/* ------------------------------ request card ----------------------------- */

function RequestCard({ req, onPrayed }: { req: Req; onPrayed: (id: string) => void }) {
  const [verse, setVerse] = useState<{ reference: string; text: string } | null>(null);
  const [prayed, setPrayed] = useState(false);
  useEffect(() => { api(`/api/requests/${req.id}/verse`).then(setVerse).catch(() => {}); }, [req.id]);
  return (
    <article className={`card ${req.is_urgent ? "urgent" : ""}`}>
      <div className="card-head">
        <h4>{req.title}</h4>
        {req.is_urgent && <span className="flame" aria-label="urgent">urgent</span>}
      </div>
      {req.body && <p className="card-body">{req.body}</p>}
      {verse && (
        <blockquote className="verse">
          <p>{verse.text}</p>
          <cite>{verse.reference} (KJV)</cite>
        </blockquote>
      )}
      <div className="card-foot">
        <button
          className={`pray-btn ${prayed ? "did" : ""}`}
          onClick={() => { setPrayed(true); onPrayed(req.id); }}
        >
          {prayed ? "Prayed today" : "I prayed"}
        </button>
        <span className="count">{req.prayer_count + (prayed ? 1 : 0)} prayers</span>
      </div>
    </article>
  );
}

/* ---------------------------------- wall --------------------------------- */

function WallView({ groupId }: { groupId: string }) {
  const [wall, setWall] = useState<Wall | null>(null);
  const load = useCallback(() => { api(`/api/groups/${groupId}/wall`).then(setWall); }, [groupId]);
  useEffect(load, [load]);
  const prayed = (id: string) => api(`/api/requests/${id}/prayed`, { method: "POST" }).catch(() => {});
  if (!wall) return <p className="hint">Lighting the candles…</p>;
  const empty = wall.families.every(f => f.requests.length === 0 && f.members.every(m => m.requests.length === 0));
  return (
    <div>
      {wall.urgent.length > 0 && (
        <section className="urgent-strip">
          <h3 className="eyebrow">Urgent</h3>
          {wall.urgent.map(r => <RequestCard key={r.id} req={r} onPrayed={prayed} />)}
        </section>
      )}
      {empty && <p className="hint">No active requests yet. Add the first one from the + tab.</p>}
      {wall.families.map(f => {
        const total = f.requests.length + f.members.reduce((n, m) => n + m.requests.length, 0);
        if (total === 0) return null;
        return (
          <section key={f.id} className="family">
            <h3 className="family-name">{f.family_name}</h3>
            {f.requests.filter(r => !r.is_urgent).map(r => <RequestCard key={r.id} req={r} onPrayed={prayed} />)}
            {f.members.map(m => m.requests.length > 0 && (
              <div key={m.id} className="member-block">
                <h4 className="member-name">
                  {m.display_name}
                  {m.relationship_label && <span className="rel"> · {m.relationship_label}</span>}
                </h4>
                {m.requests.filter(r => !r.is_urgent).map(r => <RequestCard key={r.id} req={r} onPrayed={prayed} />)}
              </div>
            ))}
          </section>
        );
      })}
    </div>
  );
}

/* ----------------------------- prayer session ---------------------------- */

function SessionView({ groupId }: { groupId: string }) {
  const [queue, setQueue] = useState<Req[] | null>(null);
  const [i, setI] = useState(0);
  const [verse, setVerse] = useState<{ reference: string; text: string } | null>(null);
  useEffect(() => {
    api(`/api/groups/${groupId}/wall`).then((w: Wall) => {
      const all = [
        ...w.urgent,
        ...w.families.flatMap(f => [...f.requests, ...f.members.flatMap(m => m.requests)]),
      ];
      const seen = new Set<string>();
      setQueue(all.filter(r => !seen.has(r.id) && seen.add(r.id)));
    });
  }, [groupId]);
  const cur = queue?.[i];
  useEffect(() => {
    if (cur) api(`/api/requests/${cur.id}/verse`).then(setVerse).catch(() => setVerse(null));
  }, [cur?.id]);
  if (!queue) return <p className="hint">Gathering requests…</p>;
  if (!cur) return <p className="hint session-done">You have prayed through every request. Amen.</p>;
  const advance = () => {
    api(`/api/requests/${cur.id}/prayed`, { method: "POST" }).catch(() => {});
    setVerse(null); setI(i + 1);
  };
  return (
    <div className="session">
      <p className="session-progress">{i + 1} of {queue.length}</p>
      <h2 className="session-title">{cur.title}</h2>
      {cur.body && <p className="session-body">{cur.body}</p>}
      {verse && (
        <blockquote className="verse session-verse">
          <p>{verse.text}</p>
          <cite>{verse.reference} (KJV)</cite>
        </blockquote>
      )}
      <button className="primary session-next" onClick={advance}>Amen — next</button>
    </div>
  );
}

/* ------------------------------- praise wall ------------------------------ */

function PraiseView({ groupId }: { groupId: string }) {
  const [items, setItems] = useState<{ id: string; title: string; answer_note: string | null; answered_at: string | null }[]>([]);
  useEffect(() => { api(`/api/groups/${groupId}/praise-wall`).then(setItems); }, [groupId]);
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

/* ------------------------------- new request ------------------------------ */

function NewRequestView({ groupId, onDone }: { groupId: string; onDone: () => void }) {
  const [wall, setWall] = useState<Wall | null>(null);
  const [subject, setSubject] = useState<{ type: "family" | "member"; id: string; label: string } | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [cat, setCat] = useState("general");
  const [urgent, setUrgent] = useState(false);
  const [privacy, setPrivacy] = useState("group");
  const [err, setErr] = useState("");
  useEffect(() => { api(`/api/groups/${groupId}/wall`).then(setWall); }, [groupId]);
  if (!wall) return <p className="hint">Loading families…</p>;
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
      await api(`/api/groups/${groupId}/requests`, {
        method: "POST",
        body: JSON.stringify({
          subject_type: subject.type, subject_id: subject.id,
          title, body, category_slug: cat, is_urgent: urgent, privacy,
        }),
      });
      onDone();
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="form">
      <p className="hint">For <strong>{subject.label}</strong> · <button className="link" onClick={() => setSubject(null)}>change</button></p>
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
      {err && <p className="error">{err}</p>}
      <button className="primary" disabled={!title.trim()} onClick={submit}>Add request</button>
    </div>
  );
}

/* -------------------------------- join wizard ----------------------------- */

function JoinWizard({ code }: { code: string }) {
  const [info, setInfo] = useState<{ group_name: string; families: { id: string; family_name: string }[] } | null>(null);
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [famId, setFamId] = useState<string | null>(null);
  const [newFam, setNewFam] = useState("");
  const [result, setResult] = useState<{ status: string; message: string } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api(`/api/join/${code}`).then(setInfo).catch(e => setErr(e.message)); }, [code]);
  if (err && !info) return <div className="join"><p className="error">{err}</p></div>;
  if (!info) return <div className="join"><p className="hint">Opening your invitation…</p></div>;
  if (result) {
    return (
      <div className="join">
        <h1 className="join-title">{result.status === "pending" ? "Almost there" : "Welcome!"}</h1>
        <p className="join-lede">{result.message}</p>
        {result.status !== "pending" && <a className="primary btn-link" href="/">Open the prayer wall</a>}
      </div>
    );
  }
  const submit = async () => {
    setErr("");
    try {
      const r = await api(`/api/join/${code}`, {
        method: "POST",
        body: JSON.stringify({
          display_name: name, email, password: pw,
          family_id: famId, new_family_name: famId ? null : (newFam || null),
        }),
      });
      setResult(r);
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="join">
      {step === 0 && (<>
        <p className="eyebrow">You are invited to</p>
        <h1 className="join-title">{info.group_name}</h1>
        <p className="join-lede">A shared prayer wall for our study — requests, updates, and answered prayers, kept within the group.</p>
        <button className="primary" onClick={() => setStep(1)}>Join the group</button>
      </>)}
      {step === 1 && (<div className="form">
        <h2 className="join-sub">About you</h2>
        <label>Your name<input autoComplete="name" value={name} onChange={e => setName(e.target.value)} /></label>
        <label>Email<input type="email" inputMode="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} /></label>
        <label>Password<input type="password" autoComplete="new-password" value={pw} onChange={e => setPw(e.target.value)} placeholder="8+ characters" /></label>
        <button className="primary" disabled={!name || !email || pw.length < 8} onClick={() => setStep(2)}>Next</button>
      </div>)}
      {step === 2 && (<div className="form">
        <h2 className="join-sub">Your family</h2>
        {info.families.map(f => (
          <button key={f.id} className={`picker-card ${famId === f.id ? "sel" : ""}`}
            onClick={() => { setFamId(f.id); setNewFam(""); }}>{f.family_name}</button>
        ))}
        <label>Or start a new family
          <input value={newFam} onChange={e => { setNewFam(e.target.value); setFamId(null); }} placeholder="e.g. The Andersons" />
        </label>
        {err && <p className="error">{err}</p>}
        <button className="primary" disabled={!famId && !newFam.trim()} onClick={submit}>Finish</button>
      </div>)}
    </div>
  );
}

/* ------------------------------- auth screen ------------------------------ */

function AuthView({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const go = async () => {
    setErr("");
    try {
      await api(`/api/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify(mode === "login" ? { email, password: pw } : { email, password: pw, display_name: name }),
      });
      onAuthed();
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="join">
      <h1 className="join-title brand">Intercede</h1>
      <p className="join-lede">Bear one another's burdens.</p>
      <div className="form">
        {mode === "register" && <label>Your name<input autoComplete="name" value={name} onChange={e => setName(e.target.value)} /></label>}
        <label>Email<input type="email" inputMode="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} /></label>
        <label>Password<input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} value={pw} onChange={e => setPw(e.target.value)} /></label>
        {err && <p className="error">{err}</p>}
        <button className="primary" onClick={go}>{mode === "login" ? "Sign in" : "Create account"}</button>
        <button className="link" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "New here? Create an account" : "Have an account? Sign in"}
        </button>
      </div>
    </div>
  );
}

/* ---------------------------------- shell --------------------------------- */

export default function App() {
  const joinCode = useMemo(() => {
    const match = window.location.pathname.match(/^\/join\/([A-Za-z0-9]+)/);
    return match ? match[1] : null;
  }, []);
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [groups, setGroups] = useState<GroupT[] | null>(null);
  const [gid, setGid] = useState<string | null>(null);
  const [tab, setTab] = useState<"wall" | "pray" | "praise" | "add">("wall");
  const [newGroupName, setNewGroupName] = useState("");

  const loadGroups = useCallback(() => {
    api("/api/groups").then((gs: GroupT[]) => {
      setGroups(gs);
      if (gs.length > 0) setGid(g => g ?? gs[0].id);
    });
  }, []);
  useEffect(() => {
    api("/api/me").then(() => { setAuthed(true); loadGroups(); }).catch(() => setAuthed(false));
  }, [loadGroups]);

  if (joinCode) return <JoinWizard code={joinCode} />;
  if (authed === null) return <p className="hint">…</p>;
  if (!authed) return <AuthView onAuthed={() => { setAuthed(true); loadGroups(); }} />;
  if (!groups) return <p className="hint">…</p>;
  if (groups.length === 0) {
    return (
      <div className="join">
        <h1 className="join-title brand">Intercede</h1>
        <p className="join-lede">You are not in a group yet. Scan your group's QR poster to join, or start a new group.</p>
        <div className="form">
          <label>Group name<input value={newGroupName} onChange={e => setNewGroupName(e.target.value)} placeholder="Tuesday Night Study" /></label>
          <button className="primary" disabled={!newGroupName.trim()}
            onClick={() => api("/api/groups", { method: "POST", body: JSON.stringify({ name: newGroupName }) }).then(loadGroups)}>
            Create group
          </button>
        </div>
      </div>
    );
  }
  const group = groups.find(g => g.id === gid) ?? groups[0];
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Intercede</span>
        <span className="group-name">{group.name}</span>
      </header>
      <main className="content">
        {tab === "wall" && <WallView key={`w${gid}`} groupId={group.id} />}
        {tab === "pray" && <SessionView key={`s${gid}`} groupId={group.id} />}
        {tab === "praise" && <PraiseView key={`p${gid}`} groupId={group.id} />}
        {tab === "add" && <NewRequestView groupId={group.id} onDone={() => setTab("wall")} />}
      </main>
      <nav className="tabs" aria-label="Main">
        {([["wall", "Wall"], ["pray", "Pray"], ["praise", "Praise"], ["add", "+ Add"]] as const).map(([t, label]) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{label}</button>
        ))}
      </nav>
    </div>
  );
}
