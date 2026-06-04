import csv
import io
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

app = FastAPI(title="FlowMind AI")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

WAITLIST_FILE = Path("data/waitlist.json")
WAITLIST_FILE.parent.mkdir(exist_ok=True)
if not WAITLIST_FILE.exists():
    WAITLIST_FILE.write_text("[]")

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


class WaitlistEntry(BaseModel):
    email: str
    name: str
    business_type: str = ""


def load_waitlist() -> list:
    return json.loads(WAITLIST_FILE.read_text())


def save_waitlist(data: list) -> None:
    WAITLIST_FILE.write_text(json.dumps(data, indent=2))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/waitlist")
async def join_waitlist(entry: WaitlistEntry):
    if not entry.email or "@" not in entry.email:
        raise HTTPException(status_code=422, detail="Invalid email address")

    waitlist = load_waitlist()

    if any(e["email"].lower() == entry.email.lower() for e in waitlist):
        return JSONResponse(
            status_code=200,
            content={"status": "already_registered", "message": "You're already on the waitlist!"},
        )

    waitlist.append(
        {
            "email": entry.email.lower().strip(),
            "name": entry.name.strip(),
            "business_type": entry.business_type.strip(),
            "joined_at": datetime.now(timezone.utc).isoformat(),
            "position": len(waitlist) + 1,
        }
    )
    save_waitlist(waitlist)

    return JSONResponse(
        status_code=201,
        content={
            "status": "success",
            "message": "You're on the list!",
            "position": len(waitlist),
        },
    )


@app.get("/api/waitlist/count")
async def waitlist_count():
    waitlist = load_waitlist()
    return {"count": len(waitlist)}


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, _: None = Depends(require_admin)):
    waitlist = load_waitlist()
    by_type: dict = {}
    for e in waitlist:
        t = e.get("business_type") or "not specified"
        by_type[t] = by_type.get(t, 0) + 1
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "entries": waitlist, "by_type": by_type},
    )


@app.get("/admin/export")
async def export_csv(_: None = Depends(require_admin)):
    waitlist = load_waitlist()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["position", "name", "email", "business_type", "joined_at"])
    writer.writeheader()
    writer.writerows(waitlist)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=flowmind_waitlist.csv"},
    )
