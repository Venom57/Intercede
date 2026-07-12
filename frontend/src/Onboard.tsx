import { useEffect, useState } from "react";
import { api } from "./api";

/* ------------------------------- auth screen ------------------------------ */

export function AuthView({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setErr("");
    setBusy(true);
    try {
      await api(`/api/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify(mode === "login" ? { email, password: pw } : { email, password: pw, display_name: name }),
      });
      onAuthed();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="join">
      <h1 className="join-title brand">Intercede</h1>
      <p className="join-lede">Bear one another's burdens.</p>
      <div className="form">
        {mode === "register" && <label>Your name<input autoComplete="name" value={name} onChange={e => setName(e.target.value)} /></label>}
        <label>Email<input type="email" inputMode="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} /></label>
        <label>Password<input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} value={pw} onChange={e => setPw(e.target.value)} /></label>
        {err && <p className="error" role="alert">{err}</p>}
        <button className="primary" disabled={busy} onClick={go}>{mode === "login" ? "Sign in" : "Create account"}</button>
        <button className="link" onClick={() => { setErr(""); setMode(mode === "login" ? "register" : "login"); }}>
          {mode === "login" ? "New here? Create an account" : "Have an account? Sign in"}
        </button>
      </div>
    </div>
  );
}

/* -------------------------------- join wizard ----------------------------- */

function useWizardStep(): [number, (s: number) => void] {
  // steps live in the hash so the back gesture walks the wizard, not out of the app
  const read = () => {
    const m = window.location.hash.match(/^#\/step(\d)$/);
    return m ? Number(m[1]) : 0;
  };
  const [step, setStep] = useState(read);
  useEffect(() => {
    const on = () => setStep(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (s: number) => {
    if (s === 0) window.location.hash = "";
    else window.location.hash = `/step${s}`;
  };
  return [step, go];
}

export function JoinWizard({ code }: { code: string }) {
  const [info, setInfo] = useState<{ group_name: string; families: { id: string; family_name: string }[] } | null>(null);
  const [step, setStep] = useWizardStep();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [famId, setFamId] = useState<string | null>(null);
  const [newFam, setNewFam] = useState("");
  const [result, setResult] = useState<{ status: string; message: string } | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<typeof info>(`/api/join/${code}`).then(setInfo)
      .catch((e: Error) => setErr(e.message));
  }, [code]);
  if (err && !info) return <div className="join"><p className="error" role="alert">{err}</p></div>;
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
    setBusy(true);
    try {
      const r = await api<{ status: string; message: string }>(`/api/join/${code}`, {
        method: "POST",
        body: JSON.stringify({
          display_name: name, email, password: pw,
          family_id: famId, new_family_name: famId ? null : (newFam || null),
        }),
      });
      setResult(r);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
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
        {err && <p className="error" role="alert">{err}</p>}
        <button className="primary" disabled={busy || (!famId && !newFam.trim())} onClick={submit}>Finish</button>
      </div>)}
    </div>
  );
}
