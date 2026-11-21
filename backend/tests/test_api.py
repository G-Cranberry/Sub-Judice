import pytest
from datetime import date, timedelta

from tests.helpers import STAFF, client, file_case


# ---------------------------------------------------------------- POST /cases
def test_small_civil_goes_to_ai_review():
    r = file_case()
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["route"] == "AI_REVIEW" and b["status"] == "AI_REVIEW" and b["priority"] == "STANDARD"
    assert len(b["evidence"]) == 1 and len(b["evidence"][0]["sha256"]) == 64
    assert b["route_reasons"] and b["case_number"].startswith("SJ-")


def test_urgent_deadline_goes_to_court_with_reasons():
    soon = (date.today() + timedelta(days=3)).isoformat()
    b = file_case(description="Urgent eviction notice", deadline=soon).json()
    assert b["route"] == "DIRECT_TO_COURT" and b["priority"] == "URGENT"
    assert any("Urgency" in r for r in b["route_reasons"])  # judge gets the reasons


def test_criminal_goes_to_court():
    b = file_case(case_type="Criminal Case").json()
    assert b["route"] == "DIRECT_TO_COURT" and b["case_type"] == "criminal" and b["priority"] == "CRIMINAL"


def test_client_scores_are_used_and_disagreement_is_flagged():
    b = file_case(urgency_score="0.95", complexity_score="0.2").json()
    assert b["route"] == "DIRECT_TO_COURT" and b["score_source"] == "client"
    # AI says "calm", but the text screams injunction -> keep AI's route, tell the reviewer
    b = file_case(description="need an injunction", urgency_score="0.1", complexity_score="0.1").json()
    assert b["route"] == "AI_REVIEW" and any("rule-based check would route" in r for r in b["route_reasons"])


def test_one_client_score_is_mixed():
    b = file_case(urgency_score="0.9").json()
    assert b["score_source"] == "mixed" and b["urgency_score"] == 0.9 and b["route"] == "DIRECT_TO_COURT"


def test_empty_optional_form_fields_ok():
    assert file_case(claim_amount="", deadline="").status_code == 201


def test_case_without_files_ok():
    r = file_case(files=[])
    assert r.status_code == 201 and r.json()["evidence"] == []


@pytest.mark.parametrize("over", [
    {"claim_amount": "-5"}, {"claim_amount": "nan"}, {"claim_amount": "inf"}, {"claim_amount": "abc"},
    {"deadline": "31-12-2030"}, {"urgency_score": "1.5"}, {"complexity_score": "-0.1"}, {"urgency_score": "nan"},
    {"title": "   "}, {"title": "x" * 301}, {"filer_name": ""}, {"filer_type": "company"},
])
def test_bad_input_is_422_not_500(over):
    assert file_case(**over).status_code == 422


# ---------------------------------------------------------------- uploads
def total_cases():
    return int(client.get("/cases", params={"limit": 1}).headers["X-Total-Count"])


def test_path_traversal_filename_is_neutralised():
    b = file_case(files=[("files", ("../../etc/passwd.txt", b"x", "text/plain"))]).json()
    assert "/" not in b["evidence"][0]["filename"] and ".." not in b["evidence"][0]["filename"]


def test_disallowed_extension_rejected_before_anything_is_saved():
    before = total_cases()
    r = file_case(files=[("files", ("virus.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 422 and total_cases() == before


def test_oversize_rejected_and_nothing_left_behind():
    import os
    from app.routers.cases import UPLOAD_DIR
    before = total_cases()
    dirs_before = set(os.listdir(UPLOAD_DIR)) if UPLOAD_DIR.exists() else set()
    r = file_case(files=[("files", ("big.txt", b"a" * (2 * 1024 * 1024), "text/plain"))])
    assert r.status_code == 413
    assert total_cases() == before                                     # DB rolled back
    assert (set(os.listdir(UPLOAD_DIR)) if UPLOAD_DIR.exists() else set()) == dirs_before  # no orphan dir/files


def test_empty_file_rejected():
    assert file_case(files=[("files", ("empty.txt", b"", "text/plain"))]).status_code == 422


def test_too_many_files_rejected():
    files = [("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(21)]
    assert file_case(files=files).status_code == 422


def test_multiple_files_get_individual_custody_entries():
    cid = file_case(files=[("files", (f"f{i}.txt", f"data{i}".encode(), "text/plain")) for i in range(3)]).json()["id"]
    log = client.get("/custody", params={"case_id": cid}).json()
    assert [e["action"] for e in log] == ["CASE_FILED"] + ["EVIDENCE_UPLOADED"] * 3


# ---------------------------------------------------------------- list / get
def test_pagination_filters_and_total_header():
    for _ in range(3):
        file_case()
    r = client.get("/cases", params={"limit": 2, "route": "AI_REVIEW"})
    assert r.status_code == 200 and len(r.json()) == 2 and int(r.headers["X-Total-Count"]) >= 3
    assert all(c["route"] == "AI_REVIEW" for c in r.json())
    assert client.get("/cases", params={"limit": 2, "offset": 2}).json()[0]["id"] < r.json()[-1]["id"]
    assert client.get("/cases", params={"route": "NOPE"}).status_code == 422
    assert client.get("/cases", params={"limit": 0}).status_code == 422
    assert client.get("/cases", params={"limit": 9999}).status_code == 422


def test_404s():
    assert client.get("/cases/99999").status_code == 404
    assert client.post("/cases/99999/override", json={"new_route": "AI_REVIEW", "reason": "xxx", "staff_name": "a"}, headers=STAFF).status_code == 404
    assert client.get("/custody/verify/99999").status_code == 404
    assert client.get("/hearings/99999").status_code == 404
    assert client.post("/hearings", json={"case_id": 99999, "scheduled_at": "2030-01-01T10:00:00"}, headers=STAFF).status_code == 404


# ---------------------------------------------------------------- override
def test_override_flow_and_auth():
    cid = file_case().json()["id"]
    body = {"new_route": "DIRECT_TO_COURT", "reason": "Witness at risk", "staff_name": "Clerk Sharma"}
    assert client.post(f"/cases/{cid}/override", json=body).status_code == 401
    assert client.post(f"/cases/{cid}/override", json=body, headers={"X-Staff-Token": "wrong"}).status_code == 401
    r = client.post(f"/cases/{cid}/override", json=body, headers=STAFF)
    assert r.status_code == 200
    j = r.json()
    assert j["route"] == "DIRECT_TO_COURT" and j["routed_by"] == "Clerk Sharma" and j["status"] == "SENT_TO_COURT"
    assert len(j["overrides"]) == 1 and j["overrides"][0]["previous_route"] == "AI_REVIEW"
    assert client.post(f"/cases/{cid}/override", json=body, headers=STAFF).status_code == 400  # already there
    # and back again; history keeps both
    back = {"new_route": "AI_REVIEW", "reason": "Not actually urgent", "staff_name": "Clerk Sharma"}
    assert len(client.post(f"/cases/{cid}/override", json=back, headers=STAFF).json()["overrides"]) == 2
    actions = [e["action"] for e in client.get("/custody", params={"case_id": cid}).json()]
    assert actions.count("ROUTE_OVERRIDDEN") == 2


def test_override_validation():
    cid = file_case().json()["id"]
    for bad in ({"new_route": "SOMEWHERE", "reason": "valid reason", "staff_name": "a"},
                {"new_route": "DIRECT_TO_COURT", "reason": "x", "staff_name": "a"},
                {"new_route": "DIRECT_TO_COURT", "reason": "valid reason", "staff_name": "   "}):
        assert client.post(f"/cases/{cid}/override", json=bad, headers=STAFF).status_code == 422


def test_health_checks_db():
    assert client.get("/health").json()["status"] == "ok"
