"""Persist completed building detections separately from user-saved pins."""
import json

from pin_store import connect


def init_db():
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS detection_runs (
                id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id),
                status TEXT NOT NULL,
                years INTEGER NOT NULL,
                polygon_json TEXT NOT NULL,
                points_json TEXT NOT NULL DEFAULT '[]',
                building_count INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS detection_runs_owner
                ON detection_runs(owner_id, id DESC);
        """)


def start_run(owner_id, years, polygon):
    with connect() as db:
        row = db.execute(
            "INSERT INTO detection_runs(owner_id,status,years,polygon_json) VALUES (?,?,?,?)",
            (owner_id, "running", years, json.dumps(polygon, ensure_ascii=False)),
        )
        return row.lastrowid


def finish_run(run_id, status, points, message):
    buildings = [point for point in points if point.get("decision") == "new"
                 and isinstance(point.get("outline"), list) and len(point["outline"]) >= 3]
    with connect() as db:
        db.execute("""UPDATE detection_runs SET status=?,points_json=?,building_count=?,
                      message=?,completed_at=CURRENT_TIMESTAMP WHERE id=?""",
                   (status, json.dumps(buildings, ensure_ascii=False), len(buildings), message, run_id))


def list_runs(owner_id):
    with connect() as db:
        rows = db.execute("""SELECT id,status,years,building_count,message,created_at,completed_at
                             FROM detection_runs WHERE owner_id=? AND status IN ('done','partial')
                             ORDER BY id DESC""", (owner_id,)).fetchall()
    return [dict(row) for row in rows]


def get_run(owner_id, run_id):
    with connect() as db:
        row = db.execute("""SELECT id,status,years,polygon_json,points_json,building_count,
                            message,created_at,completed_at FROM detection_runs
                            WHERE id=? AND owner_id=? AND status IN ('done','partial')""",
                         (run_id, owner_id)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["polygon"] = json.loads(result.pop("polygon_json"))
    result["points"] = json.loads(result.pop("points_json"))
    return result
