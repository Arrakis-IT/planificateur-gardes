# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Phase 6: shared draft, save / cancel, live events, presence endpoints."""

import io
import json
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader
from sqlalchemy import func, select

from app.models import (
    Assignment,
    AssignmentDraft,
    AuditLog,
    DesiderataConflict,
    DesiderataDay,
    Generation,
    Holiday,
    OnsiteDay,
)
from app.repositories import scheduling as repo
from app.services.presence import PresentUser, hub
from tests.conftest import TEST_CSRF_COOKIE, login, post_form, with_csrf

pytestmark = pytest.mark.db

HTMX = {"HX-Request": "true"}
D1, D2, ONSITE_DAY = date(2027, 5, 3), date(2027, 5, 4), date(2027, 5, 5)


@pytest.fixture
async def team(client, db, make_clinic, make_user):
    """Clinic with 3 doctors and a May 2027 generation: odd days NR, even days DT."""
    clinic = await make_clinic("Cabinet du Parc")
    nr = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    dt = await make_user("dt@ex.lu", "Dominique Thill", clinic=clinic)
    mc = await make_user("mc@ex.lu", "Marie Curie", clinic=clinic)
    await repo.create_generation(
        db,
        clinic.id,
        date(2027, 5, 1),
        date(2027, 5, 31),
        nr.id,
        {date(2027, 5, d): (nr.id if d % 2 else dt.id) for d in range(1, 32)},
    )
    db.add(OnsiteDay(clinic_id=clinic.id, date=ONSITE_DAY))
    db.add(Holiday(user_id=mc.id, clinic_id=clinic.id, date=D1))
    await db.commit()
    await login(client, "nr@ex.lu")
    return clinic, nr, dt, mc


async def _second_client(app, email):
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    other.cookies.set("csrftoken", TEST_CSRF_COOKIE)
    await login(other, email)
    return other


async def _assignment(db, day):
    db.expunge_all()
    return await db.scalar(select(Assignment).where(Assignment.date == day))


async def _drafts(db):
    db.expunge_all()
    return (await db.scalars(select(AssignmentDraft).order_by(AssignmentDraft.date))).all()


def _listen(clinic):
    """A fake SSE connection to observe broadcasts."""
    return hub.connect(clinic.id, PresentUser(clinic.id, "ZZ", "Observer", "#000"))


def _events(connection):
    events = []
    while not connection.queue.empty():
        name, data = connection.queue.get_nowait()
        events.append((name, json.loads(data)))
    return events


# --- Modal ---------------------------------------------------------------------------------


async def test_modal_fragment_lists_doctors_and_vacations(client, db, team):
    response = await client.get(f"/resultats/jour/{D1}?retour=/mois?mois=2027-05", headers=HTMX)
    assert response.status_code == 200
    assert "<html" not in response.text and 'class="modal-backdrop"' in response.text
    assert "Marie Curie" in response.text and "en congés" in response.text  # MC away on D1
    assert 'value="XX"' in response.text
    assert 'name="retour" value="/mois?mois=2027-05"' in response.text
    assert "Jour sur place" not in response.text  # D1 is not on-site

    onsite = await client.get(f"/resultats/jour/{ONSITE_DAY}", headers=HTMX)
    assert "Jour sur place" in onsite.text and "Retirer" in onsite.text


async def test_modal_without_javascript_is_a_page(client, db, team):
    response = await client.get(f"/resultats/jour/{D1}")
    assert response.status_code == 200 and "<html" in response.text
    assert "Retour</a>" in response.text


async def test_modal_outside_generation_is_404(client, db, team):
    assert (await client.get("/resultats/jour/2027-06-01")).status_code == 404
    assert (await client.get("/resultats/jour/not-a-date")).status_code == 400


async def test_unsafe_return_target_is_ignored(client, db, team):
    response = await post_form(
        client, f"/resultats/jour/{D1}", {"doctor": "DT", "retour": "//evil.example"}
    )
    assert response.headers["location"] == "/resultats"


# --- Draft ---------------------------------------------------------------------------------


async def test_edit_goes_to_shared_draft_not_assignment(client, db, team):
    _, nr, dt, _ = team
    observer = _listen(team[0])
    response = await client.post(
        f"/resultats/jour/{D1}", data=with_csrf({"doctor": "DT"}), headers=HTMX
    )
    assert response.status_code == 204
    assert json.loads(response.headers["HX-Trigger"]) == {
        "closeModal": True,
        "planningChanged": True,
    }
    assert (await _assignment(db, D1)).user_id == nr.id  # saved value untouched
    (draft,) = await _drafts(db)
    assert (draft.date, draft.user_id, draft.edited_by) == (D1, dt.id, nr.id)
    ((name, payload),) = _events(observer)
    assert name == "draft_changed" and payload["count"] == 1
    assert payload["by"]["code"] == "NR" and payload["date"] == "2027-05-03"


async def test_draft_is_collective_and_visible_to_everyone(app, client, db, team):
    await post_form(client, f"/resultats/jour/{D1}", {"doctor": "MC"})
    dt_client = await _second_client(app, "dt@ex.lu")
    try:
        await post_form(dt_client, f"/resultats/jour/{D2}", {"doctor": "XX"})
        page = await dt_client.get("/mois?mois=2027-05")
    finally:
        await dt_client.aclose()
    assert len(await _drafts(db)) == 2  # one draft, two editors
    assert "2</strong> modifications non enregistrées" in page.text
    assert page.text.count(" drafted") == 2
    # Saved-only views ignore the draft.
    week = await client.get("/semaine?date=2027-05-03")
    assert "Marie Curie" not in week.text and "drafted" not in week.text


async def test_edit_back_to_saved_value_removes_draft_row(client, db, team):
    observer = _listen(team[0])
    await post_form(client, f"/resultats/jour/{D1}", {"doctor": "DT"})
    await post_form(client, f"/resultats/jour/{D1}", {"doctor": "NR"})
    assert await _drafts(db) == []
    assert [name for name, _ in _events(observer)] == ["draft_changed", "draft_cleared"]


async def test_invalid_doctor_rejected(client, db, team, make_clinic, make_user):
    other = await make_clinic("Autre")
    await make_user("bw@ex.lu", "Bob Weber", clinic=other)  # code BW exists elsewhere only
    for code in ("BW", "ZZ"):
        response = await post_form(client, f"/resultats/jour/{D1}", {"doctor": code})
        assert response.status_code == 400
    assert await _drafts(db) == []


async def test_onsite_toggle_in_results_only(client, db, team):
    response = await post_form(client, f"/resultats/jour/{ONSITE_DAY}/sur-place")
    assert response.status_code == 303
    (draft,) = await _drafts(db)
    assert draft.onsite_removed is True
    assert (await _assignment(db, ONSITE_DAY)).onsite_removed is False
    assert await db.scalar(select(func.count(OnsiteDay.id))) == 1  # settings untouched
    refused = await post_form(client, f"/resultats/jour/{D1}/sur-place")
    assert refused.status_code == 400


# --- Save / cancel ---------------------------------------------------------------------------


async def test_save_applies_draft_audits_each_cell_and_emails(client, db, mailer, team):
    clinic, nr, dt, _ = team
    await post_form(client, f"/resultats/jour/{D1}", {"doctor": "DT"})
    await post_form(client, f"/resultats/jour/{D2}", {"doctor": "XX"})
    await post_form(client, f"/resultats/jour/{ONSITE_DAY}/sur-place")
    observer = _listen(clinic)

    response = await post_form(client, "/resultats/enregistrer", {"retour": "/mois?mois=2027-05"})
    assert response.status_code == 303 and response.headers["location"] == "/mois?mois=2027-05"

    first = await _assignment(db, D1)
    second = await _assignment(db, D2)
    third = await _assignment(db, ONSITE_DAY)
    assert first.user_id == dt.id and first.edited_by == nr.id and first.edited_at
    assert second.user_id is None
    assert third.onsite_removed is True and third.user_id == nr.id
    assert await _drafts(db) == []

    entries = (
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.action.in_(["assignment_edited", "onsite_toggled_result"]))
            .order_by(AuditLog.id)
        )
    ).all()
    assert [(e.action, e.meta) for e in entries] == [
        ("assignment_edited", {"date": "2027-05-03", "old": "NR", "new": "DT"}),
        ("assignment_edited", {"date": "2027-05-04", "old": "DT", "new": "XX"}),
        ("onsite_toggled_result", {"date": "2027-05-05", "onsite_removed": True}),
    ]

    ((name, payload),) = _events(observer)
    assert name == "draft_cleared" and payload["reason"] == "saved" and payload["changed"] == 3

    assert sorted(m.to for m in mailer.sent) == ["dt@ex.lu", "mc@ex.lu", "nr@ex.lu"]
    assert "Nicolas Rousseau vient de modifier la planification" in mailer.sent[0].text
    pdf_text = PdfReader(io.BytesIO(mailer.sent[0].attachments[0].content)).pages[0].extract_text()
    assert "Modifié par Nicolas Rousseau" in pdf_text and "Généré par" not in pdf_text
    sent = await db.scalar(select(AuditLog).where(AuditLog.action == "pdf_sent"))
    assert sent.meta["action"] == "edited" and sent.meta["sent"] == 3


async def test_save_without_changes_sends_nothing(client, db, mailer, team):
    response = await post_form(client, "/resultats/enregistrer")
    assert response.status_code == 303
    assert mailer.sent == []
    assert "Aucune modification" in (await client.get("/resultats")).text


async def test_cancel_discards_draft_for_everyone(client, db, mailer, team):
    clinic, nr, *_ = team
    await post_form(client, f"/resultats/jour/{D1}", {"doctor": "DT"})
    observer = _listen(clinic)
    response = await post_form(client, "/resultats/annuler")
    assert response.status_code == 303
    assert await _drafts(db) == []
    assert (await _assignment(db, D1)).user_id == nr.id
    ((name, payload),) = _events(observer)
    assert name == "draft_cleared" and payload["reason"] == "cancelled"
    assert await db.scalar(select(AuditLog).where(AuditLog.action == "draft_discarded"))
    assert mailer.sent == []


async def test_regenerating_drops_the_draft(client, db, team):
    clinic = team[0]
    await post_form(client, f"/resultats/jour/{D1}", {"doctor": "DT"})
    observer = _listen(clinic)
    await post_form(
        client,
        "/generer",
        {"period_from": "2027-05-01", "period_to": "2027-05-31", "overlap_mode": "recalc"},
    )
    assert await _drafts(db) == []
    assert ("draft_cleared", "regenerated") in [(n, p.get("reason")) for n, p in _events(observer)]


async def test_results_stats_include_draft(client, db, team):
    await post_form(client, f"/resultats/jour/{D2}", {"doctor": "MC"})
    page = await client.get("/resultats")
    assert "avec les modifications non enregistrées" in page.text


# --- Presence endpoints ---------------------------------------------------------------------


async def test_heartbeat_marks_user_present(client, db, team):
    clinic = team[0]
    response = await client.post(
        "/sse/heartbeat", headers={"X-CSRF-Token": with_csrf()["csrf_token"]}
    )
    assert response.status_code == 204
    assert [u.code for u in hub.present_users(clinic.id)] == ["NR"]


async def test_heartbeat_requires_csrf_and_login(app, client, db, team):
    no_csrf = await client.post("/sse/heartbeat")
    assert no_csrf.status_code == 403
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as anon:
        anon.cookies.set("csrftoken", TEST_CSRF_COOKIE)
        no_login = await anon.post(
            "/sse/heartbeat", headers={"X-CSRF-Token": with_csrf()["csrf_token"]}
        )
        assert no_login.status_code == 303
    assert hub.present_users(team[0].id) == []


async def test_sse_stream_requires_clinic_user(app, db, make_user):
    await make_user("admin@ex.lu", "Juan Admin", is_superadmin=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as anon:
        assert (await anon.get("/sse/presence")).status_code == 401
        anon.cookies.set("csrftoken", TEST_CSRF_COOKIE)
        await login(anon, "admin@ex.lu")
        assert (await anon.get("/sse/presence")).status_code == 403


# --- Isolation --------------------------------------------------------------------------------


async def test_edits_never_reach_another_clinic(client, db, team, make_clinic, make_user):
    other = await make_clinic("Autre Cabinet")
    bob = await make_user("bob@ex.lu", "Bob Weber", clinic=other)
    other_generation = await repo.create_generation(
        db,
        other.id,
        date(2027, 6, 1),
        date(2027, 6, 30),
        bob.id,
        {date(2027, 6, d): bob.id for d in range(1, 31)},
    )
    db.add(
        AssignmentDraft(
            clinic_id=other.id, generation_id=other_generation.id, date=date(2027, 6, 2)
        )
    )
    await db.commit()
    other_listener = _listen(other)

    # A date only covered by B's generation does not exist for A.
    assert (await client.get("/resultats/jour/2027-06-02")).status_code == 404
    refused = await post_form(client, "/resultats/jour/2027-06-02", {"doctor": "NR"})
    assert refused.status_code == 404
    # A's save / cancel leave B's draft alone and B hears nothing.
    await post_form(client, f"/resultats/jour/{D1}", {"doctor": "DT"})
    await post_form(client, "/resultats/annuler")
    await post_form(client, "/resultats/enregistrer")
    remaining = await _drafts(db)
    assert [(d.clinic_id, d.date) for d in remaining] == [(other.id, date(2027, 6, 2))]
    assert _events(other_listener) == []
    generations = (await db.scalars(select(Generation))).all()
    assert all(g.is_active for g in generations)


# --- Desiderata in the modal and on save ----------------------------------------------------

DESIDERATA_WARNING = "Ce médecin a demandé à ne pas être de garde ce jour (desiderata)."


async def test_modal_warns_like_vacations_for_desiderata(client, db, team):
    clinic, _, dt, _ = team
    db.add(DesiderataDay(user_id=dt.id, clinic_id=clinic.id, date=D1))
    await db.commit()
    modal = (await client.get(f"/resultats/jour/{D1}", headers=HTMX)).text
    dt_option = modal.split('value="DT"')[1].split("</button>")[0]
    assert f'<span class="opt-tag">{DESIDERATA_WARNING}</span>' in dt_option
    mc_option = modal.split('value="MC"')[1].split("</button>")[0]
    assert '<span class="opt-tag">en congés</span>' in mc_option  # same badge, same place
    assert modal.count(DESIDERATA_WARNING) == 1
    other_day = (await client.get(f"/resultats/jour/{D2}", headers=HTMX)).text
    assert DESIDERATA_WARNING not in other_day


async def test_manual_assignment_despite_desiderata_is_flagged(client, db, team):
    clinic, _, dt, _ = team
    db.add(DesiderataDay(user_id=dt.id, clinic_id=clinic.id, date=D1))
    await db.commit()
    await post_form(client, f"/resultats/jour/{D1}", {"doctor": "DT"})
    preview = (await client.get("/mois?mois=2027-05")).text
    assert (
        "Dominique Thill avait demandé à ne pas être de garde ce jour (desiderata). "
        "Affectation manuelle." in preview
    )  # drafted cell, before saving

    await post_form(client, "/resultats/enregistrer", {"retour": "/resultats"})
    cell = await _assignment(db, D1)
    assert cell.user_id == dt.id and cell.desiderata_conflict == DesiderataConflict.MANUAL
    meta = await db.scalar(select(AuditLog.meta).where(AuditLog.action == "assignment_edited"))
    assert meta["desiderata"] is True
    results = (await client.get("/resultats")).text
    assert "Conflits avec desiderata" in results and "(affectation manuelle)" in results


async def test_reassigning_a_conflict_clears_the_flag(client, db, team):
    _, nr, _, _ = team
    cell = await _assignment(db, D2)
    cell.desiderata_conflict = DesiderataConflict.GENERATED
    await db.commit()
    await post_form(client, f"/resultats/jour/{D2}", {"doctor": "NR"})
    await post_form(client, "/resultats/enregistrer", {"retour": "/resultats"})
    await db.refresh(cell)
    assert cell.user_id == nr.id and cell.desiderata_conflict is None
    assert "Conflits avec desiderata" not in (await client.get("/resultats")).text
