# TODO

Items to resolve in later phases. Move them to GitHub issues once the repository is published.

## Deployment notes (live channel, phase 6)

- Presence and SSE fan-out live in the app process: keep **one uvicorn worker**. Scaling
  out needs a shared channel (PostgreSQL LISTEN/NOTIFY) — see `app/services/presence.py`.
- Reverse proxy for `/sse/presence`: `proxy_buffering off;`, `proxy_read_timeout` > 15 s
  (keep-alive ping interval), HTTP/1.1 with `Connection ""`.

## Phase 9 — CSP

`style-src-attr 'unsafe-inline'` is required while doctor colors are applied with an inline
`style="--doctor-color: …"` attribute.
Clean alternative: generate per-doctor CSS classes in a stylesheet regenerated when doctors
are added or edited. Decide in Phase 9.

Also for the CSP:
- HTMX indicator styles are already disabled (`htmx-config` meta in `base.html`), so HTMX
  needs no inline style; `app.js` has no inline handlers either.
- Turnstile needs `script-src` and `frame-src https://challenges.cloudflare.com`.
