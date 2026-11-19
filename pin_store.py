"""Account and saved-pin persistence. The database is local and never committed."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash
from paths import ROOT, configured_path

DB_PATH = configured_path("AKAIITO_DB_PATH", ROOT / "akaiito.sqlite3")


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS pins (
                id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
                lat REAL NOT NULL, lng REAL NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                shared INTEGER NOT NULL DEFAULT 0, deleted_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS pins_owner ON pins(owner_id, deleted_at);
            CREATE INDEX IF NOT EXISTS pins_shared ON pins(shared, deleted_at);
            CREATE TABLE IF NOT EXISTS pin_history (
                id INTEGER PRIMARY KEY, pin_id INTEGER NOT NULL REFERENCES pins(id),
                actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL,
                title TEXT NOT NULL, note TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
                shared INTEGER NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)


def create_user(username, password):
    with connect() as db:
        cur = db.execute("INSERT INTO users(username,password_hash) VALUES (?,?)",
                         (username, generate_password_hash(password)))
        return cur.lastrowid


def authenticate(username, password):
    with connect() as db:
        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return row if row and check_password_hash(row["password_hash"], password) else None


def account(user_id):
    with connect() as db:
        row = db.execute("SELECT id,username,created_at FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def change_username(user_id, username, current_password):
    with connect() as db:
        user = db.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], current_password):
            return False
        db.execute("UPDATE users SET username=? WHERE id=?", (username, user_id))
    return True


def change_password(user_id, current_password, new_password):
    with connect() as db:
        user = db.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], current_password):
            return False
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (generate_password_hash(new_password), user_id))
    return True


def list_pins(user_id):
    with connect() as db:
        rows = db.execute("""SELECT p.id,p.owner_id,u.username AS owner,p.lat,p.lng,p.title,p.note,
                          p.shared,p.created_at,p.updated_at FROM pins p JOIN users u ON u.id=p.owner_id
                          WHERE p.deleted_at IS NULL AND (p.owner_id=? OR p.shared=1)
                          ORDER BY p.updated_at DESC,p.id DESC""", (user_id,)).fetchall()
    return [dict(row) for row in rows]


def get_pin(pin_id, user_id):
    with connect() as db:
        row = db.execute("SELECT * FROM pins WHERE id=? AND owner_id=? AND deleted_at IS NULL", (pin_id, user_id)).fetchone()
    return row


def record(db, pin, actor_id, action):
    db.execute("""INSERT INTO pin_history(pin_id,actor_id,action,title,note,lat,lng,shared)
                  VALUES (?,?,?,?,?,?,?,?)""", (pin["id"], actor_id, action, pin["title"],
                  pin["note"], pin["lat"], pin["lng"], pin["shared"]))


def save_pin(user_id, values, pin_id=None):
    with connect() as db:
        if pin_id is None:
            cur = db.execute("""INSERT INTO pins(owner_id,lat,lng,title,note,shared) VALUES (?,?,?,?,?,?)""",
                             (user_id, values["lat"], values["lng"], values["title"], values["note"], values["shared"]))
            pin_id, action = cur.lastrowid, "created"
        else:
            cur = db.execute("""UPDATE pins SET lat=?,lng=?,title=?,note=?,shared=?,updated_at=CURRENT_TIMESTAMP
                                WHERE id=? AND owner_id=? AND deleted_at IS NULL""",
                             (values["lat"], values["lng"], values["title"], values["note"], values["shared"], pin_id, user_id))
            if not cur.rowcount:
                return None
            action = "updated"
        pin = db.execute("SELECT * FROM pins WHERE id=?", (pin_id,)).fetchone()
        record(db, pin, user_id, action)
        return dict(pin)


def delete_pin(user_id, pin_id):
    with connect() as db:
        cur = db.execute("UPDATE pins SET deleted_at=CURRENT_TIMESTAMP WHERE id=? AND owner_id=? AND deleted_at IS NULL", (pin_id, user_id))
        if not cur.rowcount:
            return False
        pin = db.execute("SELECT * FROM pins WHERE id=?", (pin_id,)).fetchone()
        record(db, pin, user_id, "deleted")
        return True


def history(pin_id, user_id):
    with connect() as db:
        pin = db.execute("SELECT owner_id,shared FROM pins WHERE id=? AND deleted_at IS NULL", (pin_id,)).fetchone()
        if not pin or (pin["owner_id"] != user_id and not pin["shared"]):
            return None
        rows = db.execute("""SELECT h.action,h.title,h.note,h.lat,h.lng,h.shared,h.recorded_at,u.username AS actor
                            FROM pin_history h JOIN users u ON u.id=h.actor_id
                            WHERE h.pin_id=? ORDER BY h.id DESC""", (pin_id,)).fetchall()
    return [dict(row) for row in rows]


def recent_history(user_id, limit=100):
    with connect() as db:
        rows = db.execute("""SELECT h.pin_id,h.action,h.title,h.note,h.lat,h.lng,h.shared,
                            h.recorded_at,p.deleted_at FROM pin_history h
                            JOIN pins p ON p.id=h.pin_id
                            WHERE p.owner_id=? ORDER BY h.id DESC LIMIT ?""", (user_id, limit)).fetchall()
    return [dict(row) for row in rows]
