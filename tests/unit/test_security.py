# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Tests for password hashing, tokens, password policy and redirect safety."""

import pytest

from app.config import get_settings
from app.dependencies import safe_next_path
from app.middleware.csrf import form_token
from app.repositories.clinics import slugify
from app.services.auth import decode_session_cookie, encode_session_cookie
from app.services.security import (
    generate_temporary_password,
    generate_token,
    hash_password,
    hash_token,
    needs_rehash,
    validate_new_password,
    verify_password,
)


def test_argon2id_roundtrip():
    hashed = hash_password("a very long passphrase")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "a very long passphrase")
    assert not verify_password(hashed, "wrong")
    assert not needs_rehash(hashed)


def test_verify_password_never_raises_on_garbage_hash():
    assert not verify_password("not-a-hash", "whatever")


def test_tokens_are_random_and_hashed():
    a, b = generate_token(), generate_token()
    assert a != b and len(a) >= 40
    assert hash_token(a) == hash_token(a) != hash_token(b)
    assert len(generate_temporary_password()) == 16


@pytest.mark.parametrize(
    ("password", "confirm", "expected_error"),
    [
        ("short", "short", "au moins 12"),
        ("long-enough-pass", "different-pass!!", "ne correspondent pas"),
        ("x" * 129, "x" * 129, "dépasser"),
        ("good-password-123", "good-password-123", None),
    ],
)
def test_password_policy(password, confirm, expected_error):
    error = validate_new_password(password, confirm, "someone@example.com")
    if expected_error is None:
        assert error is None
    else:
        assert error and expected_error in error


def test_password_cannot_be_email():
    error = validate_new_password("Doctor@Example.lu", "Doctor@Example.lu", "doctor@example.lu")
    assert error and "adresse e-mail" in error


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("/admin/users", "/admin/users"),
        ("/mois?x=1", "/mois?x=1"),
        ("https://evil.example", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("/\t/evil.example", "/"),
        ("javascript:alert(1)", "/"),
    ],
)
def test_safe_next_path(value, expected):
    assert safe_next_path(value) == expected


def test_session_cookie_signature():
    settings = get_settings()
    cookie = encode_session_cookie(settings, "raw-token")
    assert decode_session_cookie(settings, cookie) == "raw-token"
    assert decode_session_cookie(settings, cookie + "x") is None
    assert decode_session_cookie(settings, "garbage") is None


def test_csrf_form_token_depends_on_cookie():
    assert form_token("a" * 40) != form_token("b" * 40)
    assert len(form_token("a" * 40)) == 64


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Cabinet Médical du Parc", "cabinet-medical-du-parc"),
        ("  Équipe — Kirchberg!! ", "equipe-kirchberg"),
        ("!!!", "cabinet"),
    ],
)
def test_slugify(name, slug):
    assert slugify(name) == slug
