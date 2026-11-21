"""Judgments: professional + simple versions, draft -> publish, downloads."""
import pytest

from app import llm_scorer, simplify
from tests.helpers import STAFF, client, file_case

PRO = ("IN THE MATTER OF Amar Nath v. State Bank. Having heard the parties and perused the record, the Court holds "
       "that the respondent shall refund Rs. 20,000 with interest at 6% p.a. within 30 days of this order.")
SIMPLE = "The bank must give back Rs. 20,000 plus a little interest within 30 days."


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PROTECT_READS", "1")
    monkeypatch.delenv("STAFF_TOKENS", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)


def _case():
    b = file_case().json()
    return b["id"], {"X-Case-Token": b["access_token"]}


def _judgment(cid, **over):
    body = {"title": "Final judgment", "professional_text": PRO, "simple_text": SIMPLE, "issued_by": "Hon. Judge Rao"}
    body.update(over)
    return client.post(f"/cases/{cid}/judgments", json=body, headers=STAFF)


def test_only_staff_can_write_judgments():
    cid, tok = _case()
    body = {"title": "Final judgment", "professional_text": PRO, "simple_text": SIMPLE}
    assert client.post(f"/cases/{cid}/judgments", json=body).status_code == 401
    assert client.post(f"/cases/{cid}/judgments", json=body, headers=tok).status_code == 401


def test_draft_is_invisible_to_the_filer_until_published():
    cid, tok = _case()
    j = _judgment(cid).json()
    assert j["status"] == "DRAFT" and j["simple_source"] == "provided" and len(j["content_hash"]) == 64
    assert client.get(f"/cases/{cid}/judgments", headers=tok).json() == []
    assert client.get(f"/cases/{cid}/judgments/{j['id']}", headers=tok).status_code == 404
    assert client.get(f"/cases/{cid}/judgments/{j['id']}/download", headers=tok).status_code == 404
    assert len(client.get(f"/cases/{cid}/judgments", headers=STAFF).json()) == 1  # staff see drafts
    p = client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF)
    assert p.status_code == 200 and p.json()["status"] == "PUBLISHED" and p.json()["published_at"]
    assert client.get(f"/cases/{cid}", headers=tok).json()["status"] == "JUDGMENT_ISSUED"
    got = client.get(f"/cases/{cid}/judgments", headers=tok).json()
    assert len(got) == 1 and got[0]["professional_text"] == PRO and got[0]["simple_text"] == SIMPLE


def test_other_filers_cannot_see_the_judgment():
    cid, _ = _case()
    _, other = _case()
    j = _judgment(cid).json()
    client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF)
    assert client.get(f"/cases/{cid}/judgments", headers=other).status_code == 404


def test_both_versions_are_required_to_publish():
    cid, _ = _case()
    j = _judgment(cid, simple_text=None).json()  # no OLLAMA_URL -> nothing drafts it
    assert j["simple_source"] == "pending" and j["simple_text"] == ""
    assert client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF).status_code == 422
    r = client.patch(f"/cases/{cid}/judgments/{j['id']}", json={"simple_text": SIMPLE}, headers=STAFF)
    assert r.json()["simple_source"] == "provided"
    assert client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF).status_code == 200


def test_ai_drafted_simple_version_needs_human_review_before_publishing(monkeypatch):
    monkeypatch.setattr(llm_scorer, "call_model", lambda p, timeout=None: '{"simple_text": "AI says: refund in 30 days."}')
    cid, _ = _case()
    j = _judgment(cid, simple_text=None).json()
    assert j["simple_source"] == "ai" and j["simple_text"].startswith("AI says")
    url = f"/cases/{cid}/judgments/{j['id']}/publish"
    assert client.post(url, headers=STAFF).status_code == 422
    assert client.post(url, json={"reviewed_simple_version": False}, headers=STAFF).status_code == 422
    assert client.post(url, json={"reviewed_simple_version": True}, headers=STAFF).status_code == 200


def test_editing_the_ai_draft_by_hand_counts_as_human_written(monkeypatch):
    monkeypatch.setattr(llm_scorer, "call_model", lambda p, timeout=None: '{"simple_text": "AI draft"}')
    cid, _ = _case()
    j = _judgment(cid, simple_text=None).json()
    r = client.patch(f"/cases/{cid}/judgments/{j['id']}", json={"simple_text": SIMPLE}, headers=STAFF).json()
    assert r["simple_source"] == "provided"
    assert client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF).status_code == 200


def test_long_judgment_is_never_silently_truncated(monkeypatch):
    monkeypatch.setattr(simplify, "MAX_CHARS", 50)
    monkeypatch.setattr(llm_scorer, "call_model", lambda p, timeout=None: '{"simple_text": "should not be used"}')
    cid, _ = _case()
    j = _judgment(cid, simple_text=None).json()
    assert j["simple_source"] == "pending" and j["simple_text"] == ""


def test_model_failure_leaves_simple_version_pending(monkeypatch):
    monkeypatch.setattr(llm_scorer, "call_model", lambda p, timeout=None: None)
    cid, _ = _case()
    assert _judgment(cid, simple_text=None).json()["simple_source"] == "pending"
    monkeypatch.setattr(llm_scorer, "call_model", lambda p, timeout=None: "garbage")
    assert _judgment(cid, simple_text=None).json()["simple_source"] == "pending"


def test_published_judgment_is_immutable():
    cid, _ = _case()
    j = _judgment(cid).json()
    client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF)
    assert client.patch(f"/cases/{cid}/judgments/{j['id']}", json={"simple_text": "changed"}, headers=STAFF).status_code == 409
    assert client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF).status_code == 409


def test_download_professional_simple_both_txt_and_html():
    cid, tok = _case()
    j = _judgment(cid).json()
    client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF)
    base = f"/cases/{cid}/judgments/{j['id']}/download"
    both = client.get(base, headers=tok)
    assert both.status_code == 200 and "attachment" in both.headers["content-disposition"]
    assert both.headers["content-disposition"].count("SJ-") == 1 and ".txt" in both.headers["content-disposition"]
    t = both.text
    assert PRO in t and SIMPLE in t and "PROFESSIONAL VERSION" in t and "PLAIN-LANGUAGE" in t and j["content_hash"] in t
    pro = client.get(base, params={"version": "professional"}, headers=tok).text
    assert PRO in pro and SIMPLE not in pro
    sim = client.get(base, params={"version": "simple"}, headers=tok).text
    assert SIMPLE in sim and PRO not in sim and "professional version is the one that counts" in sim
    h = client.get(base, params={"format": "html"}, headers=tok)
    assert h.headers["content-type"].startswith("text/html") and "<!doctype html>" in h.text and SIMPLE in h.text
    assert client.get(base, params={"version": "nope"}, headers=tok).status_code == 422
    assert client.get(base).status_code == 404  # no token


def test_hindi_simple_version_downloads_correctly():
    cid, tok = _case()
    hi = "बैंक को 30 दिनों के भीतर 20,000 रुपये ब्याज सहित लौटाने होंगे।"
    j = _judgment(cid, simple_text=hi, language="hi").json()
    client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF)
    base = f"/cases/{cid}/judgments/{j['id']}/download"
    assert hi in client.get(base, params={"version": "simple"}, headers=tok).content.decode("utf-8")
    h = client.get(base, params={"version": "simple", "format": "html"}, headers=tok).content.decode("utf-8")
    assert hi in h and 'lang="hi"' in h and "सरल भाषा" in h


def test_html_download_escapes_markup():
    cid, tok = _case()
    evil = PRO + " <script>alert(1)</script>"
    j = _judgment(cid, professional_text=evil).json()
    client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF)
    h = client.get(f"/cases/{cid}/judgments/{j['id']}/download", params={"format": "html"}, headers=tok).text
    assert "<script>alert(1)</script>" not in h and "&lt;script&gt;" in h


def test_tampered_judgment_is_not_served():
    from app.database import SessionLocal
    from app.models import Judgment
    cid, tok = _case()
    j = _judgment(cid).json()
    client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF)
    with SessionLocal() as db:
        db.get(Judgment, j["id"]).professional_text = "The bank owes nothing."
        db.commit()
    assert client.get(f"/cases/{cid}/judgments/{j['id']}/download", headers=tok).status_code == 409


def test_every_step_is_in_the_tamper_evident_custody_log():
    cid, tok = _case()
    j = _judgment(cid).json()
    client.patch(f"/cases/{cid}/judgments/{j['id']}", json={"title": "Final judgment (corrected)"}, headers=STAFF)
    client.post(f"/cases/{cid}/judgments/{j['id']}/publish", headers=STAFF)
    client.get(f"/cases/{cid}/judgments/{j['id']}/download", headers=tok)
    actions = [e["action"] for e in client.get(f"/custody?case_id={cid}", headers=STAFF).json()]
    assert actions[-4:] == ["JUDGMENT_CREATED", "JUDGMENT_EDITED", "JUDGMENT_PUBLISHED", "JUDGMENT_DOWNLOADED"]
    assert client.get(f"/custody/verify/{cid}", headers=STAFF).json()["valid"] is True


def test_validation():
    cid, _ = _case()
    assert _judgment(cid, professional_text="too short").status_code == 422
    assert _judgment(cid, language="fr").status_code == 422
    assert _judgment(cid, hearing_id=9999).status_code == 404
    assert client.post("/cases/9999/judgments", json={"title": "abc", "professional_text": PRO}, headers=STAFF).status_code == 404
