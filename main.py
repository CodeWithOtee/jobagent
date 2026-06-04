import csv
import io
import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

app = FastAPI(title="FlowMind AI")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DATABASE_URL = os.getenv("DATABASE_URL", "")

# ── local JSON fallback (dev only) ──────────────────────────────────────────
WAITLIST_FILE = Path("data/waitlist.json")
WAITLIST_FILE.parent.mkdir(exist_ok=True)
if not WAITLIST_FILE.exists():
    WAITLIST_FILE.write_text("[]")

# ── auth ────────────────────────────────────────────────────────────────────
security = HTTPBasic()
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD env var not set")
    user_ok = secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Incorrect credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# ── database helpers ─────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS waitlist (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    business_type TEXT DEFAULT '',
                    joined_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)


def db_add_entry(email: str, name: str, business_type: str) -> dict:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO waitlist (email, name, business_type) VALUES (%s, %s, %s) RETURNING id",
                (email, name, business_type),
            )
            row_id = cur.fetchone()["id"]
            cur.execute("SELECT COUNT(*) AS cnt FROM waitlist")
            count = cur.fetchone()["cnt"]
    return {"id": row_id, "position": count}


def db_email_exists(email: str) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM waitlist WHERE email = %s", (email,))
            return cur.fetchone() is not None


def db_get_all() -> list:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ROW_NUMBER() OVER (ORDER BY id) AS position, name, email, business_type, joined_at FROM waitlist ORDER BY id")
            rows = cur.fetchall()
    return [
        {
            "position": r["position"],
            "name": r["name"],
            "email": r["email"],
            "business_type": r["business_type"],
            "joined_at": r["joined_at"].isoformat() if r["joined_at"] else "",
        }
        for r in rows
    ]


def db_count() -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM waitlist")
            return cur.fetchone()["cnt"]


# ── JSON file helpers (local dev) ────────────────────────────────────────────
def file_load() -> list:
    return json.loads(WAITLIST_FILE.read_text())


def file_save(data: list) -> None:
    WAITLIST_FILE.write_text(json.dumps(data, indent=2))


# ── startup ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    if DATABASE_URL:
        init_db()


# ── models ────────────────────────────────────────────────────────────────────
class WaitlistEntry(BaseModel):
    email: str
    name: str
    business_type: str = ""


# ── routes ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/waitlist")
async def join_waitlist(entry: WaitlistEntry):
    email = entry.email.lower().strip()
    name = entry.name.strip()
    biz = entry.business_type.strip()

    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Invalid email address")

    if DATABASE_URL:
        if db_email_exists(email):
            return JSONResponse(status_code=200, content={"status": "already_registered", "message": "You're already on the waitlist!"})
        result = db_add_entry(email, name, biz)
        return JSONResponse(status_code=201, content={"status": "success", "message": "You're on the list!", "position": result["position"]})
    else:
        waitlist = file_load()
        if any(e["email"] == email for e in waitlist):
            return JSONResponse(status_code=200, content={"status": "already_registered", "message": "You're already on the waitlist!"})
        waitlist.append({"email": email, "name": name, "business_type": biz, "joined_at": datetime.now(timezone.utc).isoformat(), "position": len(waitlist) + 1})
        file_save(waitlist)
        return JSONResponse(status_code=201, content={"status": "success", "message": "You're on the list!", "position": len(waitlist)})


@app.get("/api/waitlist/count")
async def waitlist_count():
    count = db_count() if DATABASE_URL else len(file_load())
    return {"count": count}


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, _: None = Depends(require_admin)):
    entries = db_get_all() if DATABASE_URL else file_load()
    by_type: dict = {}
    for e in entries:
        t = e.get("business_type") or "not specified"
        by_type[t] = by_type.get(t, 0) + 1
    return templates.TemplateResponse("admin.html", {"request": request, "entries": entries, "by_type": by_type})


@app.get("/admin/export")
async def export_csv(_: None = Depends(require_admin)):
    entries = db_get_all() if DATABASE_URL else file_load()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["position", "name", "email", "business_type", "joined_at"])
    writer.writeheader()
    writer.writerows(entries)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=flowmind_waitlist.csv"},
    )
