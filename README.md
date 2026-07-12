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
- Frontend: mobile-first PWA-ready shell — Wall / Pray (session mode) /
  Praise / Add tabs, join wizard, 49 KB gzipped JS

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`) runs on every push/PR:

- **Backend** — ruff lint + pytest unit suite (19 tests: golden paths,
  privacy enforcement, tenant isolation, rate limiting, enumeration
  resistance, role gates, audit access control)
- **Frontend** — TypeScript typecheck + production Vite build
- **Security** — pip-audit, Bandit, npm audit; CodeQL runs in a separate
  weekly + per-PR workflow; Dependabot keeps pip/npm/actions current
- **Docker** — image build validated on PRs; pushed to
  `ghcr.io/<owner>/intercede-api` on merge to main

## Known gaps vs. the full spec (next phases)

- Alembic migrations (dev bootstrap uses create_all)
- Magic-link auth, MFA, CAPTCHA on join
- Notifications/digests, PDF prayer sheet export, leader dashboard
- Service worker offline queue for prayed actions
- Verse seed texts entered by hand — proofread against a printed KJV
- Frontend UI for the new updates/delete/audit endpoints
