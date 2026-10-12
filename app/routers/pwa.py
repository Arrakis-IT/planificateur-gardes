# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""PWA: web app manifest and a minimal service worker (static assets only).

The service worker never caches pages or data: the planning and its shared draft must
always come from the server. It only serves the static shell (CSS, JS, HTMX, fonts,
icons) from cache. The cache name carries a hash of those files, so every deployment
that changes one of them installs a new worker and drops the old cache.
"""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from starlette.responses import Response

router = APIRouter(tags=["pwa"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Files (relative to /static) precached by the service worker.
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

MANIFEST = {
    "name": "Planificateur des gardes",
    "short_name": "Gardes",
    "description": "Planification des gardes médicales du cabinet.",
    "lang": "fr",
    "start_url": "/?source=pwa",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#f6f5f1",
    "theme_color": "#2d4a3e",
    "icons": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {
            "src": "/static/icons/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
        {"src": "/static/icons/icon.svg", "sizes": "any", "type": "image/svg+xml"},
    ],
}


@lru_cache
def static_version() -> str:
    """Short hash of the precached files' content (changes on every asset change)."""
    digest = hashlib.sha256()
    for name in PRECACHE:
        digest.update(name.encode())
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:12]


SERVICE_WORKER = """// Planificateur des gardes service worker: static assets only, no offline data.
const CACHE = "gardes-static-%(version)s";
const PRECACHE = %(precache)s;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key.startsWith("gardes-static-") && key !== CACHE)
          .map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

// Cache-first for precached static files; everything else (pages, HTMX, SSE, POST)
// goes straight to the network, untouched.
self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || !PRECACHE.includes(url.pathname)) return;
  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request))
  );
});
"""


@router.api_route("/manifest.webmanifest", methods=["GET", "HEAD"], include_in_schema=False)
async def manifest() -> Response:
    """Web app manifest (makes "Add to home screen" available)."""
    return Response(
        json.dumps(MANIFEST, ensure_ascii=False),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.api_route("/sw.js", methods=["GET", "HEAD"], include_in_schema=False)
async def service_worker() -> Response:
    """Service worker, served from the root so its scope is the whole app."""
    body = SERVICE_WORKER % {
        "version": static_version(),
        "precache": json.dumps([f"/static/{name}" for name in PRECACHE], indent=2),
    }
    return Response(
        body,
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            # Browsers must always revalidate the worker script to pick up new versions.
            "Cache-Control": "no-cache",
        },
    )
