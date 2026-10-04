import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "health.db"))
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pass")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    with TestClient(app) as test_client:
        yield test_client


def login(client):
    response = client.post("/api/login", json={"username": "admin", "password": "test-admin-pass"})
    assert response.status_code == 200
    assert response.json()["admin"] is True


def track_id(client, name):
    tracks = client.get("/api/tracks").json()
    return next(track["id"] for track in tracks if track["name"] == name)


def submit(
    client,
    name,
    status="green",
    comment="",
    submitted_by="Priya",
    day=None,
    overrides=None,
    reference="",
):
    day = day or client.get("/api/meta").json()["today"]
    selected = track_id(client, name)
    tasks = client.get("/api/tasks", params={"track_id": selected}).json()
    items = []
    for index, task in enumerate(tasks):
        item_status = status
        item_comment = comment
        item_reference = reference
        if overrides and index in overrides:
            item_status = overrides[index]["status"]
            item_comment = overrides[index].get("comment", "")
            item_reference = overrides[index].get("reference", reference)
        items.append(
            {
                "task_id": task["id"],
                "status": item_status,
                "comment": item_comment,
                "reference": item_reference,
            }
        )
    response = client.post(
        "/api/submissions",
        json={
            "track_id": selected,
            "check_date": day,
            "submitted_by": submitted_by,
            "items": items,
        },
    )
    return response


def test_seed_tracks_and_home(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    names = [track["name"] for track in client.get("/api/tracks").json()]
    assert names == ["Windows", "VMware", "Storage", "Backup", "Database", "Linux"]
    page = client.get("/")
    assert page.status_code == 200
    assert "Daily Health" in page.text
    tasks = client.get("/api/tasks", params={"track_id": track_id(client, "Backup")}).json()
    assert len(tasks) == 5


def test_green_submission_rolls_up(client):
    saved = submit(client, "Linux")
    assert saved.status_code == 200
    body = saved.json()
    assert body["overall"] == "green"
    assert body["counts"] == {"red": 0, "amber": 0, "green": 5}

    board = client.get("/api/board").json()
    linux = next(track for track in board["tracks"] if track["name"] == "Linux")
    assert linux["overall"] == "green"
    assert board["summary"] == {"red": 0, "amber": 0, "green": 1, "missing": 5}


def test_amber_and_red_require_a_real_comment(client):
    missing = submit(client, "Windows", status="green", overrides={0: {"status": "amber", "comment": ""}})
    assert missing.status_code == 422
    assert missing.json()["detail"]["fields"][0]["field"] == "comment"

    placeholder = submit(client, "Windows", status="green", overrides={0: {"status": "red", "comment": "N/A"}})
    assert placeholder.status_code == 422

    saved = submit(
        client,
        "Windows",
        status="green",
        overrides={1: {"status": "amber", "comment": "Two hosts are low on disk", "reference": "INC0012345"}},
    )
    assert saved.status_code == 200
    assert saved.json()["overall"] == "amber"

    red = submit(
        client,
        "Windows",
        status="amber",
        comment="Replica lag is climbing",
        reference="SR123456",
        overrides={0: {"status": "red", "comment": "Domain controller DC1 is down", "reference": "RITM0012345"}},
    )
    assert red.status_code == 200
    assert red.json()["overall"] == "red"
    assert red.json()["submitted_by"] == "Priya"


def test_reference_required_for_amber_and_red_but_not_green(client):
    missing = submit(
        client,
        "Linux",
        status="amber",
        comment="Queue depth is above the limit",
        reference="",
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["fields"][0]["field"] == "reference"

    punctuation = submit(
        client,
        "Linux",
        status="red",
        comment="Primary array is offline",
        reference="---",
    )
    assert punctuation.status_code == 422

    short = submit(
        client,
        "Linux",
        status="amber",
        comment="Queue depth is above the limit",
        reference="ab",
    )
    assert short.status_code == 422

    too_long = submit(
        client,
        "Linux",
        status="amber",
        comment="Queue depth is above the limit",
        reference="INC" + ("1" * 40),
    )
    assert too_long.status_code == 422

    for ticket in ("INC0012345", "SR123456", "RITM0012345", "A12"):
        saved = submit(
            client,
            "Linux",
            status="amber",
            comment="Queue depth is above the limit",
            reference=ticket,
        )
        assert saved.status_code == 200
        assert all(item["reference"] == ticket for item in saved.json()["items"])

    ignored = submit(client, "Storage", reference="INC0012345")
    assert ignored.status_code == 200
    assert all(item["reference"] == "" for item in ignored.json()["items"])

    green = submit(client, "VMware")
    assert green.status_code == 200
    assert all(item["reference"] == "" for item in green.json()["items"])


def test_legacy_database_gains_reference_column(tmp_path, monkeypatch):
    import sqlite3

    from app.db import find_submission, get_conn, init_db

    path = tmp_path / "legacy.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            track_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            guidance TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE submissions (
            id INTEGER PRIMARY KEY,
            track_id INTEGER NOT NULL,
            check_date TEXT NOT NULL,
            submitted_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (track_id, check_date)
        );
        CREATE TABLE submission_items (
            id INTEGER PRIMARY KEY,
            submission_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            task_title TEXT NOT NULL,
            status TEXT NOT NULL,
            comment TEXT NOT NULL DEFAULT '',
            UNIQUE (submission_id, task_id)
        );
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO tracks (slug, name, sort_order, active) VALUES ('linux', 'Linux', 1, 1);
        INSERT INTO tasks (track_id, title, guidance, sort_order, active)
            VALUES (1, 'Host availability', 'Production hosts are reachable.', 10, 1);
        INSERT INTO submissions (track_id, check_date, submitted_by, created_at, updated_at)
            VALUES (1, '2026-01-01', 'Priya', '2026-01-01T09:00:00', '2026-01-01T09:00:00');
        INSERT INTO submission_items (submission_id, task_id, task_title, status, comment)
            VALUES (1, 1, 'Host availability', 'green', '');
        """
    )
    conn.commit()
    conn.close()

    init_db()
    with get_conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(submission_items)")}
        assert "reference" in columns
        saved = find_submission(conn, 1, "2026-01-01")
    assert saved["items"][0]["reference"] == ""
    assert saved["items"][0]["comment"] == ""


def test_name_future_date_and_checklist_mismatch(client):
    nameless = submit(client, "Storage", submitted_by=" ")
    assert nameless.status_code == 422

    future = submit(client, "Storage", day="2999-01-01")
    assert future.status_code == 400

    selected = track_id(client, "Storage")
    tasks = client.get("/api/tasks", params={"track_id": selected}).json()
    partial = client.post(
        "/api/submissions",
        json={
            "track_id": selected,
            "check_date": client.get("/api/meta").json()["today"],
            "submitted_by": "Priya",
            "items": [{"task_id": tasks[0]["id"], "status": "green", "comment": ""}],
        },
    )
    assert partial.status_code == 422
    assert "checklist" in partial.json()["detail"]["message"].lower()


def test_past_day_cannot_be_changed(client):
    past = submit(client, "Windows", day="2020-01-01")
    assert past.status_code == 400
    assert "past" in past.json()["detail"].lower()

    selected = track_id(client, "Windows")
    today = client.get("/api/meta").json()["today"]
    closed = client.get("/api/entry", params={"track_id": selected, "date": "2020-01-01"})
    assert closed.status_code == 200
    assert closed.json()["editable"] is False
    current = client.get("/api/entry", params={"track_id": selected, "date": today})
    assert current.json()["editable"] is True


def test_history_shows_today(client):
    today = client.get("/api/meta").json()["today"]
    assert submit(
        client,
        "Database",
        overrides={2: {"status": "red", "comment": "Log volume is full", "reference": "INC0098765"}},
    ).status_code == 200
    history = client.get("/api/history", params={"days": 7, "end": today}).json()
    database = next(track for track in history["tracks"] if track["name"] == "Database")
    assert database["days"][-1] == {"date": today, "overall": "red"}
    assert database["days"][0]["overall"] is None

    entry = client.get("/api/entry", params={"track_id": track_id(client, "Database"), "date": today}).json()
    assert entry["submission"]["overall"] == "red"
    assert any(item["status"] == "red" for item in entry["submission"]["items"])


def test_hidden_check_is_not_required_on_next_save(client):
    login(client)
    selected = track_id(client, "VMware")
    tasks = client.get("/api/tasks", params={"track_id": selected}).json()
    hidden = client.patch(f"/api/tasks/{tasks[-1]['id']}", json={"active": False})
    assert hidden.status_code == 200
    saved = submit(client, "VMware")
    assert saved.status_code == 200
    assert saved.json()["counts"]["green"] == 4
    active = client.get("/api/tasks", params={"track_id": selected}).json()
    assert len(active) == 4
    including_hidden = client.get(
        "/api/tasks",
        params={"track_id": selected, "include_inactive": True},
    ).json()
    assert len(including_hidden) == 5


def test_reorder_and_add_check(client):
    login(client)
    selected = track_id(client, "Linux")
    tasks = client.get("/api/tasks", params={"track_id": selected}).json()
    moved = client.patch(f"/api/tasks/{tasks[1]['id']}", json={"move": "up"})
    assert moved.status_code == 200
    again = client.get("/api/tasks", params={"track_id": selected}).json()
    assert [task["id"] for task in again[:2]] == [tasks[1]["id"], tasks[0]["id"]]

    created = client.post(
        "/api/tasks",
        json={
            "track_id": selected,
            "title": "Certificate expiry",
            "guidance": "TLS certificates are inside the renewal window.",
        },
    )
    assert created.status_code == 201
    titles = [task["title"] for task in client.get("/api/tasks", params={"track_id": selected}).json()]
    assert titles[-1] == "Certificate expiry"


def test_admin_login_gates_track_and_task_changes(client):
    assert client.get("/api/session").json() == {"admin": False}
    blocked = client.post("/api/tracks", json={"name": "Network"})
    assert blocked.status_code == 401
    wrong = client.post("/api/login", json={"username": "admin", "password": "nope"})
    assert wrong.status_code == 401

    login(client)
    created = client.post("/api/tracks", json={"name": "Network"})
    assert created.status_code == 201
    removed = client.delete(f"/api/tracks/{created.json()['id']}")
    assert removed.status_code == 200
    assert removed.json()["kept_history"] is False
    assert "Network" not in [track["name"] for track in client.get("/api/tracks").json()]

    logged_out = client.post("/api/logout")
    assert logged_out.status_code == 200
    assert client.post("/api/tasks", json={"track_id": 1, "title": "Should fail", "guidance": ""}).status_code == 401


def test_removed_track_stays_in_history(client):
    login(client)
    assert submit(client, "Backup").status_code == 200
    selected = track_id(client, "Backup")
    removed = client.delete(f"/api/tracks/{selected}")
    assert removed.status_code == 200
    assert removed.json()["kept_history"] is True
    names = [track["name"] for track in client.get("/api/tracks").json()]
    assert "Backup" not in names
    history = client.get("/api/history", params={"days": 7}).json()
    assert any(track["name"] == "Backup" for track in history["tracks"])
    assert client.get("/api/entry", params={"track_id": selected}).status_code == 404


def test_admin_can_set_logo_and_banner_colour(client):
    branding = client.get("/api/branding")
    assert branding.status_code == 200
    assert branding.json() == {"banner_color": "#1f3d36", "has_logo": False}
    assert client.put("/api/branding", json={"banner_color": "#112233"}).status_code == 401

    login(client)
    rejected = client.put("/api/branding", json={"banner_color": "blue"})
    assert rejected.status_code == 422
    png = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    saved = client.put("/api/branding", json={"banner_color": "#0B3D91", "logo_data_url": png})
    assert saved.status_code == 200
    assert saved.json() == {"banner_color": "#0b3d91", "has_logo": True}
    logo = client.get("/api/branding/logo")
    assert logo.status_code == 200
    assert logo.headers["content-type"].startswith("image/png")
    assert logo.content.startswith(b"\x89PNG")

    removed = client.put("/api/branding", json={"remove_logo": True})
    assert removed.json()["has_logo"] is False
    assert client.get("/api/branding/logo").status_code == 404


def test_add_track_and_reject_duplicate(client):
    login(client)
    created = client.post("/api/tracks", json={"name": "Network"})
    assert created.status_code == 201
    duplicate = client.post("/api/tracks", json={"name": "network"})
    assert duplicate.status_code == 409
    board = client.get("/api/board").json()
    assert [track["name"] for track in board["tracks"]][-1] == "Network"
    assert board["summary"]["missing"] == 7
