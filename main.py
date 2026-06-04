import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr

app = FastAPI(title="FlowMind AI")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

WAITLIST_FILE = Path("data/waitlist.json")
WAITLIST_FILE.parent.mkdir(exist_ok=True)
if not WAITLIST_FILE.exists():
    WAITLIST_FILE.write_text("[]")


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
