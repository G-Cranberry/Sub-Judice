# Sub-Judice Backend (FastAPI) - v0.3

## Run
```bash
pip install -r requirements.txt
cp .env.example .env        # optional (env vars export karo ya .env tool use karo)
uvicorn app.main:app --reload
```
Docs: http://localhost:8000/docs  (Swagger me saare endpoints try kar sakte ho)

Postgres: `pip install psycopg2-binary` aur `DATABASE_URL=postgresql://user:pass@host:5432/subjudice`. Code change nahi lagega
(psycopg2 ya psycopg v3, jo installed ho, app khud pick karta hai). Test ho chuka: poori suite Postgres 16 par bhi pass.

**Purana `casetrace.db` hai (v0.1/v0.2)?** Ab bhi automatically use hota hai agar `subjudice.db` maujood nahi; seedha chalega - startup par 2 naye columns auto-add hote hain, purani custody chain verify hoti rahegi.

## Endpoints
| Method | Path | Kaam |
|---|---|---|
| POST | `/cases` | multipart/form-data: `title`, `filer_name`, `description`, `case_type`, `filer_type` (`individual`/`law_firm`), `claim_amount`, `deadline` (YYYY-MM-DD), optional `urgency_score`/`complexity_score` (0..1), `files` (multiple). Response me scores + `route` + `priority` + `route_reasons` |
| GET | `/cases`, `/cases/{id}` | List (filter: `route`, `status`; `limit` 1-200 (default 50), `offset`; total `X-Total-Count` header me) / detail |
| POST | `/cases/{id}/override` 🔒 | JSON: `new_route` (`DIRECT_TO_COURT`/`AI_REVIEW`), `reason`, `staff_name`. History `overrides[]` me |
| POST | `/custody` 🔒 | JSON: `case_id`, `evidence_id?`, `action` (auto UPPER_SNAKE), `handled_by`, `role?`, `notes?` |
| GET | `/custody?case_id=` | Hash-chained log. `role=case_officer` entries ka naam non-staff ko `[restricted]` dikhta hai |
| GET | `/custody/verify/{case_id}` | Chain tamper check **+** har original file ka SHA-256 dobara check (`evidence_intact`, `evidence_problems[]`). `first_broken_entry_id = -1` => latest entries delete hui |
| POST/GET/PATCH | `/hearings`, `/hearings/{id}` (POST/PATCH 🔒) | Schedule / list (`case_id`, `upcoming`, `limit`, `offset`) / update. Same court + same time par dusri SCHEDULED hearing => 409 |
| POST 🔒 / GET | `/hearings/{id}/drafts` | `doc_type`: `hearing_notice`, `summons`, `adjournment_application`, `order_sheet` |
| GET | `/health` | DB reachable bhi check karta hai |

🔒 = `X-Staff-Token` header (sirf jab `STAFF_TOKEN` env set ho; khali = dev mode).

Status codes: 401 token, 404 not found, 409 slot clash / concurrent update (retry), 413 file too big, 422 bad input.

## Routing (`app/routing.py`)
* Criminal => hamesha `DIRECT_TO_COURT`. Warna urgency **ya** complexity >= 0.7 => `DIRECT_TO_COURT`, nahi to `AI_REVIEW`.
* Scores client se aaye to wahi (`score_source`: `client` / `mixed` / `heuristic`). Agar rules ka route AI ke route se alag ho to `route_reasons` me note aata hai.
* Heuristic: word-boundary keywords (`bail` != `bailment`); ek **critical** term (injunction, eviction, domestic violence, ...) akela kaafi hai;
  deadline <= 7 din ya nikal chuki => court; constitutional/corporate => complex; claim value, evidence count, multiple parties.
* `priority` flag judge ke liye: `CRIMINAL` / `URGENT` / `COMPLEX` / `STANDARD`.
* **Limitation:** keyword rules ko wo cases nahi dikhte jo urgent hain par un me koi trigger word nahi (e.g. "dialysis stopped this morning").
  Uske liye Ollama/Qwen yahin plug karo - `score_case()` ka return shape same rakhna.

## Evidence & custody
* Upload: filename sanitised, extension allow-list, max size/files, empty file reject; fail hone par DB rollback + files delete.
* Custody hash = SHA-256 over JSON of (prev_hash, fields, timestamp). Concurrent writers ek hi case par serialise hote hain (DB lock),
  aur `UNIQUE(case_id, prev_hash)` fork ko DB level par rok deta hai.
* Hearing times: timezone-aware input UTC me **convert** hota hai; naive input UTC maana jata hai.

## Tests
```bash
pip install -r requirements-dev.txt
pytest                                   # 60 tests (SQLite)
DATABASE_URL=postgresql://user:pass@localhost/subjudice_test pytest   # same suite on Postgres (empty DB)
python -m eval.routing_eval              # routing accuracy on labelled fictional cases
python -m eval.holdout_eval              # hold-out set (new vs original logic)
```
Eval labels humne khud likhe hain (deck ke rule se, fictional cases) - ye "code deck ka rule follow karta hai" naapta hai, real court accuracy nahi.

## Shilpa ke contract ke liye
Field names/shapes sirf `app/schemas.py` aur `app/routers/cases.py` ke Form fields me hain. v0.1 se sab response fields waise hi hain;
naye fields (`priority`, verify ke extra fields) sirf **add** hue hain.


---
## v0.3 changes (what is new, what the frontend must send)

**Auth**
* `POST /cases` now also returns `access_token` (shown once). Frontend stores it and sends it as `X-Case-Token` on reads of that case:
  `GET /cases/{id}`, `GET /custody?case_id=`, `GET /custody/verify/{id}`, `GET /hearings?case_id=`, `GET /hearings/{id}[/drafts]`, evidence download.
* Staff send `X-Staff-Token`. Use `STAFF_TOKENS=name:token,...` so the audit log records the real person; the name comes from the token, not the request body.
* Listings (`GET /cases`, `GET /hearings` without `case_id`) are staff-only. Wrong/missing case token => **404** (not 403) so ids can't be probed.
* Reads are protected automatically once a staff token exists. `PROTECT_READS=0` restores the open v0.2 behaviour. No token configured = dev mode (open, with a startup warning).

**New endpoints**
| Method | Path | Notes |
|---|---|---|
| GET | `/cases/{id}/evidence/{eid}/download` | Re-hashes the file first (409 if modified, 410 if missing), serves the original, logs `EVIDENCE_DOWNLOADED` |
| PATCH | `/cases/{id}/status` 🔒 | `FILED/AI_REVIEW/SENT_TO_COURT/WITHDRAWN/CLOSED`; logged. Cases are deliberately **never deleted** (legal record) |
| GET | `/custody/anchor/{id}` | Chain head + HMAC signature. Save it OUTSIDE the database; later `GET /custody/verify/{id}?anchor_head=..&anchor_entries=..` returns `anchor_ok`, which catches a fully rebuilt chain |

**Routing**
* Rules now also catch health/life-safety terms (dialysis, surgery, ICU, ...), "locked out/homeless", and time-pressure words (tomorrow, this morning, turant, ...).
* Optional AI model: set `OLLAMA_URL` (+ `OLLAMA_MODEL`). Final score = **higher of rules and model** (model can't lower urgency); failure/timeout => rules only; `score_source` becomes `ai+rules`; prompt-injection text in a filing can't lower the route.

**Honest accuracy numbers** (our own fictional labels, not real court data)
| Set | Result |
|---|---|
| `eval.routing_eval` (47, used for tuning) | 100% |
| `eval.holdout_eval` (22, **now also used for tuning - regression only**) | 100% |
| `eval.holdout2_eval` (18, fresh, run once, not tuned) | **77.8%**, 4 urgent cases missed (pregnancy dismissal, Hinglish eviction, school entry refused, insulin supply) |

Rules alone top out around this level; the AI hook is meant to close that gap. To get a number that means something, label real cases and run `python -m eval.real_eval eval/real_labels.csv`.

**Contract for the frontend/AI teammates:** `docs/openapi.json` (also live at `/openapi.json`).
**Still true:** the hash chain *detects* tampering, it can't *prevent* it for someone with full DB access (use anchors). Drafts are fixed templates by design. Move evidence to object storage for production.

---
## Sources, bulk import and judgments (new)

**Nothing is hard-coded.** Cases come in through the API; the routing keywords live in `app/data/vocabulary.json`
(or your own file via `VOCAB_FILE`); the evaluation cases live in `eval/data/*.csv`. Edit those files, no Python needed.

### Who can file a case
| `source` | Who | Needs |
|---|---|---|
| `user` (default) | the public / a lawyer | nothing; gets an `access_token` back |
| `judiciary` | court staff | `X-Staff-Token` |
| `ai` | the AI intake service | `X-AI-Token` (env `AI_TOKEN`) or a staff token |

`POST /cases` (form, with files) takes `source` and optional `submitted_by`. Same validation, scoring and routing for every source;
the source is stored on the case, shown in `route_reasons` and written to the custody log.

| Method | Path | Notes |
|---|---|---|
| POST | `/cases/import?source=judiciary\|ai` | JSON `{"cases":[{title, filer_name, description, case_type, filer_type, claim_amount, deadline, urgency_score, complexity_score}, ...]}`, up to 200. Each row is handled on its own and the response says which succeeded, with `access_token`s. |
| POST | `/cases/{id}/evidence` | Add more files later (filer with `X-Case-Token`, staff, or AI). A failed upload never deletes older evidence. |

### Judgments: professional + simple, downloadable
A judgment has **two versions**: the professional text (authoritative) and a plain-language version (`language`: `en` or `hi`).

| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/cases/{id}/judgments` | staff | `title`, `professional_text`, optional `simple_text`, `language`, `hearing_id`. Saved as **DRAFT**. |
| PATCH | `/cases/{id}/judgments/{jid}` | staff | Edit while DRAFT. `resimplify=true` asks the AI to redraft the simple version. |
| POST | `/cases/{id}/judgments/{jid}/publish` | staff | Needs both versions. Sets case status `JUDGMENT_ISSUED`. Published judgments are **immutable**. |
| GET | `/cases/{id}/judgments[/{jid}]` | filer / staff | Filers see only PUBLISHED; drafts return 404 for them. |
| GET | `/cases/{id}/judgments/{jid}/download?version=professional\|simple\|both&format=txt\|html` | filer / staff | `html` is print-ready (browser Print -> Save as PDF) and handles Hindi. Verifies the stored fingerprint first (409 if altered). |

How the simple version is produced:
1. Staff paste/write it (`simple_source=provided`), **or**
2. if `OLLAMA_URL` is set and no simple text was sent, the AI drafts it (`simple_source=ai`). **An AI draft cannot be published until staff review it and publish with `{"reviewed_simple_version": true}`.**
3. If the AI is off/failing, or the judgment is longer than `SIMPLIFY_MAX_CHARS`, the simple version stays `pending` (never a truncated summary) and staff must write it before publishing.

Every simple download says the professional version is the one that counts. Create / edit / publish / download are all recorded in the hash-chained custody log (`JUDGMENT_CREATED`, `JUDGMENT_EDITED`, `JUDGMENT_PUBLISHED`, `JUDGMENT_DOWNLOADED`).

Not built: PDF/Word files (use the html download -> Print to PDF), digital signature of the judge, and e-filing integration with a real court system.
