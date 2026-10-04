import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "health.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    title TEXT NOT NULL,
    guidance TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    check_date TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (track_id, check_date)
);

CREATE TABLE IF NOT EXISTS submission_items (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    task_title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('red', 'amber', 'green')),
    comment TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '',
    UNIQUE (submission_id, task_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_track ON tasks(track_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_submissions_date ON submissions(check_date);
CREATE INDEX IF NOT EXISTS idx_items_submission ON submission_items(submission_id);
"""

DEFAULT_TRACKS = [
    (
        "Windows",
        [
            (
                "Directory and core services",
                "Domain controllers, DNS, and other required Windows services are healthy.",
            ),
            (
                "Patch posture",
                "Critical and security updates are within the agreed window.",
            ),
            (
                "Disk capacity",
                "System and data volumes are under the capacity threshold.",
            ),
            (
                "Event and service health",
                "No repeating critical errors, and required services are running.",
            ),
            (
                "Identity replication",
                "Active Directory replication is current, with no unresolved failures.",
            ),
        ],
    ),
    (
        "VMware",
        [
            (
                "vCenter and clusters",
                "Management services and clusters are healthy.",
            ),
            (
                "Host state",
                "No production host is disconnected, failed, or stuck in maintenance.",
            ),
            (
                "Datastore capacity",
                "Datastores are above the free-space threshold.",
            ),
            (
                "Snapshot age",
                "No snapshots are older than the agreed window.",
            ),
            (
                "Production alarms",
                "No unresolved red alarms remain on production workloads.",
            ),
        ],
    ),
    (
        "Storage",
        [
            (
                "Array health",
                "Controllers, ports, and paths are online.",
            ),
            (
                "Capacity headroom",
                "Pools and file systems are above the free-space threshold.",
            ),
            (
                "Drives and spares",
                "No unresolved failed media, and hot spares are available.",
            ),
            (
                "Protection copies",
                "Replication or snapshot schedules completed as expected.",
            ),
            (
                "Performance",
                "Latency and throughput are within agreed limits.",
            ),
        ],
    ),
    (
        "Backup",
        [
            (
                "Scheduled jobs",
                "Production backup jobs completed successfully.",
            ),
            (
                "Failures and misses",
                "No unresolved failed or missed jobs remain.",
            ),
            (
                "Repository capacity",
                "Backup storage is above the free-space threshold.",
            ),
            (
                "Secondary copies",
                "Offsite or immutable copies are current.",
            ),
            (
                "Backup platform",
                "Backup servers, proxies, and agents are reachable.",
            ),
        ],
    ),
    (
        "Database",
        [
            (
                "Instance availability",
                "Production instances are up and accepting connections.",
            ),
            (
                "Backup currency",
                "Full and log backups completed within policy.",
            ),
            (
                "Space",
                "Data files, logs, and tablespaces are within threshold.",
            ),
            (
                "High availability",
                "Clustering, replicas, or Always On status is healthy.",
            ),
            (
                "Errors and blocking",
                "No sustained blocking or repeating severe errors.",
            ),
        ],
    ),
    (
        "Linux",
        [
            (
                "Host availability",
                "Production hosts are reachable.",
            ),
            (
                "Filesystem capacity",
                "Disk and inode usage is within threshold.",
            ),
            (
                "Core services",
                "Required daemons are running.",
            ),
            (
                "Security updates",
                "Critical patches are not overdue.",
            ),
            (
                "Resource health",
                "No sustained CPU, memory, or recurring error spikes.",
            ),
        ],
    ),
]


def database_path() -> Path:
    return Path(os.environ.get("DATABASE_PATH", DEFAULT_DB))


def timezone_name() -> str:
    return os.environ.get("TZ", "Asia/Kolkata")


def current_zone():
    try:
        return ZoneInfo(timezone_name())
    except ZoneInfoNotFoundError:
        return datetime.now().astimezone().tzinfo


def now_local() -> datetime:
    return datetime.now(current_zone())


def today_iso() -> str:
    return now_local().date().isoformat()


def now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "track"


@contextmanager
def get_conn():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL").fetchone()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracks)")}
        if "active" not in columns:
            conn.execute("ALTER TABLE tracks ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        item_columns = {row["name"] for row in conn.execute("PRAGMA table_info(submission_items)")}
        if "reference" not in item_columns:
            conn.execute(
                "ALTER TABLE submission_items ADD COLUMN reference TEXT NOT NULL DEFAULT ''"
            )
        seed(conn)


def seed(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT COUNT(*) AS n FROM tracks").fetchone()["n"]
    if existing:
        return
    for index, (name, tasks) in enumerate(DEFAULT_TRACKS, start=1):
        slug = unique_slug(conn, slugify(name))
        cursor = conn.execute(
            "INSERT INTO tracks (slug, name, sort_order, active) VALUES (?, ?, ?, 1)",
            (slug, name, index),
        )
        track_id = cursor.lastrowid
        for task_index, (title, guidance) in enumerate(tasks, start=1):
            conn.execute(
                """
                INSERT INTO tasks (track_id, title, guidance, sort_order, active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (track_id, title, guidance, task_index * 10),
            )


def unique_slug(conn: sqlite3.Connection, base: str) -> str:
    slug = base
    suffix = 2
    while conn.execute("SELECT 1 FROM tracks WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def overall_for(statuses: list[str]) -> str | None:
    if not statuses:
        return None
    if "red" in statuses:
        return "red"
    if "amber" in statuses:
        return "amber"
    return "green"


def counts_for(statuses: list[str]) -> dict:
    return {
        "red": sum(status == "red" for status in statuses),
        "amber": sum(status == "amber" for status in statuses),
        "green": sum(status == "green" for status in statuses),
    }


def track_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "sort_order": row["sort_order"],
        "active": bool(row["active"]),
    }


def task_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "track_id": row["track_id"],
        "title": row["title"],
        "guidance": row["guidance"],
        "sort_order": row["sort_order"],
        "active": bool(row["active"]),
    }


def list_tracks(
    conn: sqlite3.Connection,
    include_inactive: bool = False,
    on_date: str | None = None,
    between: tuple[str, str] | None = None,
) -> list[dict]:
    if include_inactive:
        rows = conn.execute("SELECT * FROM tracks ORDER BY sort_order, name").fetchall()
    elif on_date:
        rows = conn.execute(
            """
            SELECT * FROM tracks AS t
            WHERE t.active = 1
               OR EXISTS (
                    SELECT 1 FROM submissions AS s
                    WHERE s.track_id = t.id AND s.check_date = ?
               )
            ORDER BY t.sort_order, t.name
            """,
            (on_date,),
        ).fetchall()
    elif between:
        rows = conn.execute(
            """
            SELECT * FROM tracks AS t
            WHERE t.active = 1
               OR EXISTS (
                    SELECT 1 FROM submissions AS s
                    WHERE s.track_id = t.id AND s.check_date BETWEEN ? AND ?
               )
            ORDER BY t.sort_order, t.name
            """,
            between,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tracks WHERE active = 1 ORDER BY sort_order, name"
        ).fetchall()
    return [track_out(row) for row in rows]


def get_track(conn: sqlite3.Connection, track_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


def list_tasks(conn: sqlite3.Connection, track_id: int, include_inactive: bool) -> list[dict]:
    if include_inactive:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE track_id = ? ORDER BY sort_order, id",
            (track_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE track_id = ? AND active = 1 ORDER BY sort_order, id",
            (track_id,),
        ).fetchall()
    return [task_out(row) for row in rows]


def submission_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    items = conn.execute(
        """
        SELECT i.task_id, i.task_title, i.status, i.comment, i.reference,
               COALESCE(t.sort_order, 9999) AS sort_order
        FROM submission_items i
        LEFT JOIN tasks t ON t.id = i.task_id
        WHERE i.submission_id = ?
        ORDER BY sort_order, i.id
        """,
        (row["id"],),
    ).fetchall()
    statuses = [item["status"] for item in items]
    return {
        "id": row["id"],
        "track_id": row["track_id"],
        "check_date": row["check_date"],
        "submitted_by": row["submitted_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "overall": overall_for(statuses),
        "counts": counts_for(statuses),
        "items": [
            {
                "task_id": item["task_id"],
                "task_title": item["task_title"],
                "status": item["status"],
                "comment": item["comment"],
                "reference": item["reference"] or "",
            }
            for item in items
        ],
    }


DEFAULT_BANNER = "#1f3d36"


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def delete_setting(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def branding_summary(conn: sqlite3.Connection) -> dict:
    return {
        "banner_color": get_setting(conn, "banner_color") or DEFAULT_BANNER,
        "has_logo": bool(get_setting(conn, "logo_data")),
    }


def find_submission(conn: sqlite3.Connection, track_id: int, check_date: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM submissions WHERE track_id = ? AND check_date = ?",
        (track_id, check_date),
    ).fetchone()
    if row is None:
        return None
    return submission_payload(conn, row)
