# Contributing

## Development setup

See [README.md](README.md#local-development): `cp .env.example .env && docker compose up --build`.

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

- Code, identifiers, comments and docstrings in English; user-facing text (UI, emails, PDF) in French.
- `ruff check` and `ruff format` must pass; `mypy` runs in strict mode on `app/`.
- Every public function, router and service has a docstring.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `build:`, `chore:`, `docs:`, `test:`, `refactor:` …), enforced by the `conventional-pre-commit` hook.
