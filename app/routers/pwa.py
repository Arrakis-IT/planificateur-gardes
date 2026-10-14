# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""PWA: web app manifest and a minimal service worker (static assets only).

The service worker never caches pages or data: the planning and its shared draft must
always come from the server. It only serves the static shell (CSS, JS, HTMX, fonts,
icons) from cache. The cache name carries a hash of those files, so every deployment
that changes one of them installs a new worker and drops the old cache. Precached URLs
are versioned like the ones in the pages (see app/static_assets.py).

PRECACHE and static_version are re-exported from app.static_assets.
"""

import json
from typing import Any

from fastapi import APIRouter
from starlette.responses import Response

from app.static_assets import PRECACHE, static_url, static_version

router = APIRouter(tags=["pwa"])

PNG = "image/png"


def _manifest() -> dict[str, Any]:
    return {
        "name": "Planificateur des gardes",
        "short_name": "Gardes",
        "description": "Planification des gardes médicales du groupe.",
        "lang": "fr",
        "start_url": "/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f6f5f1",
        "theme_color": "#2d4a3e",
        "icons": [
            {"src": static_url("icons/icon-192.png"), "sizes": "192x192", "type": PNG},
            {"src": static_url("icons/icon-512.png"), "sizes": "512x512", "type": PNG},
            {
                "src": static_url("icons/icon-maskable-512.png"),
                "sizes": "512x512",
                "type": PNG,
                "purpose": "maskable",
            },
            {"src": static_url("icons/icon.svg"), "sizes": "any", "type": "image/svg+xml"},
        ],
    }


SERVICE_WORKER = """// Planificateur des gardes service worker: static assets only, no offline data.
const CACHE = "gardes-static-%(version)s";
// Versioned URLs ("?v=<version>"), as emitted by the pages.
const PRECACHE = %(precache)s;
const VERSIONED = new Map(PRECACHE.map((u) => [new URL(u, self.location.origin).pathname, u]));

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
  if (url.origin !== self.location.origin || !VERSIONED.has(url.pathname)) return;
  // Exact match: a page of a newer deployment asks for a new "?v=" that this worker's
  // cache does not hold, so it goes to the network. Unversioned requests (fonts referenced
  // from the CSS) use this worker's own copy.
  const key = url.search ? request : VERSIONED.get(url.pathname);
  event.respondWith(
    caches.match(key).then((cached) => cached || fetch(request))
  );
});
"""


@router.api_route("/manifest.webmanifest", methods=["GET", "HEAD"], include_in_schema=False)
async def manifest() -> Response:
    """Web app manifest (makes "Add to home screen" available)."""
    return Response(
        json.dumps(_manifest(), ensure_ascii=False),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.api_route("/sw.js", methods=["GET", "HEAD"], include_in_schema=False)
async def service_worker() -> Response:
    """Service worker, served from the root so its scope is the whole app."""
    body = SERVICE_WORKER % {
        "version": static_version(),
        "precache": json.dumps([static_url(name) for name in PRECACHE], indent=2),
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
