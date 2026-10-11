# TODO

Items to resolve in later phases. Move them to GitHub issues once the repository is published.

## Phase 6 — Isolation of assignment edits

Draft edits will address assignments (by date or id). Resolve them through the clinic's
active generation (`generation.clinic_id == scope`), never by bare id, and add the new routes
to `CROSS_CLINIC_CASES` in `tests/integration/test_isolation.py`.

## Phase 9 — CSP

`style-src-attr 'unsafe-inline'` is required while doctor colors are applied with an inline
`style="--doctor-color: …"` attribute.
Clean alternative: generate per-doctor CSS classes in a stylesheet regenerated when doctors
are added or edited. Decide in Phase 9.

Also for the CSP:
- HTMX injects a `<style>` element for request indicators unless disabled with
  `<meta name="htmx-config" content='{"includeIndicatorStyles": false}'>`; disable it rather
  than allowing inline styles.
- Turnstile needs `script-src` and `frame-src https://challenges.cloudflare.com`.
