import base64
import binascii
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.auth import clear_admin_cookie, is_admin, require_admin, secrets_equal, set_admin_cookie, admin_password, admin_username
from app.db import (
    branding_summary,
    counts_for,
    delete_setting,
    find_submission,
    get_conn,
    get_setting,
    get_track,
    init_db,
    list_tasks,
    list_tracks,
    now_iso,
    overall_for,
    set_setting,
    slugify,
    submission_payload,
    task_out,
    timezone_name,
    today_iso,
    track_out,
    unique_slug,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATUS_RANK = {"green": 1, "amber": 2, "red": 3}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Daily Health", lifespan=lifespan)


class ItemIn(BaseModel):
    task_id: int
    status: str
    comment: str = ""
    reference: str = ""


class SubmissionIn(BaseModel):
    track_id: int
    check_date: str
    submitted_by: str
    items: list[ItemIn]


class TrackIn(BaseModel):
    name: str


class TrackPatch(BaseModel):
    name: str | None = None
    active: bool | None = None


class LoginIn(BaseModel):
    username: str
    password: str


class BrandingIn(BaseModel):
    banner_color: str | None = None
    logo_data_url: str | None = None
    remove_logo: bool = False


class TaskIn(BaseModel):
    track_id: int
    title: str
    guidance: str = ""


class TaskPatch(BaseModel):
    title: str | None = None
    guidance: str | None = None
    active: bool | None = None
    move: str | None = None


def parse_date(value: str, allow_future: bool = False) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Use a date in YYYY-MM-DD format.") from exc
    if not allow_future and parsed.isoformat() > today_iso():
        raise HTTPException(status_code=400, detail="A future day cannot be recorded.")
    return parsed.isoformat()


def comment_is_meaningful(text: str) -> bool:
    stripped = text.strip()
    alnum = re.sub(r"[^A-Za-z0-9]", "", stripped)
    return len(stripped) >= 3 and len(alnum) >= 3


def reference_error(text: str) -> str | None:
    stripped = text.strip()
    if len(stripped) > 40:
        return "Service request or incident number must be 40 characters or fewer."
    if len(stripped) < 3 or not re.search(r"[A-Za-z0-9]", stripped):
        return "Amber and red need a service request or incident number."
    return None


def clean_title(value: str, label: str) -> str:
    title = " ".join(value.split())
    if len(title) < 3:
        raise HTTPException(status_code=422, detail=f"{label} must be at least 3 characters.")
    if len(title) > 120:
        raise HTTPException(status_code=422, detail=f"{label} must be 120 characters or fewer.")
    return title


def clean_guidance(value: str) -> str:
    guidance = value.strip()
    if len(guidance) > 400:
        raise HTTPException(status_code=422, detail="Guidance must be 400 characters or fewer.")
    return guidance


def require_track(conn, track_id: int):
    track = get_track(conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="That track does not exist.")
    return track


@app.middleware("http")
async def disable_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/meta")
def meta():
    return {"today": today_iso(), "timezone": timezone_name()}


COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp|gif);base64,([A-Za-z0-9+/=\s]+)$", re.IGNORECASE)
MAX_LOGO_BYTES = 512_000


def sniff_image(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


@app.get("/api/branding")
def branding():
    with get_conn() as conn:
        return branding_summary(conn)


@app.get("/api/branding/logo")
def branding_logo():
    with get_conn() as conn:
        encoded = get_setting(conn, "logo_data")
        media_type = get_setting(conn, "logo_type")
    if not encoded or not media_type:
        raise HTTPException(status_code=404, detail="No logo has been added.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=404, detail="The saved logo could not be read.") from exc
    return Response(content=raw, media_type=media_type)


@app.put("/api/branding")
def update_branding(body: BrandingIn, request: Request):
    require_admin(request)
    if body.banner_color is None and body.logo_data_url is None and not body.remove_logo:
        raise HTTPException(status_code=422, detail="Nothing to update.")
    color = None
    if body.banner_color is not None:
        color = body.banner_color.strip()
        if not COLOR_RE.fullmatch(color):
            raise HTTPException(status_code=422, detail="Choose a banner colour as a 6-digit hex value.")
    logo = None
    if body.logo_data_url is not None:
        match = DATA_URL_RE.fullmatch(body.logo_data_url.strip())
        if not match:
            raise HTTPException(status_code=422, detail="Logo must be a PNG, JPEG, WEBP, or GIF image.")
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
        except binascii.Error as exc:
            raise HTTPException(status_code=422, detail="That logo file could not be read.") from exc
        if not raw or len(raw) > MAX_LOGO_BYTES:
            raise HTTPException(status_code=422, detail="Logo must be 512 KB or smaller.")
        media_type = sniff_image(raw)
        if media_type is None:
            raise HTTPException(status_code=422, detail="Logo must be a PNG, JPEG, WEBP, or GIF image.")
        logo = (base64.b64encode(raw).decode(), media_type)
    with get_conn() as conn:
        if color is not None:
            set_setting(conn, "banner_color", color.lower())
        if body.remove_logo:
            delete_setting(conn, "logo_data")
            delete_setting(conn, "logo_type")
        elif logo is not None:
            set_setting(conn, "logo_data", logo[0])
            set_setting(conn, "logo_type", logo[1])
        return branding_summary(conn)


@app.get("/api/session")
def session(request: Request):
    return {"admin": is_admin(request)}


@app.post("/api/login")
def login(body: LoginIn, response: Response):
    if not admin_password():
        raise HTTPException(status_code=503, detail="Admin login is not configured. Set ADMIN_PASSWORD.")
    username = body.username.strip()
    password_ok = secrets_equal(body.password, admin_password())
    username_ok = secrets_equal(username, admin_username())
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail="Those admin details were not recognised.")
    set_admin_cookie(response, admin_username())
    return {"admin": True}


@app.post("/api/logout")
def logout(response: Response):
    clear_admin_cookie(response)
    return {"admin": False}


@app.get("/api/tracks")
def tracks(request: Request, include_inactive: bool = False):
    if include_inactive:
        require_admin(request)
    with get_conn() as conn:
        return list_tracks(conn, include_inactive=include_inactive)


@app.post("/api/tracks", status_code=201)
def create_track(body: TrackIn, request: Request):
    require_admin(request)
    name = clean_title(body.name, "Track name")
    with get_conn() as conn:
        taken = conn.execute(
            "SELECT id FROM tracks WHERE lower(name) = lower(?)",
            (name,),
        ).fetchone()
        if taken:
            raise HTTPException(status_code=409, detail="A track with that name already exists.")
        sort_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM tracks").fetchone()["n"]
        cursor = conn.execute(
            "INSERT INTO tracks (slug, name, sort_order, active) VALUES (?, ?, ?, 1)",
            (unique_slug(conn, slugify(name)), name, sort_order),
        )
        row = get_track(conn, cursor.lastrowid)
        return track_out(row)


@app.patch("/api/tracks/{track_id}")
def update_track(track_id: int, body: TrackPatch, request: Request):
    require_admin(request)
    if body.name is None and body.active is None:
        raise HTTPException(status_code=422, detail="Nothing to update.")
    with get_conn() as conn:
        require_track(conn, track_id)
        if body.name is not None:
            name = clean_title(body.name, "Track name")
            taken = conn.execute(
                "SELECT id FROM tracks WHERE lower(name) = lower(?) AND id != ?",
                (name, track_id),
            ).fetchone()
            if taken:
                raise HTTPException(status_code=409, detail="A track with that name already exists.")
            conn.execute("UPDATE tracks SET name = ? WHERE id = ?", (name, track_id))
        if body.active is not None:
            conn.execute("UPDATE tracks SET active = ? WHERE id = ?", (int(body.active), track_id))
        return track_out(get_track(conn, track_id))


@app.delete("/api/tracks/{track_id}")
def delete_track(track_id: int, request: Request):
    require_admin(request)
    with get_conn() as conn:
        require_track(conn, track_id)
        filed = conn.execute(
            "SELECT 1 FROM submissions WHERE track_id = ?",
            (track_id,),
        ).fetchone()
        if filed:
            conn.execute("UPDATE tracks SET active = 0 WHERE id = ?", (track_id,))
            return {"removed": True, "kept_history": True}
        conn.execute("DELETE FROM tasks WHERE track_id = ?", (track_id,))
        conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        return {"removed": True, "kept_history": False}


@app.get("/api/tasks")
def tasks(track_id: int, request: Request, include_inactive: bool = False):
    if include_inactive:
        require_admin(request)
    with get_conn() as conn:
        require_track(conn, track_id)
        return list_tasks(conn, track_id, include_inactive)


@app.post("/api/tasks", status_code=201)
def create_task(body: TaskIn, request: Request):
    require_admin(request)
    title = clean_title(body.title, "Check title")
    guidance = clean_guidance(body.guidance)
    with get_conn() as conn:
        require_track(conn, body.track_id)
        sort_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 10 AS n FROM tasks WHERE track_id = ?",
            (body.track_id,),
        ).fetchone()["n"]
        cursor = conn.execute(
            """
            INSERT INTO tasks (track_id, title, guidance, sort_order, active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (body.track_id, title, guidance, sort_order),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return task_out(row)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskPatch, request: Request):
    require_admin(request)
    if body.move is not None and body.move not in {"up", "down"}:
        raise HTTPException(status_code=422, detail="Move must be up or down.")
    with get_conn() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="That check does not exist.")
        title = task["title"] if body.title is None else clean_title(body.title, "Check title")
        guidance = task["guidance"] if body.guidance is None else clean_guidance(body.guidance)
        active = task["active"] if body.active is None else int(body.active)
        conn.execute(
            "UPDATE tasks SET title = ?, guidance = ?, active = ? WHERE id = ?",
            (title, guidance, active, task_id),
        )
        if body.move:
            move_task(conn, task_id, body.move)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return task_out(row)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, request: Request):
    require_admin(request)
    with get_conn() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="That check does not exist.")
        used = conn.execute(
            "SELECT 1 FROM submission_items WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if used:
            conn.execute("UPDATE tasks SET active = 0 WHERE id = ?", (task_id,))
            return {"removed": True, "kept_history": True}
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return {"removed": True, "kept_history": False}


def move_task(conn, task_id: int, direction: str) -> None:
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if direction == "up":
        neighbor = conn.execute(
            """
            SELECT * FROM tasks
            WHERE track_id = ? AND sort_order < ?
            ORDER BY sort_order DESC LIMIT 1
            """,
            (task["track_id"], task["sort_order"]),
        ).fetchone()
    else:
        neighbor = conn.execute(
            """
            SELECT * FROM tasks
            WHERE track_id = ? AND sort_order > ?
            ORDER BY sort_order ASC LIMIT 1
            """,
            (task["track_id"], task["sort_order"]),
        ).fetchone()
    if neighbor is None:
        return
    conn.execute("UPDATE tasks SET sort_order = ? WHERE id = ?", (neighbor["sort_order"], task["id"]))
    conn.execute("UPDATE tasks SET sort_order = ? WHERE id = ?", (task["sort_order"], neighbor["id"]))


@app.get("/api/entry")
def entry(track_id: int, date: str | None = None):
    check_date = parse_date(date or today_iso(), allow_future=True)
    if check_date > today_iso():
        raise HTTPException(status_code=400, detail="A future day cannot be opened.")
    with get_conn() as conn:
        track = require_track(conn, track_id)
        if not track["active"]:
            raise HTTPException(status_code=404, detail="That track has been removed.")
        return {
            "date": check_date,
            "editable": check_date == today_iso(),
            "track": track_out(track),
            "tasks": list_tasks(conn, track_id, include_inactive=False),
            "submission": find_submission(conn, track_id, check_date),
        }


@app.post("/api/submissions")
def save_submission(body: SubmissionIn):
    check_date = parse_date(body.check_date)
    if check_date < today_iso():
        raise HTTPException(status_code=400, detail="A past day cannot be changed.")
    submitted_by = " ".join(body.submitted_by.split())
    errors = []
    if len(submitted_by) < 2:
        errors.append({"field": "submitted_by", "message": "Enter your name."})
    elif len(submitted_by) > 80:
        errors.append({"field": "submitted_by", "message": "Name must be 80 characters or fewer."})

    seen = set()
    for item in body.items:
        if item.task_id in seen:
            raise HTTPException(
                status_code=422,
                detail={"message": "Each check can only be answered once.", "fields": []},
            )
        seen.add(item.task_id)
        if item.status not in STATUS_RANK:
            errors.append(
                {
                    "task_id": item.task_id,
                    "field": "status",
                    "message": "Choose green, amber, or red.",
                }
            )
        elif item.status in {"red", "amber"} and not comment_is_meaningful(item.comment):
            errors.append(
                {
                    "task_id": item.task_id,
                    "field": "comment",
                    "message": "Amber and red need a comment that describes the issue.",
                }
            )
        elif len(item.comment.strip()) > 1000:
            errors.append(
                {
                    "task_id": item.task_id,
                    "field": "comment",
                    "message": "Comment must be 1000 characters or fewer.",
                }
            )
        elif item.status in {"red", "amber"} and (message := reference_error(item.reference)):
            errors.append(
                {
                    "task_id": item.task_id,
                    "field": "reference",
                    "message": message,
                }
            )

    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Some checks still need attention.", "fields": errors},
        )

    with get_conn() as conn:
        track = require_track(conn, body.track_id)
        if not track["active"]:
            raise HTTPException(status_code=404, detail="That track has been removed.")
        active = conn.execute(
            "SELECT * FROM tasks WHERE track_id = ? AND active = 1 ORDER BY sort_order, id",
            (body.track_id,),
        ).fetchall()
        if not active:
            raise HTTPException(status_code=422, detail="This track has no checks to fill in yet.")
        active_ids = {row["id"] for row in active}
        if seen != active_ids:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "The checklist changed. Reload this page and fill every current check.",
                    "fields": [],
                },
            )
        tasks_by_id = {row["id"]: row for row in active}
        timestamp = now_iso()
        existing = conn.execute(
            "SELECT * FROM submissions WHERE track_id = ? AND check_date = ?",
            (body.track_id, check_date),
        ).fetchone()
        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO submissions (track_id, check_date, submitted_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (body.track_id, check_date, submitted_by, timestamp, timestamp),
            )
            submission_id = cursor.lastrowid
        else:
            submission_id = existing["id"]
            conn.execute(
                "UPDATE submissions SET submitted_by = ?, updated_at = ? WHERE id = ?",
                (submitted_by, timestamp, submission_id),
            )
            conn.execute("DELETE FROM submission_items WHERE submission_id = ?", (submission_id,))

        for item in body.items:
            task = tasks_by_id[item.task_id]
            conn.execute(
                """
                INSERT INTO submission_items
                    (submission_id, task_id, task_title, status, comment, reference)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    task["id"],
                    task["title"],
                    item.status,
                    item.comment.strip(),
                    "" if item.status == "green" else item.reference.strip(),
                ),
            )
        row = conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
        return submission_payload(conn, row)


@app.get("/api/board")
def board(date: str | None = None):
    check_date = parse_date(date or today_iso(), allow_future=True)
    if check_date > today_iso():
        raise HTTPException(status_code=400, detail="A future day cannot be opened.")
    with get_conn() as conn:
        tracks = list_tracks(conn, on_date=check_date)
        cards = []
        summary = {"red": 0, "amber": 0, "green": 0, "missing": 0}
        for track in tracks:
            submission = find_submission(conn, track["id"], check_date)
            overall = submission["overall"] if submission else None
            if overall is None:
                summary["missing"] += 1
                counts = counts_for([])
            else:
                summary[overall] += 1
                counts = submission["counts"]
            cards.append(
                {
                    **track,
                    "overall": overall,
                    "counts": counts,
                    "submission": submission,
                }
            )
        return {"date": check_date, "summary": summary, "tracks": cards}


@app.get("/api/history")
def history(days: int = Query(default=30, ge=7, le=90), end: str | None = None):
    end_date = parse_date(end or today_iso(), allow_future=True)
    if end_date > today_iso():
        raise HTTPException(status_code=400, detail="A future day cannot be opened.")
    end_value = datetime.strptime(end_date, "%Y-%m-%d").date()
    dates = [(end_value - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
    start = dates[0]
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.track_id, s.check_date, i.status
            FROM submissions s
            JOIN submission_items i ON i.submission_id = s.id
            WHERE s.check_date BETWEEN ? AND ?
            """,
            (start, end_date),
        ).fetchall()
        grouped: dict[tuple[int, str], list[str]] = {}
        for row in rows:
            grouped.setdefault((row["track_id"], row["check_date"]), []).append(row["status"])
        tracks = []
        for track in list_tracks(conn, between=(start, end_date)):
            tracks.append(
                {
                    "id": track["id"],
                    "name": track["name"],
                    "days": [
                        {
                            "date": day,
                            "overall": overall_for(grouped.get((track["id"], day), [])),
                        }
                        for day in dates
                    ],
                }
            )
        return {"dates": dates, "tracks": tracks}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
