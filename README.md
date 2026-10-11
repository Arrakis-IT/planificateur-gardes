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

## License

[AGPL-3.0](LICENSE) © Arrakis IT Services
This project is licensed under the GNU Affero General Public License v3 or later (AGPL-3.0-or-later). See the LICENSE file for the full text.
