# Contributing

Thank you for helping! This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
Security issues: please **do not** open a public issue, see [SECURITY.md](SECURITY.md).

## Development setup

1. Install Docker (with Docker Compose). Optional, to work outside containers: Python 3.12 and
   [Poetry](https://python-poetry.org/) 2.x.
2. Fork the repository on GitHub, then clone your fork:

   ```bash
   git clone https://github.com/<you>/planificateur-gardes.git
   cd planificateur-gardes
   git remote add upstream https://github.com/Arrakis-IT/planificateur-gardes.git
   ```

3. Configure and start the stack (app with auto-reload, PostgreSQL 16, Mailpit):

   ```bash
   cp .env.example .env          # working development values, nothing to edit
   docker compose up --build
   ```

   - App: http://localhost:8000 — bootstrap superadmin `admin@example.lu` /
     `dev-only-admin-password` (change it at first login).
   - Mailpit (all outgoing e-mails): http://localhost:8025

4. Install the git hooks (ruff, ruff-format, mypy, gitleaks, commit message check):

   ```bash
   pip install pre-commit        # or: poetry install --no-root --with dev
   pre-commit install --hook-type pre-commit --hook-type commit-msg
   ```

5. Database changes: edit the models in `app/models/`, then generate and review a migration:

   ```bash
   docker compose exec app alembic revision --autogenerate -m "short description"
   docker compose exec app alembic upgrade head
   docker compose exec app alembic check     # models and migrations in sync
   ```

## Running the checks

```bash
docker compose exec -e TEST_DATABASE_URL=postgresql+asyncpg://planificateur_gardes:planificateur_gardes@db:5432/planificateur_gardes_test app pytest
docker compose exec app ruff check .
docker compose exec app ruff format --check .
docker compose exec app mypy app
```

CI runs the same checks plus a migration round-trip and a production image build. Coverage
must stay ≥ 70 %.

## Coding conventions

### License header

Every project Python module starts with this header:

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services
```

It is placed on the first lines of the file, before the module docstring.
Exceptions: auto-generated Alembic migrations (`alembic/versions/`) and templates.

### Style

- `ruff` (lint) and `ruff format` (formatting) are authoritative; run them before committing.
- `mypy --strict` for everything under `app/`; new modules must type-check strictly.
- Docstrings on every public module, class, function, router and service.
- Code, identifiers, comments, docstrings and commit messages in English; user-facing text
  (UI, e-mails, PDF) in French.
- Clinic data: every query on clinic-scoped tables filters on the request's clinic
  (`ClinicScope`); add cross-clinic isolation tests for new routes
  (`tests/integration/test_isolation.py`).
- No inline `<script>`, `on*=` handlers or `<style>` blocks in templates (strict CSP; a test
  enforces it). Put JavaScript in `app/static/js/`.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/), enforced by the
`conventional-pre-commit` hook: `type(optional scope): summary` in the imperative mood.

| Type | Use for | Example |
|---|---|---|
| `feat` | a new feature | `feat(admin): add clinic custom holidays` |
| `fix` | a bug fix | `fix(results): show generation time in Luxembourg time` |
| `docs` | documentation only | `docs(readme): describe reverse proxy requirements` |
| `chore` | maintenance, config | `chore(env): remove obsolete variable` |
| `test` | tests only | `test(isolation): cover draft routes` |
| `refactor` | no behaviour change | `refactor(config): drop dead length check` |
| `build` | dependencies, Docker, packaging | `build: bump weasyprint to 70.1` |
| `ci` | GitHub Actions | `ci: build multi-arch release images` |

## Pull requests

1. Sync your fork, then create a descriptive branch from `main`
   (`feat/onsite-bulk-edit`, `fix/pdf-footer`).
2. Keep the change focused; add or update tests; update the docs when behaviour changes.
3. Open a pull request against `main` using the template: what, why, how it was tested.
4. CI must be green and a maintainer must review before merging.
5. Pull requests are squash-merged with a Conventional Commit title.

## Issues

Use the templates: [bug report](.github/ISSUE_TEMPLATE/bug_report.md) or
[feature request](.github/ISSUE_TEMPLATE/feature_request.md). Never include personal data
(names, e-mails of doctors, schedules) in issues or screenshots.
