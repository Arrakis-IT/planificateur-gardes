# Planificateur de Gardes

Multi-tenant web application to schedule medical on-call duties for clinics in Luxembourg.

*Application web multi-cabinets pour planifier les gardes médicales des cabinets au Luxembourg.*

> Work in progress. Full documentation will come with the first release.

## Local development

```bash
cp .env.example .env
docker compose up --build
```

- App: http://localhost:8000 (healthcheck: http://localhost:8000/healthz)
- Mailpit: http://localhost:8025

`.env.example` ships with values ready for development, so `cp .env.example .env && docker compose up` works out of the box.
For production, regenerate `APP_SECRET_KEY` (the command is included as a comment in `.env.example`); the app refuses to start in production with the dev key.

Run checks inside the dev container:

```bash
docker compose exec app ruff check .
docker compose exec app ruff format --check .
docker compose exec app mypy app
docker compose exec -e TEST_DATABASE_URL=postgresql+asyncpg://planificateur_gardes:planificateur_gardes@db:5432/planificateur_gardes_test app pytest
```

Dependencies are managed with [Poetry](https://python-poetry.org/) 2.x (`poetry.lock` is committed).
To work outside Docker:

```bash
poetry install --no-root --with dev
poetry run pytest
```

After changing dependencies in `pyproject.toml`, regenerate the lock with `poetry lock` and commit it.

## Statistics / Statistiques

La page « Résultats » reprend le tableau de statistiques de la version d'origine (Semaine,
Week-end, Sur place, Congés). Les jours fériés tombant en semaine sont comptés dans la colonne
« Week-end ». La colonne « Congés » ne compte que les jours de congés compris dans la période
de la planification, alors que la version Excel/VBA d'origine comptait tous les jours de congés,
quelle que soit leur date : des statistiques par période sont plus parlantes pour une
planification semestrielle, et les congés hors période n'ont aucune incidence sur l'équilibre
du planning en cours.

## Security design (summary)

- **Passwords**: argon2id (`argon2-cffi`), minimum 12 characters.
- **Sessions**: signed cookie (`itsdangerous`) holding a random token; only its SHA-256 hash is
  stored in the `session` table, so sessions can be revoked (logout, password change/reset,
  deactivation). Details in `app/services/auth.py`.
- **CSRF**: signed double-submit cookie on every POST (`app/middleware/csrf.py`).
- **Brute force**: 5 login attempts/min per IP, account locked 15 min after 10 failures,
  password reset limited to 3/hour per account; Cloudflare Turnstile on `/login` and `/reset`.

## Deployment

Production runs the `prod` image behind the shared reverse proxy (TLS termination), with
`docker-compose.prod.yml`. Essentials:

- `APP_ENV=production`, a strong `APP_SECRET_KEY`, `APP_BASE_URL=https://gardes.arrakis.lu`
  (both enforced at startup), `SESSION_COOKIE_SECURE=true`, `TURNSTILE_ENABLED=true`.
- **One uvicorn worker**: presence and live events are kept in process memory.
- Build with `--build-arg GIT_SHA=$(git rev-parse --short HEAD)`: the SHA is written to
  `/app/VERSION` and used as the Sentry release.

### Reverse proxy

- **Do not overwrite or strip the security headers set by the app** (CSP, HSTS,
  X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, COOP/CORP):
  no `proxy_hide_header` for them and no `add_header` with different values. HSTS is sent by
  the app in production; if the proxy also sends it, it must be the identical value
  `max-age=63072000; includeSubDomains; preload`. The proxy must redirect HTTP to HTTPS.
- Forward `X-Forwarded-For` / `X-Forwarded-Proto` (uvicorn runs with `--proxy-headers`;
  restrict `FORWARDED_ALLOW_IPS` to the proxy's network).
- `/sse/presence` (Server-Sent Events): `proxy_buffering off;`, `proxy_read_timeout` above
  15 s (keep-alive ping), HTTP/1.1 with `Connection ""`.

### Content Security Policy

`script-src 'self'` and `style-src 'self'`: no inline scripts or `<style>` blocks anywhere
(checked by a test). The single relaxation is `style-src-attr 'unsafe-inline'`, needed for
doctor colors applied as `style="--doctor-color: …"` (values come from the server-side
palette). Generating per-doctor CSS classes would mean regenerating a stylesheet whenever a
doctor is added or edited, and inline *attributes* (not `<style>` blocks) have a much smaller
attack surface. For a stricter CSP later: serve the classes from an endpoint such as
`/static/doctor-colors.css?v={hash}` and drop the relaxation.

### Error reporting

Set `SENTRY_DSN` to enable Sentry (nothing is sent otherwise). Only unhandled exceptions and
HTTP 500 are reported, without performance tracing. Personal data is removed before sending
(`app/observability.py`, `scrub_event`): no user, cookies, request body, query string or
sensitive headers; emails and IP addresses in messages are masked; stack frames are sent
without local variables. `GET /_dev/sentry-test` (development only) raises a test error.

## License

[AGPL-3.0](LICENSE) © Arrakis IT Services
This project is licensed under the GNU Affero General Public License v3 or later (AGPL-3.0-or-later). See the LICENSE file for the full text.
