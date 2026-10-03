from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, bills, chat, documents, expenses, invoices, ird, stats
from app.config import get_settings

app = FastAPI(title="Nepal Fin Buddy")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)
for r in (auth, expenses, bills, documents, invoices, stats, ird, chat):
    app.include_router(r.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
