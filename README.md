# MAHDI AI — Backend (mahdi-api)

FastAPI + PostgreSQL. REST + WebSocket. No API keys in the Android app; all AI keys live here as env vars.

## Endpoints (all under /v1, Bearer device token except register/health)
- `POST /v1/devices/register` → `{device_token}` (app calls once on first run)
- `GET /v1/health` → status, db, available providers, sandbox capabilities
- `POST /v1/chat` → `{message, style: egyptian|fusha, conversation_id?}` → `{reply, conversation_id, provider}`
- `WS  /v1/chat/ws?token=...` → send same JSON, receive `{type:"chunk"}*` then `{type:"done", conversation_id, provider}`
- `POST /v1/translate` → `{text, target: egyptian|fusha|en}` → `{translation, provider}`
- `POST /v1/code/run` → `{code, language:"python"}` → `{stdout, stderr, exit_code, timed_out, network_isolated}`
- `GET /v1/history`, `GET /v1/history/{id}`, `DELETE /v1/history/{id}`

## Env vars
| var | required | notes |
|---|---|---|
| `DATABASE_URL` | yes (prod) | Neon Postgres; tables are prefixed `mahdi_` and auto-created on startup. Safe to share the existing Neon DB with Chat Luck. |
| `GEMINI_API_KEY` | yes (today) | from vault entry "Gemini API key 2" |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | no | adapters already wired; they join the chain only when a key exists |
| `AI_PROVIDER_PRIORITY` | no | default `gemini,openai,anthropic`; fallback is in this order |
| `GEMINI_MODEL` / `OPENAI_MODEL` / `ANTHROPIC_MODEL` | no | defaults: gemini-flash-lite-latest, gpt-4o-mini, claude-3-5-haiku-20241022 |
| `CODE_RUN_TIMEOUT` / `CODE_RUN_MAX_MEMORY_MB` | no | defaults 10s / 256MB |

## Deploy on Render
`render.yaml` is included. New Web Service → name `mahdi-api`, root dir = this folder, plan free, health check `/v1/health`. Set the env vars above. If it shares the Chat Luck repo instead, set rootDir accordingly.

## Sandbox honesty
Code runs in a subprocess with CPU/memory/file-size limits and, when the host supports `unshare --net`, full network isolation (`/v1/health` reports `network_isolated`). This runs short code snippets, not full projects.

## Local dev & tests
```
pip install -r requirements.txt
pytest -q          # 29 tests, mock provider, sqlite
uvicorn app.main:app --reload
```

## Post-deploy verification
1. `GET /v1/health` → `providers` must contain `gemini` (proves the key is set).
2. Register a device, then `POST /v1/chat` `{"message":"اهلا"}` → real Gemini reply, `provider:"gemini"`.
