# TODO

Items to resolve in later phases. Move them to GitHub issues once the repository is published.

## Phase 4 — Cross-clinic isolation tests for new resources

When the holiday, on-site day, generation and assignment routes exist, add them to
`CROSS_CLINIC_CASES` in `tests/integration/test_isolation.py` (one case per HTTP verb):
a clinic-A user must get 403/404 on every clinic-B resource, and the resource must be unchanged.

## Phase 9 — CSP

`style-src-attr 'unsafe-inline'` is required while doctor colors are applied with an inline
`style="--doctor-color: …"` attribute.
Clean alternative: generate per-doctor CSS classes in a stylesheet regenerated when doctors
are added or edited. Decide in Phase 9.
