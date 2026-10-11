# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Per-IP rate limiting (slowapi, in-memory: the app runs a single worker).

The client IP comes from `request.client`, which uvicorn `--proxy-headers` sets from
X-Forwarded-For when the request comes from a trusted proxy (FORWARDED_ALLOW_IPS).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

# Per-IP limit on reset requests; the per-account limit (3/hour) lives in services/auth.py.
RESET_REQUESTS_PER_IP_PER_HOUR = 10

limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")


def login_limit() -> str:
    """Login attempts per IP (RATE_LIMIT_LOGIN_PER_MINUTE)."""
    return f"{get_settings().rate_limit_login_per_minute}/minute"


def reset_limit() -> str:
    """Password reset requests per IP."""
    return f"{RESET_REQUESTS_PER_IP_PER_HOUR}/hour"
