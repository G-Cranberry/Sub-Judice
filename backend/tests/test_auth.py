"""v0.3: read protection, filer tokens, named staff, status change, evidence download, anchors."""
import pytest

from tests.helpers import STAFF, client, file_case


@pytest.fixture(autouse=True)
def protected(monkeypatch):
    monkeypatch.setenv("PROTECT_READS", "1")
    monkeypatch.delenv("STAFF_TOKENS", raising=False)


def _filed():
    b = file_case().json()
    return b["id"], {"X-Case-Token": b["access_token"]}, b


def test_create_returns_one_time_token_and_it_is_not_in_later_reads():
    cid, tok, b = _filed()
    assert len(b["access_token"]) >= 20
    got = client.get(f"/cases/{cid}", headers=tok).json()
    assert "access_token" not in got and "access_token_hash" not in got


def test_reads_blocked_without_token_and_404_not_403():
    cid, tok, _ = _filed()
    assert client.get(f"/cases/{cid}").status_code == 404
    assert client.get(f"/cases/{cid}", headers={"X-Case-Token": "wrong"}).status_code == 404
    assert client.get(f"/custody?case_id={cid}").status_code == 404
    assert client.get(f"/custody/verify/{cid}").status_code == 404
    assert client.get(f"/hearings?case_id={cid}").status_code == 404


def test_filer_token_reads_own_case_but_not_another():
    a, ta, _ = _filed()
    b, tb, _ = _filed()
    assert client.get(f"/cases/{a}", headers=ta).status_code == 200
    assert client.get(f"/cases/{b}", headers=ta).status_code == 404
    assert client.get(f"/custody?case_id={a}", headers=ta).status_code == 200
    assert client.get(f"/custody/verify/{a}", headers=ta).json()["valid"] is True


def test_staff_reads_everything_and_listings_are_staff_only():
    cid, _, _ = _filed()
    assert client.get(f"/cases/{cid}", headers=STAFF).status_code == 200
    assert client.get("/cases").status_code == 401
    assert client.get("/cases", headers=STAFF).status_code == 200
    assert client.get("/hearings").status_code == 401
    assert client.get("/hearings", headers=STAFF).status_code == 200


def test_reads_open_when_protection_off(monkeypatch):
    monkeypatch.setenv("PROTECT_READS", "0")
    cid, _, _ = _filed()
    assert client.get(f"/cases/{cid}").status_code == 200 and client.get("/cases").status_code == 200


def test_named_staff_tokens_are_recorded_in_audit_log(monkeypatch):
    monkeypatch.setenv("STAFF_TOKENS", "asha:tok-asha,ravi:tok-ravi")
    cid, _, _ = _filed()
    h = {"X-Staff-Token": "tok-asha"}
    r = client.post(f"/cases/{cid}/override", headers=h,
                    json={"new_route": "DIRECT_TO_COURT", "reason": "judge asked", "staff_name": "SOMEONE ELSE"})
    assert r.status_code == 200 and r.json()["routed_by"] == "asha"  # identity comes from the token, not the body
    log = client.get(f"/custody?case_id={cid}", headers=h).json()
    assert log[-1]["action"] == "ROUTE_OVERRIDDEN" and log[-1]["handled_by"] == "asha"
    assert client.get(f"/cases/{cid}", headers={"X-Staff-Token": "tok-ravi"}).status_code == 200
    assert client.get(f"/cases/{cid}", headers={"X-Staff-Token": "nope"}).status_code == 404


def test_status_change_logged_and_staff_only():
    cid, tok, _ = _filed()
    assert client.patch(f"/cases/{cid}/status", json={"status": "WITHDRAWN"}, headers=tok).status_code == 401
    r = client.patch(f"/cases/{cid}/status", json={"status": "WITHDRAWN", "reason": "settled", "updated_by": "Asha"}, headers=STAFF)
    assert r.status_code == 200 and r.json()["status"] == "WITHDRAWN"
    assert client.patch(f"/cases/{cid}/status", json={"status": "WITHDRAWN"}, headers=STAFF).status_code == 400
    assert client.patch(f"/cases/{cid}/status", json={"status": "DELETED"}, headers=STAFF).status_code == 422
    last = client.get(f"/custody?case_id={cid}", headers=STAFF).json()[-1]
    assert last["action"] == "STATUS_CHANGED" and "settled" in last["notes"]
    assert client.get(f"/custody/verify/{cid}", headers=STAFF).json()["valid"] is True


def test_evidence_download_serves_original_and_logs_access():
    cid, tok, b = _filed()
    eid = b["evidence"][0]["id"]
    r = client.get(f"/cases/{cid}/evidence/{eid}/download", headers=tok)
    assert r.status_code == 200 and r.content == b"withdrawal slip"
    assert client.get(f"/cases/{cid}/evidence/{eid}/download").status_code == 404  # no token
    assert client.get(f"/cases/{cid}/evidence/9999/download", headers=tok).status_code == 404
    log = client.get(f"/custody?case_id={cid}", headers=tok).json()
    assert log[-1]["action"] == "EVIDENCE_DOWNLOADED" and log[-1]["evidence_id"] == eid
    assert client.get(f"/custody/verify/{cid}", headers=tok).json()["valid"] is True


def test_download_refuses_a_modified_file():
    from app.database import SessionLocal
    from app.models import Evidence
    cid, tok, b = _filed()
    eid = b["evidence"][0]["id"]
    with SessionLocal() as db:
        path = db.get(Evidence, eid).stored_path
    open(path, "wb").write(b"tampered")
    assert client.get(f"/cases/{cid}/evidence/{eid}/download", headers=tok).status_code == 409


def test_anchor_detects_a_rebuilt_chain():
    from app.database import SessionLocal
    from app.models import CustodyEntry
    cid, tok, _ = _filed()
    a = client.get(f"/custody/anchor/{cid}", headers=tok).json()
    assert a["entries"] == 2 and len(a["head"]) == 64 and a["signature"]  # HMAC present (staff token configured)
    ok = client.get(f"/custody/verify/{cid}", params={"anchor_head": a["head"], "anchor_entries": a["entries"]}, headers=tok).json()
    assert ok["anchor_ok"] is True
    client.post("/custody", headers=STAFF, json={"case_id": cid, "action": "viewed", "handled_by": "X"})
    assert client.get(f"/custody/verify/{cid}", params={"anchor_head": a["head"], "anchor_entries": a["entries"]}, headers=tok).json()["anchor_ok"] is True  # still holds after growth
    with SessionLocal() as db:  # attacker rewrites entry 1 AND recomputes the chain hashes -> anchor still catches it
        from app.custody_chain import _hash
        es = db.query(CustodyEntry).filter_by(case_id=cid).order_by(CustodyEntry.id).all()
        es[0].notes = "rewritten"
        prev = es[0].prev_hash
        for e in es:
            e.prev_hash = prev
            e.entry_hash = _hash(prev, e.case_id, e.evidence_id, e.action, e.handled_by, e.role, e.notes, e.timestamp)
            prev = e.entry_hash
        db.commit()
    v = client.get(f"/custody/verify/{cid}", params={"anchor_head": a["head"], "anchor_entries": a["entries"]}, headers=tok).json()
    assert v["anchor_ok"] is False  # internal chain looks consistent, but it no longer matches the saved anchor
