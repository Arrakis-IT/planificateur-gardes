# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Doctor color palettes per clinic."""

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import AuditLog, Clinic, User
from app.repositories import scheduling as scheduling_repo
from app.services.doctors import DOCTOR_PALETTE
from app.services.palettes import PALETTES, ColorPalette
from tests.conftest import login, post_form, with_csrf

pytestmark = pytest.mark.db

BLUE = PALETTES[ColorPalette.BLUE].hexes
PINK = PALETTES[ColorPalette.PINK_PURPLE].hexes


def _contrast_with_white(hex_color: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return 1.05 / (luminance + 0.05)


def test_palettes_definition():
    assert PALETTES[ColorPalette.DEFAULT].hexes == DOCTOR_PALETTE  # unchanged, 12 colors
    for key in (ColorPalette.GRAYSCALE, ColorPalette.BLUE, ColorPalette.PINK_PURPLE):
        assert len(PALETTES[key].hexes) == 10, key
    for palette in PALETTES.values():
        assert len(set(palette.hexes)) == len(palette.hexes)  # distinct
        assert all(name for _, name in palette.colors)
        for color in palette.hexes:
            assert color == color.lower() and _contrast_with_white(color) >= 4.5, color
    labels = [p.label for p in PALETTES.values()]
    assert labels == ["Par défaut", "Nuances de gris", "Bleus", "Rose et violet"]


@pytest.fixture
async def group(client, db, make_clinic, make_user):
    """Clinic with 3 doctors (creation order: admin, b, c); the group admin is logged in."""
    clinic = await make_clinic("Groupe CRT")
    admin = await make_user("a@ex.lu", "Anne Admin", clinic=clinic)
    admin.is_group_admin = True
    b = await make_user("b@ex.lu", "Bruno Weber", clinic=clinic)
    c = await make_user("c@ex.lu", "Claire Thill", clinic=clinic)
    await db.commit()
    await login(client, "a@ex.lu")
    return clinic, [admin, b, c]


async def _palette(client, value: str, confirm: bool = True):
    data = {"palette": value} | ({"confirm": "true"} if confirm else {})
    return await post_form(client, "/parametres/apparence", data)


async def _colors(db, users) -> list[str]:
    for user in users:
        await db.refresh(user)
    return [u.color for u in users]


async def test_settings_section_for_group_admin_only(client, db, group):
    page = (await client.get("/parametres")).text
    assert "Apparence du groupe" in page
    for label in ("Par défaut", "Nuances de gris", "Bleus", "Rose et violet"):
        assert label in page
    assert page.count('class="swatch" style="--doctor-color:') == 12 + 10 + 10 + 10  # previews
    assert "data-palette-dialog" in page  # confirmation modal
    await login(client, "b@ex.lu")
    assert "Apparence du groupe" not in (await client.get("/parametres")).text
    assert (await _palette(client, "blue")).status_code == 403


async def test_palette_change_recolors_by_creation_order(client, db, group):
    clinic, doctors = group
    refused = await _palette(client, "blue", confirm=False)
    assert refused.status_code == 400 and "Confirmez le changement de palette" in refused.text
    assert await _colors(db, doctors) == list(DOCTOR_PALETTE[:3])

    response = await _palette(client, "blue")
    assert response.status_code == 303
    assert "Palette « Bleus » appliquée" in (await client.get(response.headers["location"])).text
    assert await _colors(db, doctors) == list(BLUE[:3])
    await db.refresh(clinic)
    assert clinic.color_palette == ColorPalette.BLUE
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "clinic_palette_changed"))
    assert entry.user_id == doctors[0].id and entry.clinic_id == clinic.id
    assert entry.meta == {"previous": "default", "new": "blue", "doctors": 3}
    assert (await _palette(client, "nope")).status_code == 400


async def test_override_persists_until_next_palette_change(client, db, group):
    _, doctors = group
    await _palette(client, "blue")
    response = await client.post(
        "/parametres/apparence/couleur",
        data=with_csrf({"user_id": str(doctors[1].id), "color": BLUE[7]}),
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 204
    assert (
        "Couleur de Bruno Weber enregistrée."
        in json.loads(response.headers["HX-Trigger"])["toast"]["message"]
    )
    assert await _colors(db, doctors) == [BLUE[0], BLUE[7], BLUE[2]]
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "doctor_color_changed"))
    assert entry.meta == {"old": BLUE[1], "new": BLUE[7], "palette": "blue"}
    assert entry.target_id == str(doctors[1].id)

    await client.get("/parametres")  # unrelated activity keeps the override
    assert (await _colors(db, doctors))[1] == BLUE[7]
    await _palette(client, "pink_purple")  # a palette change resets every doctor
    assert await _colors(db, doctors) == list(PINK[:3])


async def test_color_outside_the_active_palette_is_rejected(client, db, group, make_user):
    clinic, doctors = group
    await _palette(client, "blue")
    for color in (PINK[0], DOCTOR_PALETTE[0], "#ffffff"):
        response = await post_form(
            client, "/parametres/apparence/couleur", {"user_id": str(doctors[1].id), "color": color}
        )
        assert response.status_code == 400, color
    bad_format = await post_form(
        client, "/parametres/apparence/couleur", {"user_id": str(doctors[1].id), "color": "blue"}
    )
    assert bad_format.status_code == 400
    assert (await _colors(db, doctors))[1] == BLUE[1]

    await make_user("root@ex.lu", "Super Admin", is_superadmin=True)
    await login(client, "root@ex.lu")
    sheet = await client.get(f"/admin/users/{doctors[2].id}")
    assert sheet.text.count("<option value=") == 10 and "Indigo nuit" in sheet.text
    refused = await post_form(client, f"/admin/users/{doctors[2].id}/color", {"color": PINK[1]})
    assert refused.status_code == 400
    ok = await post_form(client, f"/admin/users/{doctors[2].id}/color", {"color": BLUE[9]})
    assert ok.status_code == 303
    assert (await _colors(db, doctors))[2] == BLUE[9]
    via_clinic = await post_form(
        client,
        f"/admin/clinic/{clinic.id}/couleur",
        {"user_id": str(doctors[0].id), "color": "#000000"},
    )
    assert via_clinic.status_code == 400


async def test_ten_doctors_unique_eleventh_cycles(client, db, make_clinic, make_user):
    clinic = await make_clinic("Grand groupe")
    doctors = []
    for i in range(11):
        doctors.append(
            await make_user(f"d{i}@ex.lu", f"Docteur Numero{chr(65 + i)}", clinic=clinic)
        )
    doctors[0].is_group_admin = True
    await db.commit()
    await login(client, "d0@ex.lu")
    assert (await _palette(client, "blue")).status_code == 303
    colors = await _colors(db, doctors)
    assert len(set(colors[:10])) == 10 and colors[:10] == list(BLUE)
    assert colors[10] == colors[0] == BLUE[0]  # index 10 → color 0


async def test_new_doctors_get_a_color_of_the_active_palette(client, db, group, make_user):
    clinic, _ = group
    await _palette(client, "pink_purple")
    await make_user("root@ex.lu", "Super Admin", is_superadmin=True)
    await login(client, "root@ex.lu")
    await post_form(
        client,
        "/admin/users",
        {"email": "new@ex.lu", "full_name": "Nouveau Médecin", "clinic_id": str(clinic.id)},
    )
    new = await db.scalar(select(User).where(User.email == "new@ex.lu"))
    assert new.color in PINK


async def test_superadmin_changes_a_clinic_palette(client, db, group, make_user):
    clinic, doctors = group
    await make_user("root@ex.lu", "Super Admin", is_superadmin=True)
    await login(client, "root@ex.lu")
    assert "Apparence du groupe" in (await client.get(f"/admin/clinic/{clinic.id}")).text
    refused = await post_form(
        client, f"/admin/clinic/{clinic.id}/palette", {"palette": "grayscale"}
    )
    assert refused.status_code == 400
    ok = await post_form(
        client, f"/admin/clinic/{clinic.id}/palette", {"palette": "grayscale", "confirm": "true"}
    )
    assert ok.status_code == 303
    gray = PALETTES[ColorPalette.GRAYSCALE].hexes
    assert await _colors(db, doctors) == list(gray[:3])


async def test_pdf_reflects_the_new_colors(client, db, group, monkeypatch):
    from app.services import pdf as pdf_service

    clinic, doctors = group
    start = date(2027, 4, 1)
    days = {start + timedelta(days=i): doctors[i % 3].id for i in range(9)}
    generation = await scheduling_repo.create_generation(
        db, clinic.id, start, start + timedelta(days=8), doctors[0].id, days
    )
    await db.commit()
    await _palette(client, "blue")

    captured: list[str] = []
    monkeypatch.setattr(pdf_service, "html_to_pdf", lambda html: captured.append(html) or b"%PDF")
    await db.refresh(clinic)
    for doctor in doctors:
        await db.refresh(doctor)
    await pdf_service.build_schedule_pdf(db, clinic, generation, "Anne Admin")
    html = captured[0]
    assert all(f"background: {color}" in html for color in BLUE[:3])
    assert not any(f"background: {color}" in html for color in DOCTOR_PALETTE[:3])
    stored = (await db.scalars(select(Clinic.color_palette).where(Clinic.id == clinic.id))).one()
    assert stored == ColorPalette.BLUE
