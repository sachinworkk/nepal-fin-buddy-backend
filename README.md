# Nepal Fin Buddy — Backend

FastAPI + async SQLAlchemy + PostgreSQL (pgvector) backend for expenses, bills, documents, IRD prize-lottery submissions and stats.

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
| `mock-prize` | A mock of IRD's prize portal API, on port 8002 |
| `api` | Runs migrations, then serves the API on port 8000 |

To run the mock outside Docker:

```sh
uv run uvicorn mock_prize.app:app --port 8002
```

## IRD prize lottery (prize.ird.gov.np)

Customers submit their **cash bills** to IRD's taxpayer incentive lottery. The
portal API is public (no credentials) but every submission must carry a fresh
Cloudflare **Turnstile** token and a `form_started_at` timestamp. IRD's Turnstile
widget only works on IRD's own hostnames (error 110200 elsewhere), so there are
two modes, reported by `GET /prize/config` as `submission_mode`:

- `direct` (`PRIZE_MODE=test`, the local mock): the flow below.
- `handoff` (`PRIZE_MODE=live`): the app prefills, the user submits on the portal.
  1. `GET /prize/eligible` — confirmed bills that pass the portal's checks and
     aren't enrolled yet, with the fiscal year and `days_left` (null if the portal
     is unreachable).
  2. `POST /prize/enrollments/handoff` (`bill_id`, name, address, mobile) — returns
     the prefilled `fields` and `portal_url`; records a `handed_off` row
     (idempotent per bill; `409` if already submitted).
  3. `PATCH /prize/enrollments/{id}` with `{"status": "submitted",
     "prize_coupon_number": "..."}` once the user has finished on the portal.

  `POST /prize/enrollments` returns `409` in live mode.

Direct-mode frontend flow:

1. `GET /prize/config` — Turnstile sitekey + supported payment methods (`Cash`).
2. Render the Turnstile widget with the sitekey; record `form_started_at`
   (epoch ms) when the form opens.
3. `POST /prize/enrollments` with the bill fields, `turnstile_token` and
   `form_started_at`. Either send the raw fields or pass a `bill_id` of a
   confirmed bill — its PAN, bill number, date and amount prefill the request.
4. `GET /prize/enrollments` — the user's submission history (coupon numbers).

Backend behaviour:

- Validation mirrors the portal's ladder locally (PAN 9 digits, bill number
  `1–40` chars of letters/digits/`/.-_`, `YYYY-MM-DD` date, amount > 0,
  Nepali mobile `9[678]xxxxxxx`), so invalid input never burns a Turnstile token.
- `form_started_at` must be 5s–24h old (the portal rejects faster/older forms).
- Submissions by the same user are spaced out (`PRIZE_USER_SUBMIT_INTERVAL_SECONDS`)
  because the portal throttles bursts with a misleading `form expired` error.
- Duplicate bills (same user + PAN + bill number + date, already accepted) get `409`.
- Portal rejections pass through as `400` with the portal's message; transport/5xx
  errors become `502`. Failed attempts are still recorded with `last_error`.
- `PRIZE_MODE=test` (default) targets the local mock; `live` is refused unless
  `APP_ENV=production`.

`GET /prize/fiscal-years` proxies the portal's fiscal-year list (1h cache).

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
| `CORS_ORIGINS` | Allowed frontend origin(s) |
| `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL` | LLM provider access |
| `EXTRACTION_MODEL`, `CHAT_MODEL` | Model names used for extraction and chat |
| `EMBED_MODEL`, `EMBED_DIM` | Embedding model and dimension |
| `PRIZE_MODE` | `test` (mock portal) or `live` (only allowed when `APP_ENV=production`) |
| `PRIZE_MOCK_URL`, `PRIZE_LIVE_URL` | Prize portal endpoints |
| `PRIZE_TURNSTILE_SITEKEY` | Public sitekey the frontend renders the Turnstile widget with |
| `PRIZE_MIN_FORM_AGE_SECONDS`, `PRIZE_MAX_FORM_AGE_SECONDS` | Allowed age of `form_started_at` |
| `PRIZE_USER_SUBMIT_INTERVAL_SECONDS` | Minimum spacing between submissions per user |
| `STORAGE_DIR` | Where uploaded documents are saved |