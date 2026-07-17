/* Fetch wrapper with typed errors, global 401 handling, a toast bus, and an
   offline queue for "I prayed" taps (localStorage, flushed on reconnect). */

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
  get offline() { return this.status === 0; }
}

export const UNAUTHED_EVENT = "intercede:unauthed";
export const TOAST_EVENT = "intercede:toast";

export function toast(message: string) {
  window.dispatchEvent(new CustomEvent(TOAST_EVENT, { detail: message }));
}

export async function api<T = unknown>(path: string, opts: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
  } catch {
    throw new ApiError(0, "You appear to be offline");
  }
  if (!res.ok) {
    // expired/invalid session anywhere (except while signing in/joining) sends
    // the user back to the auth screen instead of a dead spinner
    if (res.status === 401 && !path.startsWith("/api/auth/") && !path.startsWith("/api/join/")) {
      window.dispatchEvent(new Event(UNAUTHED_EVENT));
    }
    const body = await res.json().catch(() => null);
    let detail = res.statusText;
    if (body && typeof body.detail === "string") {
      detail = body.detail;
    } else if (body && Array.isArray(body.detail)) {
      // FastAPI validation errors arrive as a list of {loc, msg} objects
      const msgs = (body.detail as { loc?: (string | number)[]; msg?: string }[])
        .map(d => (d.msg ? (d.loc?.length ? `${d.loc[d.loc.length - 1]}: ${d.msg}` : d.msg) : ""))
        .filter(Boolean);
      if (msgs.length) detail = msgs.join("; ");
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

/* ------------------------- offline "I prayed" queue ------------------------ */

const QUEUE_KEY = "intercede.prayQueue";

function readQueue(): string[] {
  try {
    const q = JSON.parse(localStorage.getItem(QUEUE_KEY) ?? "[]");
    return Array.isArray(q) ? q : [];
  } catch {
    return [];
  }
}

function writeQueue(q: string[]) {
  localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
}

/** POST a prayed action; when offline, queue it for the next reconnect. */
export async function recordPrayed(requestId: string): Promise<"recorded" | "queued"> {
  try {
    await api(`/api/requests/${requestId}/prayed`, { method: "POST" });
    return "recorded";
  } catch (e) {
    if (e instanceof ApiError && e.offline) {
      const q = readQueue();
      if (!q.includes(requestId)) writeQueue([...q, requestId]);
      return "queued";
    }
    throw e;
  }
}

/** Replay queued prayed actions; returns how many were delivered. */
export async function flushPrayQueue(): Promise<number> {
  const q = readQueue();
  if (q.length === 0) return 0;
  let delivered = 0;
  const remaining: string[] = [];
  for (const id of q) {
    try {
      await api(`/api/requests/${id}/prayed`, { method: "POST" });
      delivered++;
    } catch (e) {
      // offline again or not signed in yet: keep for a later flush;
      // anything else (request deleted, access revoked) drops the entry
      if (e instanceof ApiError && (e.offline || e.status === 401)) remaining.push(id);
    }
  }
  writeQueue(remaining);
  return delivered;
}
