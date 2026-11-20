# AI (Shilpa + Aditya)
Prompts live in `ai/prompts/`. Model: Qwen via Ollama. OCR: Tesseract.

## Quick test of a prompt (Ollama must be running)
    ollama pull qwen2.5:7b
    ollama run qwen2.5:7b

Paste the system prompt, then a case from `docs/mock_data/cases.json`, and check the reply is valid JSON.
For the backend, call `POST http://localhost:11434/api/chat` with `"format": "json"` and `"stream": false`.
If your Qwen tag differs, change it everywhere (`ollama list` shows the exact name).

## Rules
- The model only SCORES and DRAFTS. The routing decision is made by backend code (see contract section 3).
- Always parse the reply as JSON. If parsing fails, retry once, then fall back to PHYSICAL_COURT.
