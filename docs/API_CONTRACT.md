# API Contract (v1): single source of truth
Base URL `http://localhost:8000`. All bodies are JSON unless marked multipart. All times ISO-8601 (`2026-11-22T10:30:00`).
Errors always look like `{"error": "short_code", "message": "human text"}` with a proper HTTP status.
Auth is fake for the demo: `POST /auth/demo-login` returns a user; send `X-User-Id: <id>` on later calls (optional for the demo).

## 1. Enums (use these exact strings everywhere)
| Field | Values |
|---|---|
| `role` | `filer`, `court_staff`, `student`, `lawyer`, `judge`, `notary` |
| `case_type` | `consumer`, `contract`, `property`, `tenancy`, `criminal`, `family`, `banking`, `other` |
| `status` | `filed`, `triaged`, `routed_court`, `ai_review`, `under_review`, `resolved` |
| `route` | `PHYSICAL_COURT`, `AI_REVIEW` |
| `level` (urgency/complexity) | `LOW`, `MEDIUM`, `HIGH` |
| `hard_flags` | `criminal_offence`, `risk_to_life_or_safety`, `minor_involved`, `injunction_or_stay`, `property_title`, `multiple_parties` |
| `attestation_status` | `pending`, `approved`, `flagged` |
| `hearing_type` | `first_hearing`, `evidence`, `arguments`, `judgment` |

## 2. Endpoints
### Auth
- `POST /auth/demo-login` body `{"role": "judge"}` returns `{"user": {id, name, role}}` (picks the demo user of that role).
- `GET /users` returns `{"users": [...]}`

### Cases
- `POST /cases` (multipart: field `data` = JSON string, plus optional `files[]`)
  data: `{"title", "case_type", "summary", "claim_amount", "deadline", "claimant_name", "respondent_name"}`
  returns `201` `{"case": Case}` with `status: "filed"`. Backend then auto-runs triage (below) and returns the triaged case if it finishes in time.
- `GET /cases?status=&route=&urgency=` returns `{"cases": [Case]}` (staff queue; sort urgent first)
- `GET /cases/{id}` returns `{"case": Case, "evidence": [Evidence], "proposal": Proposal|null}`
- `POST /cases/{id}/triage` runs the AI triage prompt, stores scores, applies the routing rule. Returns `{"case": Case}`.
- `POST /cases/{id}/override` body `{"new_route": "PHYSICAL_COURT", "reason": "text", "by_user_id": "u2"}` returns `{"case": Case}` and logs who/why.

### AI proposal and review
- `POST /cases/{id}/proposal` generates the AI proposed resolution (only if `route == AI_REVIEW`). Returns `{"proposal": Proposal}`.
- `GET /cases/{id}/review` returns `{"case": Case, "proposal": Proposal, "opinions": [Opinion]}` (judge review screen, everything in one call)
- `POST /cases/{id}/review/sign` body `{"judge_id": "u5", "decision": "approve" | "modify" | "reject", "note": "text"}` returns `{"case": Case}`; `approve` sets `status: "resolved"`.

### Mentorship forum
- `GET /cases/{id}/opinions` returns `{"opinions": [Opinion]}`
- `POST /cases/{id}/opinions` body `{"author_id": "u3", "text": "..."}` returns `201` `{"opinion": Opinion}`
- `POST /opinions/{id}/comments` body `{"author_id": "u4", "text": "..."}` returns `201` `{"comment": Comment}`

### Evidence, custody, notary
- `POST /cases/{id}/evidence` (multipart: `file`, `description`, `handler_id`) returns `201` `{"evidence": Evidence}`; also writes the first custody log entry.
- `GET /evidence/{id}/custody` returns `{"entries": [CustodyEntry]}` (append-only, never edit or delete)
- `POST /evidence/{id}/custody` body `{"action": "transferred", "from_alias": "IO-0417", "to_alias": "LAB-02", "note": "..."}` returns `201` `{"entry": CustodyEntry}`
- `POST /evidence/{id}/attest` body `{"notary_id": "u6", "status": "approved" | "flagged", "note": "..."}` returns `{"evidence": Evidence}`
- Investigating officer real identity is NEVER returned by any endpoint, only `handler_alias` such as `IO-0417`.

### Hearings and paperwork
- `GET /hearings?from=&to=&case_id=` returns `{"hearings": [Hearing]}`
- `POST /hearings` body `{"case_id", "scheduled_at", "hearing_type", "room"}` returns `201` `{"hearing": Hearing}`
- `POST /cases/{id}/documents/draft` body `{"doc_type": "notice" | "summons" | "order_draft"}` returns `{"document": {"id", "doc_type", "content_text", "created_at"}}`

## 3. Routing rule (backend code, not the AI)
After triage, set `route = PHYSICAL_COURT` if ANY of these is true, else `AI_REVIEW`:
1. `urgency_score >= 70`
2. `complexity_score >= 65`
3. `hard_flags` is not empty (except `multiple_parties` alone, which does not force court)
4. `confidence < 0.6`
5. AI reply failed to parse after one retry (safe default)

Then `status = "routed_court"` or `"ai_review"`. Staff can override either way via `/override`.
Levels: score 0-39 `LOW`, 40-69 `MEDIUM`, 70-100 `HIGH`.

## 4. Object shapes
Case:
    {"id": "C001", "case_number": "BP/2026/0001", "title": "...", "case_type": "banking",
     "summary": "...", "claim_amount": 10000, "deadline": "2026-12-15", "filed_by": "u1",
     "claimant_name": "...", "respondent_name": "...", "filed_at": "2026-11-18T09:12:00",
     "status": "ai_review", "route": "AI_REVIEW",
     "triage": {"urgency_score": 22, "urgency_level": "LOW", "complexity_score": 18, "complexity_level": "LOW",
                "confidence": 0.91, "hard_flags": [], "reasons": ["..."]},
     "override": null}
`override` when set: `{"by_user_id": "u2", "from_route": "AI_REVIEW", "to_route": "PHYSICAL_COURT", "reason": "...", "at": "..."}`

Proposal:
    {"id": "P001", "case_id": "C001", "proposed_resolution": "...", "reasoning": ["..."],
     "suggested_relief": "...", "confidence": 0.82, "status": "pending_review",
     "disclaimer": "AI-generated draft. Not a judgment. Needs human review.", "created_at": "..."}
`status`: `pending_review`, `approved`, `modified`, `rejected`.

Opinion: `{"id": "O001", "case_id": "C001", "author_id": "u3", "author_name": "...", "author_role": "student", "text": "...", "created_at": "...", "comments": [Comment]}`
Comment: `{"id": "M001", "opinion_id": "O001", "author_id": "u4", "author_name": "...", "author_role": "lawyer", "text": "...", "created_at": "..."}`
Evidence: `{"id": "E001", "case_id": "C001", "description": "...", "file_name": "bank_statement.pdf", "file_url": "/files/E001", "handler_alias": "IO-0417", "attestation_status": "pending", "ocr_text": null, "created_at": "..."}`
CustodyEntry: `{"id": "L001", "evidence_id": "E001", "action": "collected" | "transferred" | "sealed" | "examined" | "attested", "from_alias": "...", "to_alias": "...", "note": "...", "at": "..."}`
Hearing: `{"id": "H001", "case_id": "C001", "case_number": "BP/2026/0001", "scheduled_at": "...", "hearing_type": "first_hearing", "room": "Court 3"}`
