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

- Auth: argon2id, signed HttpOnly session cookie
- Security hardening: per-IP rate limiting on login/register/join,
  account-enumeration resistance (identical error + timing-parity hash),
  security response headers, non-root container, fail-fast on the default
  SECRET_KEY when APP_ENV=production — see SECURITY.md
- Audit log: logins, joins, approvals, invite rotations, deletions, and
  status changes recorded per group; leaders read it at
  GET /api/groups/{gid}/audit
- Request updates thread: POST/GET /api/requests/{rid}/updates
  (author/leader post; anyone who can see the request reads)
- Soft deletion with cascade: DELETE /api/requests/{rid} (author/leader),
  DELETE /api/families/{fid} and /api/members/{mid} (leader) also retire
  attached requests
- Groups, Families, Members (members decoupled from user accounts)
- Requests: polymorphic subject (family|member), categories, urgency,
  privacy (group / leaders_only / family_only) enforced in the SQL query path
- Verse packs: 11 categories seeded with KJV text, per-request rotation
- Wall endpoint: hierarchical rollup, urgent pinned
- "I prayed": idempotent per user/day, aggregate counts
- Answered → praise wall with answer note
- QR join: /join/{code} wizard (join or create family), approval queue,
  code rotation, server-rendered SVG poster QR
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
  - Leader tools in-app: join-queue approve/deny, invite link copy + QR
    poster, code rotation, audit trail; members get group switcher + logout
  - Request cards: updates thread (author/leader post) and remove
  - Self-hosted variable fonts (no third-party requests), dark
    color-scheme for native controls, press states, ARIA live regions,
    safe-area padding on all four edges

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`) runs on every push/PR:

- **Backend** — ruff lint + pytest unit suite (19 tests: golden paths,
  privacy enforcement, tenant isolation, rate limiting, enumeration
  resistance, role gates, audit access control)
- **Frontend** — ESLint (typescript-eslint + react-hooks), TypeScript
  typecheck + production Vite build
- **Security** — pip-audit, Bandit, npm audit; CodeQL runs in a separate
  weekly + per-PR workflow; Dependabot keeps pip/npm/actions current
- **Docker** — image build validated on PRs; pushed to
  `ghcr.io/<owner>/intercede-api` on merge to main

## Known gaps vs. the full spec (next phases)

- Alembic migrations (dev bootstrap uses create_all)
- Magic-link auth, MFA, CAPTCHA on join
- Notifications/digests, PDF prayer sheet export
- Verse seed texts entered by hand — proofread against a printed KJV
- Production static hosting for the frontend (nginx serving dist/ and
  proxying /api — compose currently ships the API only)
