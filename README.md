# Nepal Fin Buddy — Backend

FastAPI + async SQLAlchemy + PostgreSQL (pgvector) backend for expenses, bills, documents, invoices, IRD credentials and stats.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker (for PostgreSQL with pgvector)

## Run locally

1. **Install dependencies**

   ```sh
   uv sync
   ```

2. **Configure environment**

   ```sh
   cp .env.example .env
   ```

   Then edit `.env`:

   - `JWT_SECRET` — set to a long random string.
   - `FIELD_ENCRYPTION_KEY` — required; generate with:

     ```sh
     uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```

   - `OPENROUTER_API_KEY` — only needed for LLM features (extraction/chat).

   The default `DATABASE_URL` points at `localhost:5433`, matching the compose file.

3. **Start the database**

   ```sh
   docker compose up -d db
   ```

4. **Apply migrations**

   ```sh
   uv run alembic upgrade head
   ```

5. **Start the API**

   ```sh
   uv run uvicorn app.main:app --reload --port 8000
   ```

   - Health check: <http://localhost:8000/health>
   - Interactive docs: <http://localhost:8000/docs>

Uploaded files are stored in `STORAGE_DIR` (default `./storage`).

## Run with Docker

```sh
docker compose up --build
```

Make sure `.env` exists first (see step 2 above). This starts:

| Service | What it does |
| --- | --- |
| `db` | Postgres + pgvector, on host port 5433 |
| `api` | Runs migrations, then serves the API on port 8000 |
| `mock-cbms` | A mock of IRD's CBMS invoice API, on port 8001 |
| `worker` | Submits issued invoices to CBMS in the background |

To run the worker or the mock outside Docker:

```sh
uv run uvicorn mock_cbms.app:app --port 8001
uv run python -m app.worker.sync
```

## CBMS invoice sync

Creating an invoice (`POST /invoices`) queues it for submission to CBMS. The worker picks it up within `WORKER_POLL_SECONDS`, and `GET /invoices/{id}` shows the result under `sync`.

1. Save IRD credentials with `PUT /settings/ird-credentials`. The mock accepts the sandbox pair `Test_CBMS` / `test@321` (override with `CBMS_SANDBOX_USERNAME` / `CBMS_SANDBOX_PASSWORD` on the mock), with any 9-digit `seller_pan`.
2. Create an invoice. Within a few seconds its `sync.status` goes from `pending` to `synced`.

How CBMS response codes are handled:

| Code | Meaning | Result |
| --- | --- | --- |
| 200 | Saved | `synced` |
| 101 | Already exists | `synced` (makes resubmission safe) |
| 100 | Bad credentials | `failed`; fix credentials, then `POST /invoices/{id}/resync` |
| 102 | Could not save (e.g. PAN not 9 digits) | `failed`; resync after fixing |
| 104 | Invalid data | `failed`; resync after fixing |
| 105 | Credit note's original invoice not found at IRD | retried (IRD may be lagging), then `failed` |
| 103, network error, 5xx | Temporary | retried with exponential backoff (base `CBMS_BACKOFF_BASE_SECONDS`), then `failed` after 6 attempts |

A credit note waits until its original invoice is synced. Error messages are returned in English (`last_error`) and Nepali (`last_error_ne`).

To trigger each path against the mock, use these buyer names: `FORCE_103`, `FORCE_104`, or `FLAKY_103` (103 once, then success).

## Tests and linting

```sh
uv run pytest        # live LLM tests are skipped by default
uv run pytest -m live_llm   # requires OPENROUTER_API_KEY
uv run ruff check .
```

Integration tests need the Postgres container from step 3 running.

## Configuration reference

| Variable | Purpose |
| --- | --- |
| `APP_ENV` | `development` or `production` |
| `DATABASE_URL` | Async Postgres URL (`postgresql+asyncpg://...`) |
| `JWT_SECRET` | Secret for signing auth tokens |
| `FIELD_ENCRYPTION_KEY` | Fernet key for encrypting IRD credentials |
| `CORS_ORIGINS` | Allowed frontend origin(s) |
| `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL` | LLM provider access |
| `EXTRACTION_MODEL`, `CHAT_MODEL` | Model names used for extraction and chat |
| `EMBED_MODEL`, `EMBED_DIM` | Embedding model and dimension |
| `CBMS_MODE` | `test` (mock CBMS) or `live` (only allowed when `APP_ENV=production`) |
| `CBMS_MOCK_URL`, `CBMS_LIVE_URL` | CBMS endpoints |
| `CBMS_BACKOFF_BASE_SECONDS`, `CBMS_BACKOFF_CAP_SECONDS` | Retry delay for temporary CBMS failures (default 30s, capped at 1h) |
| `WORKER_POLL_SECONDS` | How often the worker checks the queue (default 5s) |
| `STORAGE_DIR` | Where uploaded documents are saved |
