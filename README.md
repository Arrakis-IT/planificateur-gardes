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

Run checks inside the dev container:

```bash
docker compose exec app ruff check .
docker compose exec app ruff format --check .
docker compose exec app mypy app
docker compose exec -e TEST_DATABASE_URL=postgresql+asyncpg://planificateur_gardes:planificateur_gardes@db:5432/planificateur_gardes_test app pytest
```

## License

[AGPL-3.0](LICENSE) © Arrakis IT Services
