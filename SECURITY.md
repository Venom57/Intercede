# Security

Intercede handles pastorally sensitive data (health, family, and faith
details). Treat every request body as confidential.

## Reporting a vulnerability

Open a private report via GitHub Security Advisories on this repository
(Security → Report a vulnerability). Please do not file public issues for
exploitable bugs. You should receive an acknowledgement within a few days.

## Threat model & controls

| Concern | Control |
| --- | --- |
| Credential stuffing / brute force | Argon2id hashing; per-IP sliding-window rate limits on `login`, `register`, `join` (429 + `Retry-After`) |
| Account enumeration | Identical 401 body for unknown email vs. wrong password; dummy-hash verify keeps timing comparable |
| Session theft | Signed (`itsdangerous`) HttpOnly cookie, `SameSite=Lax`, `Secure` behind TLS (`COOKIE_SECURE=1`), 14-day max age |
| Cross-tenant reads | Every group-scoped endpoint resolves an *active* membership first (deny by default); tested in CI |
| Privacy leaks (`family_only`, `leaders_only`) | Enforced in the SQL query path (`visible_request_filter`), never client-side; invisible requests 404 rather than 403 so existence is not leaked |
| Scripted QR-poster abuse | Join rate limiting, leader approval gate (default ON), invite-code rotation |
| Accountability | Append-only `audit_log` (logins, joins, approvals, rotations, deletions, status changes), readable by group leaders only |
| Response hardening | `X-Content-Type-Options`, `X-Frame-Options: DENY`, restrictive `Content-Security-Policy`, `Cache-Control: no-store` on all API responses |
| Supply chain | `pip-audit`, `npm audit`, Bandit, and CodeQL in CI; Dependabot weekly |
| Container | Non-root runtime user in the API image |

## Deployment checklist

- Set `SECRET_KEY` to a long random value — with `APP_ENV=production` the app
  refuses to boot on the dev default.
- Set `COOKIE_SECURE=1` and serve only over TLS.
- Change the PostgreSQL password in `docker-compose.yml`.
- Put a reverse proxy in front that sets `X-Forwarded-For` (the in-app rate
  limiter keys on it) and adds its own connection-level limits.
- The in-app rate limiter is per-process; multi-replica deployments need a
  shared limiter (proxy or Redis) as the primary control.

## Known gaps (tracked for future phases)

- No Alembic migrations yet (dev bootstrap uses `create_all`).
- No MFA / magic-link auth; no CAPTCHA on the join form.
- No CSRF token — mitigated by `SameSite=Lax` + JSON-only bodies, but a
  token would harden state-changing routes further.
