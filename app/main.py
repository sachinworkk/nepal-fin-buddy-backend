from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    auth,
    bills,
    budgets,
    chat,
    dates,
    documents,
    expenses,
    lookups,
    prize,
    stats,
)
from app.config import get_settings
from app.observability import init_mlflow


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_mlflow()
    yield


app = FastAPI(title="Nepal Fin Buddy", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)
for r in (auth, expenses, bills, documents, stats, budgets, prize, chat, dates, lookups):
    app.include_router(r.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
