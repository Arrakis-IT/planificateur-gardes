## What

<!-- Summary of the change. Title must follow Conventional Commits (feat:, fix:, …). -->

## Why

<!-- Problem solved, linked issue (Closes #…). -->

## How it was tested

- [ ] `pytest` green (coverage ≥ 70 %)
- [ ] `ruff check .` and `ruff format --check .`
- [ ] `mypy app`
- [ ] Migration added and `alembic check` clean (if models changed)
- [ ] Manually checked in the browser (desktop and mobile, if UI changed)

## Checklist

- [ ] SPDX header on new Python modules
- [ ] Clinic-scoped queries filter on the request's clinic; isolation tests added for new routes
- [ ] No inline scripts / styles in templates (CSP)
- [ ] Docs updated (README, docstrings) if behaviour changed
- [ ] No secrets or personal data in code, tests, logs or screenshots
