# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Versioned URLs of the static shell (cache busting).

Static files are cached on the way (Cloudflare edge, browser HTTP cache, service worker):
with plain URLs a deployment kept serving the previous CSS / JS for hours. Every URL the
pages emit carries `?v=<hash of the static files>`, so a deployment that changes one of
them produces new URLs that no cache has seen yet.
"""

import hashlib
from functools import lru_cache
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Files (relative to /static) of the static shell, precached by the service worker.
PRECACHE = (
    "css/app.css",
    "js/app.js",
    "js/htmx.min.js",
    "fonts/inter-latin.woff2",
    "fonts/fraunces-latin.woff2",
    "fonts/jetbrains-mono-latin.woff2",
    "icons/icon.svg",
    "icons/icon-192.png",
    "icons/icon-512.png",
    "icons/icon-maskable-512.png",
    "icons/apple-touch-icon.png",
    "icons/favicon-32.png",
)


@lru_cache
def static_version() -> str:
    """Short hash of the precached files' content (changes on every asset change)."""
    digest = hashlib.sha256()
    for name in PRECACHE:
        digest.update(name.encode())
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:12]


def static_url(name: str) -> str:
    """Versioned URL of a static file, e.g. "/static/js/app.js?v=80de6fadedc2"."""
    return f"/static/{name}?v={static_version()}"
