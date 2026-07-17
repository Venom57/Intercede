# Intercede — vertical slice

Prayer request tracking for Bible study groups. Group → Family → Member
hierarchy with requests attached to a whole family or an individual, KJV verse
packs mapped per category, "I prayed" tracking, praise wall, and QR-poster
onboarding with a leader approval gate (default ON).

## Run it (dev)

Backend (SQLite by default, PostgreSQL via DATABASE_URL):

    cd backend
    pip install -r requirements.txt
    uvicorn app.main:app --reload --port 8000

Frontend (proxies /api to :8000):

    cd frontend
    npm install
    npm run dev        # http://localhost:5173

Tests:

    cd backend && python -m pytest tests/ -q

## What's implemented (slice)

- Auth: argon2id, signed HttpOnly session cookie; logout bumps a per-user
  `session_epoch` so it revokes every outstanding session server-side
- Security hardening: per-IP rate limiting on login/register/join
  (X-Forwarded-For only honored with TRUST_PROXY=1, last entry),
  account-enumeration resistance (identical error + timing-parity hash),
  serialized first-admin bootstrap, security response headers, non-root
  container, fail-fast on the default SECRET_KEY when APP_ENV=production
  — see SECURITY.md
- Audit log: logins, joins, approvals, invite rotations, deletions, member
  removals, role changes, and status changes recorded per group; leaders read
  it (with actor names, paginated) at GET /api/groups/{gid}/audit
- Admin: two tiers. The first account ever created becomes the **site
  admin** (self-host bootstrap) and gets a site-wide admin screen —
  overview stats, every group/class with its designated admins, member
  roster per class with promote/demote, and grant/revoke of site-admin on
  any user (the last site admin cannot be revoked). Each **class admin**
  (group leader) can likewise designate co-admins from the Members & roles
  section of their leader tools (the last admin of a class cannot be
  demoted until another is promoted). Site admins can manage any class
  without being a member of it.
- Request updates thread: POST/GET /api/requests/{rid}/updates
  (author/leader/family-steward post; anyone who can see the request reads)
- Roles enforced server-side: viewer is read-only (no requests, prayers, or
  updates); steward manages requests about their own family; authors always
  see their own requests, whatever the privacy level
- Member removal: DELETE /api/groups/{gid}/members/{uid} (leader or site
  admin) retires the person's prayer-subject rows and attached requests;
  the last leader of a group cannot be removed
- Soft deletion with cascade: DELETE /api/requests/{rid} (author/leader),
  DELETE /api/families/{fid} and /api/members/{mid} (leader) also retire
  attached requests
- Groups, Families, Members (members decoupled from user accounts)
- Requests: polymorphic subject (family|member), categories, urgency,
  privacy (group / leaders_only / family_only) enforced in the SQL query path
- Verse packs: 11 categories seeded with KJV text, per-request rotation
  (rotation is POST /api/requests/{rid}/verse/shuffle; GET never mutates)
- Wall endpoint: hierarchical rollup, urgent pinned
- "I prayed": idempotent per user/day, aggregate counts
- Answered → praise wall with answer note
- QR join: /join/{code} wizard (join or create family), approval queue,
  code rotation, server-rendered SVG poster QR. Family/member rows are
  created at approval, not at request time: pending joins never appear on
  the wall and denied joins leave nothing behind. Pending users see a
  "waiting for approval" screen (memberships are listed with status)
- Frontend: mobile-first installable PWA — Wall / Pray (session mode) /
  Praise / Add tabs plus a settings/leader screen, join wizard,
  ~53 KB gzipped JS
  - Service worker caches the app shell (never API data); manifest + icons
    for home-screen install
  - "I prayed" taps are optimistic and queue offline (localStorage),
    flushing on reconnect
  - Wall payload embeds each request's verse and prayed-today state
    (one request per screen, no N+1 fetches); per-group wall cache makes
    tab switches instant (stale-while-revalidate)
  - Hash routing so the back gesture navigates tabs/wizard steps instead
    of exiting the app
  - Expired sessions route back to sign-in; load failures show retry, not
    a dead spinner
  - Leader tools in-app: join-queue approve/deny (with the family each
    person chose), invite link copy + QR poster, code rotation, audit trail
    with actor names, member remove; members get group switcher + logout
  - Request cards: updates thread, edit (title/body/urgent), mark answered
    with an answer note (feeds the praise wall), remove, and privacy chips
    on family-only / leaders-only requests
  - Real forms throughout (Enter submits; password managers work); prayer
    session syncs "prayed" state back into the cached wall and starts with
    requests not yet prayed for today
  - Self-hosted variable fonts (no third-party requests), dark
    color-scheme for native controls, press states, ARIA live regions,
    safe-area padding on all four edges

## Deploy (Docker Compose + Cloudflare Tunnel)

The compose file ships the whole stack: Postgres, the API, nginx serving the
built PWA (proxying `/api`), and a `cloudflared` connector. The tunnel is the
only ingress — no router ports to open, TLS terminates at Cloudflare's edge.

1. In the Cloudflare dashboard: **Zero Trust → Networks → Tunnels → Create a
   tunnel** (Cloudflared connector). Copy the token out of the `docker run`
   snippet it shows.
2. Add a **Public Hostname** to the tunnel: your domain (e.g.
   `prayer.example.com`) → service `HTTP` → URL `web:80`.
3. On the host: `cp .env.example .env` and fill in `SECRET_KEY` (the file
   shows the generator one-liner), `POSTGRES_PASSWORD`, `PUBLIC_BASE_URL`
   (your public URL — it's what the QR posters encode), and `TUNNEL_TOKEN`.
4. `docker compose up -d --build`
5. Open your domain and register — the **first account becomes site admin**,
   so do this before sharing any join links.

`web` also publishes `:8080` for LAN smoke-testing; remove that `ports:`
line if the tunnel should be the only way in. The visitor-IP chain for rate
limiting (Cloudflare `CF-Connecting-IP` → nginx `X-Forwarded-For` → API with
`TRUST_PROXY=1`) is wired up in `frontend/nginx.conf` and the compose env.

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`) runs on every push/PR:

- **Backend** — ruff lint + pytest unit suite (34 tests: golden paths,
  privacy enforcement, tenant isolation, rate limiting + spoof resistance,
  enumeration resistance, session revocation, join deferral, viewer/steward
  role gates, member removal, audit access control)
- **Frontend** — ESLint (typescript-eslint + react-hooks), TypeScript
  typecheck + production Vite build
- **Security** — pip-audit, Bandit, npm audit; CodeQL runs in a separate
  weekly + per-PR workflow; Dependabot keeps pip/npm/actions current
- **Docker** — image build validated on PRs; pushed to
  `ghcr.io/<owner>/intercede-api` on merge to main

## Known gaps vs. the full spec (next phases)

- Alembic migrations (dev bootstrap uses create_all — note: schema changed
  recently with `users.session_epoch` and
  `group_memberships.pending_family_name`; delete a pre-existing dev
  `intercede.db` so create_all rebuilds it)
- Magic-link auth, MFA, CAPTCHA on join
- Notifications/digests, PDF prayer sheet export
- Verse seed texts entered by hand — proofread against a printed KJV
