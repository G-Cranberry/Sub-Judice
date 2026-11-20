# Task Board: Team Chaarisma, BriefPrism
The Builders Theory 1.0: 21 Nov 10:00 PM to 22 Nov 5:30 PM. Tick your boxes in this file and push.
Each block is independent. Build against `docs/mock_data/` first, the real backend comes later.

## Shilpa (Lead): Block 0, done BEFORE Sprint 1
- [x] Repo skeleton: /frontend /backend /ai /docs
- [x] API contract: `docs/API_CONTRACT.md`
- [x] DB schema: `docs/DB_SCHEMA.sql`
- [x] Mock data: `docs/mock_data/` (8 cases incl. the ATM case, 5 proposals, opinions, evidence, custody, hearings, users)
- [x] AI prompts: `ai/prompts/` (triage + resolution)
- [x] This task board
- [ ] Invite all 3 teammates as collaborators on GitHub
- [ ] Test both prompts once on Ollama and note the exact Qwen model tag in `ai/README.md`
- [ ] Post the WhatsApp plan and tell everyone to `git clone` before 10 PM
- [ ] Check the organisers rule on pre-written material (this block has no application code, only docs, schema, prompts, data)

## Aditya: Backend (`/backend`)
Done when the frontend can call every endpoint in the contract against real data.
- [ ] 1. FastAPI project, CORS for http://localhost:5173, SQLite from `docs/DB_SCHEMA.sql`
- [ ] 2. Seed script that loads `docs/mock_data/*.json` into the DB
- [ ] 3. `GET /cases`, `GET /cases/{id}`, `POST /cases` (with file upload)
- [ ] 4. `POST /cases/{id}/triage` using `ai/prompts/triage_prompt.md` + routing rule (contract section 3)
- [ ] 5. `POST /cases/{id}/override`
- [ ] 6. Evidence + custody: `POST /cases/{id}/evidence`, `GET/POST /evidence/{id}/custody`, `POST /evidence/{id}/attest` (custody table is insert-only; never return officer private identity)
- [ ] 7. Proposal, review, sign: `POST /cases/{id}/proposal`, `GET /cases/{id}/review`, `POST /cases/{id}/review/sign`
- [ ] 8. Opinions + comments endpoints
- [ ] 9. Hearings + `POST /cases/{id}/documents/draft`
- [ ] 10. (Level 2) Tesseract OCR on evidence upload, fill `ocr_text`
Tip: do steps 1 to 3 first. Himanish needs those the earliest.

## Himanish: Frontend core (`/frontend`)
Done when a filer can file a case, staff can see and override it, and a judge can sign a proposal.
- [ ] 1. Vite + React app, `src/api.js` with `USE_MOCK = true` reading `docs/mock_data/*.json`
- [ ] 2. Case filing form (title, type, summary, amount, deadline, file upload)
- [ ] 3. Court staff dashboard: urgent and normal queue, AI reasons, override button
- [ ] 4. Case detail page: triage scores, AI proposal, evidence list, custody timeline
- [ ] 5. Senior judge review screen: AI proposal + student/lawyer opinions side by side, approve / modify / reject
- [ ] 6. Switch `USE_MOCK = false` when Aditya is ready, fix any mismatch
- [ ] Share the first finished page with Vanshika so she can copy the style

## Vanshika: Simple pages (`/frontend/src/pages/simple/`)
Every page is separate and uses mock data only. No backend needed.
- [ ] 1. Home page: problem, solution, "Login as..." role buttons (roles from `users.json`)
- [ ] 2. Student forum: list opinions from `opinions.json`, comment box under each
- [ ] 3. Hearing calendar: table of `hearings.json`, sorted by date
- [ ] 4. Notary page: evidence list from `evidence.json`, Approve / Flag buttons (buttons can just change local state)
- [ ] 5. Demo support: screenshots of every page, collect them for the pitch deck
Stuck for more than 20 minutes? Ask Himanish. Do not wait silently.

## Level 2 (only if time): Aditya + Shilpa
- [ ] Ollama connected to backend for real triage + proposal
- [ ] Tesseract OCR running on uploaded evidence

## Schedule
| Time | What |
|---|---|
| Now to 21 Nov 10 PM | Shilpa finishes the unticked Block 0 items, everyone clones the repo |
| 10 PM to 12 AM | Registration, understand the challenge, 10-minute walkthrough of the API contract |
| 12 AM to 2:30 AM (Sprint 1) | Aditya, Himanish, Vanshika on their blocks |
| 3 AM to 5:30 AM (Level 1) | Keep building, one quick check-in |
| 5:30 to 6:30 AM (Mentoring 1) | Shilpa talks to mentors |
| 8:30 AM onwards (Sprint 2) | Connect frontend to backend, add Vanshika pages to the main app |
| 10:30 AM to 12:30 PM (Sprint 3) | Debug and polish, Level 2 items |
| 1 to 3 PM | Pitch prep |
| 3 to 4:30 PM | Pitch |

## 10-minute walkthrough script (for 10 PM to 12 AM)
1. Show the flow: file case, AI triage, route to court or AI review, judge signs.
2. Open `API_CONTRACT.md` section 1 (enums) and section 3 (routing rule). Everyone must use the exact strings.
3. Show `mock_data/cases.json`: C001 is the ATM demo case, C002 is the urgent one that goes to court.
4. Ports: backend 8000, frontend 5173.
5. Git rules: own folder only, `git pull --rebase` before push, never force push.
