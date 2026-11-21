import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import models  # noqa: F401  (tables register hone ke liye)
from .database import Base, SessionLocal, engine
from .migrate import upgrade
from .routers import cases, custody, hearings, judgments
from .security import auth_configured


upgrade(engine)  # v0.1 databases: add the new columns before anything reads them
Base.metadata.create_all(bind=engine)  # idempotent; at import so it also works under TestClient without "with"

app = FastAPI(title="Sub-Judice API", version="0.3.0")

origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]
if auth_configured() and "*" in origins:
    logging.getLogger("sub_judice").warning(
        "CORS_ORIGINS='*' while auth is enabled: set CORS_ORIGINS to your frontend's exact origin(s) in production.")
if not auth_configured():
    logging.getLogger("sub_judice").warning("No STAFF_TOKEN/STAFF_TOKENS set: DEV MODE, every endpoint is open.")
app.add_middleware(
    CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"],  # incl. X-Staff-Token / X-Case-Token
    expose_headers=["X-Total-Count"],
)

app.include_router(cases.router)
app.include_router(custody.router)
app.include_router(hearings.router)
app.include_router(judgments.router)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_req: Request, _exc: IntegrityError):
    # e.g. two writers raced on the same custody chain link: tell the client to retry instead of a bare 500
    return JSONResponse(status_code=409, content={"detail": "Conflicting concurrent update, please retry"})


@app.get("/health", tags=["meta"])
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))  # proves the DB is reachable, not just that the process is up
    return {"status": "ok", "version": app.version}
