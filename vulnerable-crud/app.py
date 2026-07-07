from __future__ import annotations

import json
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DB_PATH = ROOT / "vulnerable.db"
HOST = "127.0.0.1"
PORT = 8088


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE,
              password TEXT NOT NULL,
              role TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              owner_id INTEGER NOT NULL,
              title TEXT NOT NULL,
              body TEXT NOT NULL,
              is_public INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        user_count = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        if user_count == 0:
            db.executemany(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                (
                    ("admin", "admin123", "admin"),
                    ("alice", "password1", "user"),
                    ("bob", "password2", "user"),
                ),
            )
            db.executemany(
                """
                INSERT INTO notes (owner_id, title, body, is_public)
                VALUES (?, ?, ?, ?)
                """,
                (
                    (1, "Admin launch checklist", "Rotate these dummy passwords.", 0),
                    (2, "Alice public note", "Hello from <strong>Alice</strong>.", 1),
                    (3, "Bob private note", "Bob's private draft.", 0),
                ),
            )


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


class VulnerableHandler(BaseHTTPRequestHandler):
    server_version = "VulnerableCRUD/0.1"

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type, x-user-id")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        path, query = self.request_parts()
        if path == "/":
            self.send_file(PUBLIC / "index.html", "text/html; charset=utf-8")
        elif path == "/api/health":
            self.send_json({"ok": True, "warning": "intentionally vulnerable local lab"})
        elif path == "/api/notes":
            self.list_notes()
        elif path.startswith("/api/notes/"):
            self.get_note(path)
        elif path == "/api/search":
            self.search_notes(query)
        elif path == "/api/debug/users":
            self.debug_users()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        path, _query = self.request_parts()
        if path == "/api/login":
            self.login()
        elif path == "/api/notes":
            self.create_note()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_PUT(self) -> None:
        path, _query = self.request_parts()
        if path.startswith("/api/notes/"):
            self.update_note(path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_DELETE(self) -> None:
        path, _query = self.request_parts()
        if path.startswith("/api/notes/"):
            self.delete_note(path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def request_parts(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return unquote(parsed.path), parse_qs(parsed.query)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length == 0:
            return {}
        try:
            raw = self.rfile.read(length).decode("utf-8")
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def current_user_id(self) -> int:
        raw = self.headers.get("x-user-id", "1")
        try:
            return int(raw)
        except ValueError:
            return 1

    def note_id_from_path(self, path: str) -> int | None:
        try:
            return int(path.rsplit("/", 1)[1])
        except ValueError:
            return None

    def login(self) -> None:
        body = self.read_json()
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))

        # VULNERABLE: raw string interpolation creates real SQLite injection.
        sql = (
            "SELECT id, username, role FROM users "
            f"WHERE username = '{username}' AND password = '{password}'"
        )
        with connect() as db:
            row = db.execute(sql).fetchone()
        if row is None:
            self.send_json({"error": "invalid credentials", "query": sql}, HTTPStatus.UNAUTHORIZED)
            return
        self.send_json({"user": row_to_dict(row), "token_hint": "send X-User-Id with this id"})

    def list_notes(self) -> None:
        user_id = self.current_user_id()
        with connect() as db:
            rows = db.execute(
                """
                SELECT notes.*, users.username AS owner
                FROM notes JOIN users ON users.id = notes.owner_id
                WHERE notes.owner_id = ? OR notes.is_public = 1
                ORDER BY notes.id DESC
                """,
                (user_id,),
            ).fetchall()
        self.send_json({"notes": [row_to_dict(row) for row in rows], "acting_user_id": user_id})

    def get_note(self, path: str) -> None:
        note_id = self.note_id_from_path(path)
        if note_id is None:
            self.send_error(HTTPStatus.BAD_REQUEST, "bad note id")
            return
        # VULNERABLE: IDOR. No owner check before returning a private note.
        with connect() as db:
            row = db.execute(
                """
                SELECT notes.*, users.username AS owner
                FROM notes JOIN users ON users.id = notes.owner_id
                WHERE notes.id = ?
                """,
                (note_id,),
            ).fetchone()
        if row is None:
            self.send_error(HTTPStatus.NOT_FOUND, "note not found")
            return
        self.send_json({"note": row_to_dict(row)})

    def search_notes(self, query: dict[str, list[str]]) -> None:
        q = query.get("q", [""])[0]
        # VULNERABLE: raw string interpolation creates real SQLite injection.
        sql = (
            "SELECT notes.*, users.username AS owner "
            "FROM notes JOIN users ON users.id = notes.owner_id "
            f"WHERE notes.title LIKE '%{q}%' OR notes.body LIKE '%{q}%' "
            "ORDER BY notes.id DESC"
        )
        try:
            with connect() as db:
                rows = db.execute(sql).fetchall()
        except sqlite3.Error as error:
            self.send_json({"error": str(error), "query": sql}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"query": sql, "notes": [row_to_dict(row) for row in rows]})

    def create_note(self) -> None:
        body = self.read_json()
        user_id = self.current_user_id()
        # VULNERABLE: mass assignment lets clients choose owner_id and is_public.
        owner_id = int(body.get("owner_id", user_id))
        title = str(body.get("title", "Untitled"))
        note_body = str(body.get("body", ""))
        is_public = 1 if body.get("is_public", False) else 0
        with connect() as db:
            cursor = db.execute(
                "INSERT INTO notes (owner_id, title, body, is_public) VALUES (?, ?, ?, ?)",
                (owner_id, title, note_body, is_public),
            )
            note_id = cursor.lastrowid
            row = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        self.send_json({"note": row_to_dict(row)}, HTTPStatus.CREATED)

    def update_note(self, path: str) -> None:
        note_id = self.note_id_from_path(path)
        if note_id is None:
            self.send_error(HTTPStatus.BAD_REQUEST, "bad note id")
            return
        body = self.read_json()
        # VULNERABLE: IDOR and mass assignment. Any user can modify ownership/public state.
        with connect() as db:
            existing = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            if existing is None:
                self.send_error(HTTPStatus.NOT_FOUND, "note not found")
                return
            title = str(body.get("title", existing["title"]))
            note_body = str(body.get("body", existing["body"]))
            owner_id = int(body.get("owner_id", existing["owner_id"]))
            is_public = 1 if body.get("is_public", existing["is_public"]) else 0
            db.execute(
                """
                UPDATE notes
                SET title = ?, body = ?, owner_id = ?, is_public = ?
                WHERE id = ?
                """,
                (title, note_body, owner_id, is_public, note_id),
            )
            row = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        self.send_json({"note": row_to_dict(row)})

    def delete_note(self, path: str) -> None:
        note_id = self.note_id_from_path(path)
        if note_id is None:
            self.send_error(HTTPStatus.BAD_REQUEST, "bad note id")
            return
        # VULNERABLE: IDOR. No owner check before delete.
        with connect() as db:
            db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.send_json({"deleted": note_id})

    def debug_users(self) -> None:
        # VULNERABLE: debug data leak includes plaintext passwords.
        with connect() as db:
            rows = db.execute("SELECT id, username, password, role FROM users").fetchall()
        self.send_json({"users": [row_to_dict(row) for row in rows]})

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "file not found")
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), VulnerableHandler)
    print(f"Vulnerable CRUD lab running at http://{HOST}:{PORT}")
    print("Local testing only. Do not expose this server publicly.")
    server.serve_forever()


if __name__ == "__main__":
    main()
