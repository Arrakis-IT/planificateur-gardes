# Planificateur de Gardes

[![CI](https://github.com/Arrakis-IT/planificateur-gardes/actions/workflows/ci.yml/badge.svg)](https://github.com/Arrakis-IT/planificateur-gardes/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Arrakis-IT/planificateur-gardes)](https://github.com/Arrakis-IT/planificateur-gardes/releases)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](LICENSE)

Multi-tenant web application that schedules medical on-call duties for clinics in Luxembourg.
Each clinic manages its doctors' vacations and on-site days, generates a balanced schedule for a
semester (weekends assigned as Saturday + Sunday blocks, Luxembourg public holidays handled), edits
it collaboratively with live presence, and emails the PDF of the planning to every doctor.

*Application web multi-cabinets de planification des gardes médicales au Luxembourg. Chaque
cabinet gère les congés de ses médecins et les jours de présence sur place, génère un planning
équilibré pour un semestre (week-ends attribués en bloc samedi + dimanche, jours fériés
luxembourgeois pris en compte), le modifie à plusieurs avec indication de présence en direct et
envoie le PDF du planning par e-mail à tous les médecins.*

## Screenshots

| | |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Month](docs/screenshots/month.png) |
| ![Results](docs/screenshots/results.png) | ![Mobile](docs/screenshots/mobile.png) |
| ![PDF](docs/screenshots/pdf.png) | ![Admin](docs/screenshots/admin.png) |

## Stack

- **Python 3.12**, **FastAPI 0.143** (async), **SQLAlchemy 2.1** (asyncpg), **Alembic 1.20**,
  **Pydantic 2.14**
- **PostgreSQL 16**
- Server-rendered **Jinja2** pages + **HTMX 2.0.4** (self-hosted), vanilla JavaScript, PWA
- **WeasyPrint 70** (PDF), SMTP via **Brevo** (Mailpit in development)
- Server-Sent Events (**sse-starlette**) for presence and live edits
- **Sentry** (optional), **Cloudflare Turnstile** on login / password reset
- Tooling: Poetry 2, ruff, mypy (strict), pytest, Docker / Docker Compose

## Local development

Requirements: Docker with Docker Compose.

```bash
git clone https://github.com/Arrakis-IT/planificateur-gardes.git
cd planificateur-gardes
cp .env.example .env
docker compose up --build
```

`.env.example` ships with working **development** values: `cp .env.example .env && docker compose up`
works out of the box (secrets prefixed `dev-only-` are refused in production).

| Service | URL |
|---|---|
| Application | http://localhost:8000 (health: http://localhost:8000/healthz) |
| Mailpit (captured e-mails) | http://localhost:8025 |

A superadmin is created at first start from the bootstrap variables of `.env.example`:

- e-mail `admin@example.lu`, password `dev-only-admin-password` (a new password is required at
  first login)

From the admin panel, create a clinic and its doctors, then log in as a doctor (or use
*Se connecter en tant que*) to use the clinic pages.

The code is mounted into the container with auto-reload. Migrations run at container start
(`alembic upgrade head`).

## Testing

Run inside the dev container (`docker compose exec app …`), or locally after
`poetry install --no-root --with dev`:

```bash
docker compose exec -e TEST_DATABASE_URL=postgresql+asyncpg://planificateur_gardes:planificateur_gardes@db:5432/planificateur_gardes_test app pytest
docker compose exec app ruff check .
docker compose exec app ruff format --check .
docker compose exec app mypy app
docker compose exec app poetry run alembic check
```

Tests use a separate database (`planificateur_gardes_test`, created by
`docker/postgres-init`). Coverage must stay ≥ 70 % (currently ~96 %).

After changing dependencies in `pyproject.toml`, regenerate the lock file with `poetry lock`
and commit it.

## Algorithm

The scheduler (`app/services/scheduler.py`) is a **faithful port** of the algorithm of the
original standalone page (itself ported from Excel/VBA): same phases, heuristics, tie-breaking
and number of random restarts, with the original Spanish function names kept to read both side
by side. Its output distribution was checked against the original JavaScript.

1. **First weekend**: one doctor available on both days, chosen at random.
2. **Other weekends**: Saturday + Sunday to one doctor, avoiding consecutive weekends; best of
   100 runs (no consecutive weekends first, then the smallest spread).
3. **On-site days**: least-loaded doctor first, avoiding the day before / after; best of 100.
4. **Remaining days**: same queue heuristic on the total number of duties; best of 100, then
   the fewest runs of ≥ 3 consecutive days.

The algorithm balances weekend pairs, on-site days and total duties. Luxembourg public holidays
(official + clinic-specific) are assigned as single days and count as a Sunday in the
statistics. Details: [module docstring](app/services/scheduler.py).

### Statistics / Statistiques

La page « Résultats » reprend le tableau de statistiques de la version d'origine (Semaine,
Week-end, Sur place, Congés). Les jours fériés tombant en semaine sont comptés dans la colonne
« Week-end ». La colonne « Congés » ne compte que les jours de congés compris dans la période de
la planification, alors que la version Excel/VBA d'origine comptait tous les jours de congés,
quelle que soit leur date : des statistiques par période sont plus parlantes pour une
planification semestrielle, et les congés hors période n'ont aucune incidence sur l'équilibre du
planning en cours.

## Security design (summary)

- **Passwords**: argon2id, minimum 12 characters; forced change for admin-issued passwords.
- **Sessions**: signed cookie (`itsdangerous`) holding a random token; only its SHA-256 hash is
  stored server-side, so sessions are revocable (logout, password change, deactivation).
- **CSRF**: signed double-submit cookie on every state-changing request.
- **Brute force**: 5 login attempts/min per IP, account locked 15 min after 10 failures,
  password reset limited to 3/hour per account; Cloudflare Turnstile on `/login` and `/reset`.
- **Multi-tenancy**: every query is scoped by clinic; cross-clinic isolation is covered by tests.
- **Superadmin**: impersonation and read-only clinic view stored server-side, fully audited.
- **Headers**: strict CSP, HSTS (production), frame denial, no-sniff, referrer and permissions
  policies (`app/middleware/security_headers.py`).

## Deployment overview

High level only — the actual deployment (Atreides, Hetzner) is managed separately.

- **Image**: `ghcr.io/arrakis-it/planificateur-gardes:<version>` and `:latest`, built for
  `linux/amd64` and `linux/arm64` by the release workflow on `v*` tags. Run it with
  `docker-compose.prod.yml` (read-only container, all capabilities dropped). The commit SHA is
  stored in `/app/VERSION` (Sentry release).
- **Critical environment variables** (see `.env.example` for all of them):

  ```bash
  APP_ENV=production                      # enables HSTS, secure defaults, startup checks
  APP_SECRET_KEY=…                        # 64 random bytes; "dev-only-" values are refused
  APP_BASE_URL=https://gardes.arrakis.lu  # used in e-mailed links; must be public https://
  DATABASE_URL=postgresql+asyncpg://planificateur_gardes:…@<db-host>:5432/planificateur_gardes
  SESSION_COOKIE_SECURE=true              # cookies only over HTTPS
  BREVO_SMTP_USER=… / BREVO_SMTP_PASSWORD=…   # outgoing e-mail
  MAIL_DEV_HOST=                          # must be empty in production (else Mailpit is used)
  TURNSTILE_ENABLED=true / TURNSTILE_SITE_KEY=… / TURNSTILE_SECRET_KEY=…
  INITIAL_SUPERADMIN_EMAIL=… / INITIAL_SUPERADMIN_PASSWORD=…  # first start only
  SENTRY_DSN=…                            # optional; empty disables Sentry
  FORWARDED_ALLOW_IPS=<proxy network>     # trust X-Forwarded-* only from the proxy
  ```

- **Single uvicorn worker**: presence and live events (SSE) are kept in process memory. Scaling
  out would need a shared channel (e.g. PostgreSQL LISTEN/NOTIFY).
- **Reverse proxy (nginx)**:
  - TLS termination and HTTP → HTTPS redirect.
  - For `/sse/`: `proxy_buffering off;`, `proxy_read_timeout` ≥ 15 s (above the keep-alive
    ping), HTTP/1.1 with `proxy_set_header Connection "";`.
  - Pass `Host`, `X-Forwarded-For` and `X-Forwarded-Proto` (uvicorn runs with
    `--proxy-headers`).
  - **Keep the security headers set by the app** (CSP, HSTS, X-Frame-Options,
    X-Content-Type-Options, Referrer-Policy, Permissions-Policy, COOP/CORP): no
    `proxy_hide_header` and no `add_header` overriding them. If the proxy also sends HSTS, it
    must be the same value: `max-age=63072000; includeSubDomains; preload`.
- **Database**: external PostgreSQL 16 shared with other apps, with a dedicated database and user
  (`planificateur_gardes`). The `citext` extension is created by the first migration (trusted
  extension, no superuser needed).
- **Backups**: nightly `scripts/backup.sh` (`pg_dump` custom format, local rotation, 14 days by
  default, files readable by the owner only). Off-site copy to a Hetzner Storage Box is pending.

### Content Security Policy

`script-src 'self'` and `style-src 'self'`: no inline scripts or `<style>` blocks anywhere
(checked by a test). The single relaxation is `style-src-attr 'unsafe-inline'`, needed for doctor
colors applied as `style="--doctor-color: …"` (values come from the server-side palette).
Generating per-doctor CSS classes would mean regenerating a stylesheet whenever a doctor is added
or edited, and inline *attributes* (not `<style>` blocks) have a much smaller attack surface. For
a stricter CSP later: serve the classes from an endpoint such as
`/static/doctor-colors.css?v={hash}` and drop the relaxation.

### Error reporting

Set `SENTRY_DSN` to enable Sentry (nothing is sent otherwise). Only unhandled exceptions and
HTTP 500 are reported, without performance tracing. Personal data is removed before sending
(`app/observability.py`, `scrub_event`): no user, cookies, request body, query string or
sensitive headers; e-mails and IP addresses in messages are masked; stack frames are sent without
local variables. `GET /_dev/sentry-test` (development only) raises a test error.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and, for
vulnerabilities, [SECURITY.md](SECURITY.md).

## License

[GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later)
© 2026 Juan Manuel Suárez – Arrakis IT Services.

This project is licensed under the GNU Affero General Public License v3 or later
(AGPL-3.0-or-later). See the LICENSE file for the full text.

## Credits

- Fonts: [Inter](https://rsms.me/inter/), [Fraunces](https://github.com/undercasetype/Fraunces)
  and [JetBrains Mono](https://www.jetbrains.com/lp/mono/) — SIL Open Font License 1.1
  (`app/static/fonts/README.md`).
- [HTMX](https://htmx.org) 2.0.4 — Zero-Clause BSD (0BSD), self-hosted in `app/static/js/`.
- [WeasyPrint](https://weasyprint.org) — BSD 3-Clause.
- [FastAPI](https://fastapi.tiangolo.com) (MIT), [SQLAlchemy](https://www.sqlalchemy.org) (MIT),
  [Pydantic](https://docs.pydantic.dev) (MIT), [Alembic](https://alembic.sqlalchemy.org) (MIT).
- The scheduling algorithm comes from the original Excel/VBA tool and its standalone JavaScript
  version (gardes.arrakis.lu/gratis).

