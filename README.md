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
- `PRIZE_MODE=test` (default) targets the local mock; `live` targets the real portal
  from any `APP_ENV`.

`GET /prize/fiscal-years` proxies the portal's fiscal-year list (1h cache).

## Chat assistant

The assistant (`POST /chat/sessions/{id}/messages`) is a [Strands Agents](https://strandsagents.com/)
agent over OpenRouter. It answers from the user's expenses and documents, and can
record, edit and delete expenses.

- **Memory is per chat session.** Strands snapshots the whole conversation, tool
  calls and results included, into the `agent_storage` table under the chat
  session's id and restores it on the next turn. The last 40 messages stay in the model's
  context. Nothing carries over between chat sessions, and deleting a session
  deletes its memory.
- **Creates** run straight away (`source: "chat"`).
- **Edits and deletes need confirmation.** The reply comes back with
  `pending_actions`. Each pending action has an `interrupt_id`, an `action`
  (`update`/`delete`), the `expense` as it is now and, for updates, the
  `changes`. Show a Confirm/Cancel card, then call:

  ```
  POST /chat/sessions/{id}/actions
  {"decisions": [{"interrupt_id": "...", "decision": "confirm" | "cancel"}]}
  ```

  The response is the assistant's follow-up message. Until the user decides,
  new messages in that session get `409`. A decision must cover every pending action.
  Once decided, the action's `decision` field is filled in on the original message.

## Budgets

A budget is a monthly spending limit for one category, checked against the current
Bikram Sambat month (there is one budget per user and category, no history or rollover).

- `GET /budgets` — the user's budgets, each with `amount`, `spent` this BS month, `remaining`
  (negative when over), `percent` and `status`: `ok`, `warning` (80% or more used) or `over`
  (more than 100%). Most-used first.
- `PUT /budgets/{category}` with `{"amount": "8000"}` — create or replace a budget (amount > 0,
  at most 2 decimals).
- `DELETE /budgets/{category}` — remove one (`404` if none).

The chat assistant has `get_budgets` and `set_budget` tools (setting runs straight away, no
confirmation; deleting is Dashboard/API only). The Dashboard shows a "Monthly budgets" card
with progress bars and an editor.

## Tests and linting

```sh
uv run pytest        # live LLM tests are skipped by default
uv run pytest -m live_llm   # requires OPENROUTER_API_KEY
uv run ruff check .
```

Integration tests need the Postgres container from step 3 running.

### Live LLM evals

`tests/eval` holds the RAG and bill-extraction evals. They call real models (needs
`OPENROUTER_API_KEY`) and are slow, since each case also makes LLM-judge calls:

```sh
uv run pytest tests/eval -m live_llm -q
EVAL_SMALL=1 uv run pytest tests/eval -m live_llm -q   # quick run: 5 documents, 18 questions
uv run pytest tests/eval/test_rag_eval.py -m live_llm -k TestRetrievalQuality -x -v   # one class, stop at first failure
```

Use `-v` to see test names while it runs; with `-q` failures only show as `F`. For tracing
the app's LLM calls and chat turns, see [MLflow monitoring](#mlflow-monitoring).

Results, known failures and the extraction benchmark number are logged in
[docs/eval-results.md](docs/eval-results.md).

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
| `PRIZE_MODE` | `test` (mock portal) or `live` (real portal) |
| `PRIZE_MOCK_URL`, `PRIZE_LIVE_URL` | Prize portal endpoints |
| `PRIZE_TURNSTILE_SITEKEY` | Public sitekey the frontend renders the Turnstile widget with |
| `PRIZE_MIN_FORM_AGE_SECONDS`, `PRIZE_MAX_FORM_AGE_SECONDS` | Allowed age of `form_started_at` |
| `PRIZE_USER_SUBMIT_INTERVAL_SECONDS` | Minimum spacing between submissions per user |
| `STORAGE_DIR` | Where uploaded documents are saved |
| `MLFLOW_ENABLED`, `MLFLOW_TRACKING_URI`, `MLFLOW_EXPERIMENT` | Optional MLflow tracing (see [MLflow monitoring](#mlflow-monitoring)) |
| `MLFLOW_LOG_CONTENT` | Also log chat query and answer text on `chat.turn` traces (default `false`) |

## MLflow monitoring

Optional tracing of LLM calls, chat turns (retrieve, rerank, tool rounds) and bill extraction
quality. Metadata only (model, tokens, latency, counts, scores); prompts and answers are never
logged unless `MLFLOW_LOG_CONTENT=true`, which records the chat query and answer on the
`chat.turn` trace. Off by default.

### Run MLflow

1. **Enable tracing** in `.env` (the `mlflow` package with the UI is in the dev dependency
   group, so `uv sync` already installed it):

   ```
   MLFLOW_ENABLED=true
   MLFLOW_TRACKING_URI=sqlite:///mlflow.db
   MLFLOW_EXPERIMENT=nepal-fin-buddy
   ```

2. **Start the API** as usual (see [Run locally](#run-locally)) and use the app: send chat
   messages or scan a bill. Traces are written to `mlflow.db` in the project root.

3. **Start the MLflow UI** in a second terminal, from the project root:

   ```sh
   uv run mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5000
   ```

4. Open <http://localhost:5000>, pick the `nepal-fin-buddy` experiment and use the **Traces**
   tab to inspect `chat.turn` traces and bill-extraction quality scores.

The tracking URI must be the same for the API and the UI, otherwise the UI shows no data.
MLflow errors are logged as warnings and never fail a request.
