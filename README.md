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

## Known gaps vs. the full spec (next phases)

- Alembic migrations (dev bootstrap uses create_all)
- Magic-link auth, MFA, CAPTCHA + rate limiting on join, audit log
- Notifications/digests, PDF prayer sheet export, leader dashboard
- Service worker offline queue for prayed actions
- Verse seed texts entered by hand — proofread against a printed KJV
