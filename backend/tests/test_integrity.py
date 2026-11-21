"""Custody chain, evidence integrity, concurrency, hearings."""
import os
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import STAFF, client, file_case


def add(cid, **over):
    body = {"case_id": cid, "action": "TRANSFERRED", "handled_by": "Officer A", "role": "case_officer"}
    body.update(over)
    return client.post("/custody", json=body, headers=STAFF)


# ---------------------------------------------------------------- custody
def test_custody_chain_and_verify():
    cid = file_case().json()["id"]
    assert add(cid).status_code == 201
    log = client.get("/custody", params={"case_id": cid}, headers=STAFF).json()
    assert [e["action"] for e in log] == ["CASE_FILED", "EVIDENCE_UPLOADED", "TRANSFERRED"]
    assert log[1]["prev_hash"] == log[0]["entry_hash"]
    v = client.get(f"/custody/verify/{cid}").json()
    assert v["valid"] and v["entries"] == 3 and v["evidence_intact"] and v["evidence_checked"] == 1


def test_custody_write_needs_staff_token():
    cid = file_case().json()["id"]
    assert client.post("/custody", json={"case_id": cid, "action": "x", "handled_by": "a"}).status_code == 401


def test_custody_action_normalised_and_validated():
    cid = file_case().json()["id"]
    assert add(cid, action="  handed over ").json()["action"] == "HANDED_OVER"
    assert add(cid, action="").status_code == 422
    assert add(cid, action="A" * 81).status_code == 422
    assert add(cid, evidence_id=999999).status_code == 404
    other = file_case().json()
    assert add(cid, evidence_id=other["evidence"][0]["id"]).status_code == 404  # evidence of another case


def test_officer_details_restricted_for_non_staff():
    cid = file_case().json()["id"]
    add(cid, handled_by="Officer Singh")
    public = client.get("/custody", params={"case_id": cid}).json()
    staff = client.get("/custody", params={"case_id": cid}, headers=STAFF).json()
    assert public[-1]["handled_by"] == "[restricted]" and staff[-1]["handled_by"] == "Officer Singh"
    assert public[0]["handled_by"] == "Amar Nath"          # filer is not restricted
    assert client.get(f"/custody/verify/{cid}").json()["valid"]  # hashes still verify


def test_pipe_in_fields_cannot_forge_collisions():
    cid = file_case().json()["id"]
    add(cid, handled_by="A|B", notes="x|y|z")
    assert client.get(f"/custody/verify/{cid}").json()["valid"]


def test_tampered_entry_detected():
    from app.database import SessionLocal
    from app.models import CustodyEntry
    cid = file_case().json()["id"]
    with SessionLocal() as db:
        e = db.query(CustodyEntry).filter_by(case_id=cid).first()
        e.handled_by = "Someone Else"
        db.commit()
        broken = e.id
    v = client.get(f"/custody/verify/{cid}").json()
    assert not v["valid"] and v["first_broken_entry_id"] == broken


def test_deleted_tail_entries_detected():
    from app.database import SessionLocal
    from app.models import CustodyEntry
    cid = file_case().json()["id"]
    add(cid)
    with SessionLocal() as db:
        last = db.query(CustodyEntry).filter_by(case_id=cid).order_by(CustodyEntry.id.desc()).first()
        db.delete(last)
        db.commit()
    v = client.get(f"/custody/verify/{cid}").json()
    assert not v["valid"] and v["first_broken_entry_id"] == -1


def test_modified_or_missing_evidence_file_detected():
    from app.database import SessionLocal
    from app.models import Evidence
    b = file_case(files=[("files", ("a.txt", b"original", "text/plain")), ("files", ("b.txt", b"second", "text/plain"))]).json()
    with SessionLocal() as db:
        evs = db.query(Evidence).filter_by(case_id=b["id"]).order_by(Evidence.id).all()
        open(evs[0].stored_path, "ab").write(b"tampered")
        os.remove(evs[1].stored_path)
        ids = [e.id for e in evs]
    v = client.get(f"/custody/verify/{b['id']}").json()
    assert v["valid"] and not v["evidence_intact"]   # log is fine; the FILES are not
    assert sorted((p["evidence_id"], p["problem"]) for p in v["evidence_problems"]) == [(ids[0], "HASH_MISMATCH"), (ids[1], "MISSING")]


# ---------------------------------------------------------------- concurrency
def test_concurrent_custody_appends_never_fork_the_chain():
    cid = file_case().json()["id"]

    def worker(i):
        with TestClient(app) as c:
            return c.post("/custody", json={"case_id": cid, "action": "VIEWED", "handled_by": f"user{i}"}, headers=STAFF).status_code

    with ThreadPoolExecutor(16) as ex:
        codes = list(ex.map(worker, range(32)))
    assert codes.count(201) == 32, codes
    v = client.get(f"/custody/verify/{cid}").json()
    assert v["valid"] and v["entries"] == 2 + 32


def test_concurrent_case_filing():
    def worker(i):
        with TestClient(app) as c:
            return file_case(c, title=f"case {i}").status_code

    with ThreadPoolExecutor(12) as ex:
        assert set(ex.map(worker, range(24))) == {201}


# ---------------------------------------------------------------- hearings
def hearing(cid, **over):
    body = {"case_id": cid, "scheduled_at": "2030-01-15T10:30:00", "court": "District Court Ambala", "purpose": "first hearing"}
    body.update(over)
    return client.post("/hearings", json=body, headers=STAFF)


def test_hearings_and_drafts():
    cid = file_case().json()["id"]
    r = hearing(cid)
    assert r.status_code == 201, r.text
    hid = r.json()["id"]
    for dt, marker in (("hearing_notice", "NOTICE OF HEARING"), ("summons", "SUMMONS"),
                       ("adjournment_application", "ADJOURNMENT"), ("order_sheet", "ORDER SHEET")):
        d = client.post(f"/hearings/{hid}/drafts", json={"doc_type": dt, "prepared_by": "Clerk"}, headers=STAFF)
        assert d.status_code == 201 and marker in d.json()["content"] and "DRAFT" in d.json()["content"]
    assert len(client.get(f"/hearings/{hid}/drafts").json()) == 4
    assert client.post(f"/hearings/{hid}/drafts", json={"doc_type": "bogus"}, headers=STAFF).status_code == 422
    assert client.patch(f"/hearings/{hid}", json={"status": "ADJOURNED"}, headers=STAFF).json()["status"] == "ADJOURNED"
    upcoming = client.get("/hearings", params={"case_id": cid, "upcoming": True}).json()
    assert hid not in [h["id"] for h in upcoming]           # adjourned => not "upcoming"
    assert client.get("/hearings", params={"case_id": cid}).json()[0]["id"] == hid


def test_hearing_writes_need_staff_but_reads_are_open():
    cid = file_case().json()["id"]
    assert client.post("/hearings", json={"case_id": cid, "scheduled_at": "2030-01-15T10:30:00"}).status_code == 401
    hid = hearing(cid, court="Court Z").json()["id"]
    assert client.patch(f"/hearings/{hid}", json={"notes": "x"}).status_code == 401
    assert client.post(f"/hearings/{hid}/drafts", json={"doc_type": "summons"}).status_code == 401
    assert client.get(f"/hearings/{hid}").status_code == 200


def test_timezone_aware_times_are_converted_not_dropped():
    cid = file_case().json()["id"]
    h = hearing(cid, scheduled_at="2030-03-01T10:30:00+05:30", court="TZ court").json()
    assert h["scheduled_at"].startswith("2030-03-01T05:00:00")   # 10:30 IST == 05:00 UTC


def test_double_booking_same_court_same_time_is_409():
    cid = file_case().json()["id"]
    assert hearing(cid, scheduled_at="2031-05-05T11:00:00", court="Court One").status_code == 201
    assert hearing(cid, scheduled_at="2031-05-05T11:00:00", court="COURT ONE").status_code == 409  # case-insensitive
    assert hearing(cid, scheduled_at="2031-05-05T11:00:00", court="Court Two").status_code == 201
    h2 = hearing(cid, scheduled_at="2031-05-05T12:00:00", court="Court One").json()
    assert client.patch(f"/hearings/{h2['id']}", json={"scheduled_at": "2031-05-05T11:00:00"}, headers=STAFF).status_code == 409


def test_cancelled_hearing_frees_the_slot():
    cid = file_case().json()["id"]
    h = hearing(cid, scheduled_at="2031-06-06T09:00:00", court="Court Free").json()
    client.patch(f"/hearings/{h['id']}", json={"status": "CANCELLED"}, headers=STAFF)
    assert hearing(cid, scheduled_at="2031-06-06T09:00:00", court="Court Free").status_code == 201


def test_patch_null_is_422_not_500():
    cid = file_case().json()["id"]
    hid = hearing(cid, court="Null court").json()["id"]
    assert client.patch(f"/hearings/{hid}", json={"scheduled_at": None}, headers=STAFF).status_code == 422
    assert client.patch(f"/hearings/{hid}", json={"status": None}, headers=STAFF).status_code == 422


def test_hearing_events_are_audited():
    cid = file_case().json()["id"]
    hid = hearing(cid, court="Audit court", scheduled_by="Clerk R").json()["id"]
    client.patch(f"/hearings/{hid}", json={"status": "COMPLETED", "updated_by": "Clerk R"}, headers=STAFF)
    actions = [e["action"] for e in client.get("/custody", params={"case_id": cid}, headers=STAFF).json()]
    assert "HEARING_SCHEDULED" in actions and "HEARING_UPDATED" in actions
    assert client.get(f"/custody/verify/{cid}").json()["valid"]
