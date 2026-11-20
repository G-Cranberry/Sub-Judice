# BriefPrism (Sub Judice)
AI case triage and review for courts. Team Chaarisma, The Builders Theory 1.0.

## Repo layout
| Folder | Owner | What goes here |
|---|---|---|
| `/backend` | Aditya | FastAPI server, DB, routing logic |
| `/frontend` | Himanish + Vanshika | React app (Himanish: core pages, Vanshika: `src/pages/simple/`) |
| `/ai` | Shilpa + Aditya | Prompts, Ollama/Qwen + Tesseract glue |
| `/docs` | Shilpa | API contract, DB schema, mock data, task board |

## Read in this order
1. `docs/TASK_BOARD.md` : what YOU have to do
2. `docs/API_CONTRACT.md` : the single source of truth for endpoints and JSON
3. `docs/mock_data/` : fake data to build against before the backend exists

## Stack
React, FastAPI, PostgreSQL (SQLite first), Tesseract OCR, Ollama with Qwen.

## Ground rules
- Backend runs on `http://localhost:8000`, frontend on `http://localhost:5173`. Backend must allow CORS for 5173.
- Work only inside your own folder. Run `git pull --rebase` before every push. Never force push.
- Commit small and often. Message format: `[area] what you did`, e.g. `[frontend] filing form`.
- Do not change the API contract alone. Tell Shilpa on WhatsApp, she updates `docs/API_CONTRACT.md`.
- All names, cases and numbers are fictional demo data.
