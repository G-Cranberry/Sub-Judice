"""Cases can come from the public, the judiciary or the AI service, one at a time or in bulk."""
import importlib
import json

import pytest

from tests.helpers import STAFF, client, file_case

AI = {"X-AI-Token": "ai-secret"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AI_TOKEN", "ai-secret")
    monkeypatch.delenv("STAFF_TOKENS", raising=False)


def test_public_user_needs_no_token_and_is_marked_user():
    b = file_case().json()
    assert b["source"] == "user" and b["submitted_by"] == "Amar Nath"


def test_judiciary_upload_needs_staff_token():
    assert file_case(source="judiciary").status_code == 401
    r = client.post(
        "/cases", data={"title": "Filed by court", "filer_name": "Ravi", "source": "judiciary"}, headers=STAFF)
    b = r.json()
    assert r.status_code == 201 and b["source"] == "judiciary" and b["submitted_by"] == "staff"
    assert any("submitted by judiciary" in x for x in b["route_reasons"])


def test_ai_upload_needs_ai_token_or_staff_token():
    data = {"title": "From intake bot", "filer_name": "Meena", "source": "ai"}
    assert client.post("/cases", data=data).status_code == 401
    assert client.post("/cases", data=data, headers={"X-AI-Token": "wrong"}).status_code == 401
    b = client.post("/cases", data=data, headers=AI).json()
    assert b["source"] == "ai" and b["submitted_by"] == "ai-service"
    b = client.post("/cases", data={**data, "submitted_by": "intake-bot v2"}, headers=AI).json()
    assert b["submitted_by"] == "intake-bot v2"
    assert client.post("/cases", data=data, headers=STAFF).status_code == 201


def test_unknown_source_is_422_and_source_is_in_the_custody_log():
    assert file_case(source="robot").status_code == 422
    cid = client.post("/cases", data={"title": "x y", "filer_name": "A", "source": "ai"}, headers=AI).json()["id"]
    first = client.get(f"/custody?case_id={cid}", headers=STAFF).json()[0]
    assert first["action"] == "CASE_FILED" and "source=ai" in first["notes"] and first["role"] == "ai"


def test_bulk_import_one_bad_row_does_not_stop_the_rest():
    body = {"cases": [
        {"title": "ATM double debit", "filer_name": "A", "case_type": "consumer", "claim_amount": 20000},
        {"title": "Bad filer type", "filer_name": "B", "filer_type": "company"},
        {"title": "Eviction notice", "filer_name": "C", "description": "Landlord served eviction tomorrow"},
    ]}
    assert client.post("/cases/import", json=body).status_code == 401  # public cannot import
    assert client.post("/cases/import?source=user", json=body, headers=STAFF).status_code == 422
    res = client.post("/cases/import?source=judiciary", json=body, headers=STAFF).json()
    assert [r["ok"] for r in res] == [True, False, True]
    assert res[0]["route"] == "AI_REVIEW" and res[2]["route"] == "DIRECT_TO_COURT"
    assert "filer_type" in res[1]["error"] and res[0]["access_token"] and res[0]["case_number"].startswith("SJ-")
    got = client.get(f"/cases/{res[2]['id']}", headers=STAFF).json()
    assert got["source"] == "judiciary"
    ai = client.post("/cases/import?source=ai", json=body, headers=AI).json()
    assert sum(r["ok"] for r in ai) == 2


def test_import_limits():
    assert client.post("/cases/import?source=ai", json={"cases": []}, headers=AI).status_code == 422


def test_add_evidence_later_by_filer_staff_and_ai(monkeypatch):
    monkeypatch.setenv("PROTECT_READS", "1")
    b = file_case().json()
    cid, tok = b["id"], {"X-Case-Token": b["access_token"]}
    f = lambda n, c=b"more evidence": [("files", (n, c, "text/plain"))]
    r = client.post(f"/cases/{cid}/evidence", files=f("extra.txt"), headers=tok)
    assert r.status_code == 201 and len(r.json()["evidence"]) == 2
    assert client.post(f"/cases/{cid}/evidence", files=f("x.txt")).status_code == 404  # no token
    assert client.post(f"/cases/{cid}/evidence", files=f("a.txt"), headers=AI).status_code == 201
    assert client.post(f"/cases/{cid}/evidence", files=f("b.txt"), headers=STAFF).status_code == 201
    assert client.post(f"/cases/{cid}/evidence", files=f("bad.exe"), headers=tok).status_code == 422
    v = client.get(f"/custody/verify/{cid}", headers=tok).json()
    assert v["valid"] and v["evidence_intact"] and v["evidence_checked"] == 4
    who = [e["handled_by"] for e in client.get(f"/custody?case_id={cid}", headers=STAFF).json() if e["action"] == "EVIDENCE_UPLOADED"]
    assert "ai-service" in who and "staff" in who


def test_failed_extra_upload_keeps_older_evidence(monkeypatch):
    monkeypatch.setenv("PROTECT_READS", "1")
    b = file_case().json()
    cid, tok = b["id"], {"X-Case-Token": b["access_token"]}
    big = [("files", ("big.txt", b"a" * (2 * 1024 * 1024), "text/plain"))]  # test limit is 1 MB
    assert client.post(f"/cases/{cid}/evidence", files=big, headers=tok).status_code == 413
    v = client.get(f"/custody/verify/{cid}", headers=tok).json()
    assert v["valid"] and v["evidence_intact"] and v["evidence_checked"] == 1  # original file untouched


def test_keyword_vocabulary_is_a_data_file(tmp_path, monkeypatch):
    from app import routing
    voc = json.load(open(routing.Path(routing.__file__).parent / "data" / "vocabulary.json", encoding="utf-8"))
    voc["critical_urgent"].append(["acid attack", r"\bacid attack\b"])
    f = tmp_path / "vocab.json"
    f.write_text(json.dumps(voc), encoding="utf-8")
    kw = dict(title="Help", description="Victim of an acid attack needs help", case_type="civil",
              claim_amount=None, deadline=None, evidence_count=0)
    assert routing.score_case(**kw)[0] < 0.7  # not a known term yet
    monkeypatch.setenv("VOCAB_FILE", str(f))
    try:
        importlib.reload(routing)
        assert routing.score_case(**kw)[0] >= 0.7  # now it is, with zero code changes
    finally:
        monkeypatch.delenv("VOCAB_FILE")
        importlib.reload(routing)
    assert routing.score_case(**kw)[0] < 0.7
