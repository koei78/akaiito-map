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
            CREATE TABLE IF NOT EXISTS checked_buildings (
                user_id INTEGER NOT NULL REFERENCES users(id),
                location_key TEXT NOT NULL,
                checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, location_key)
            );
            CREATE TABLE IF NOT EXISTS compared_cells (
                id INTEGER PRIMARY KEY, years INTEGER NOT NULL, effort TEXT NOT NULL, cache_day TEXT NOT NULL,
                south REAL NOT NULL, west REAL NOT NULL, north REAL NOT NULL, east REAL NOT NULL,
                points_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS compared_cells_lookup ON compared_cells(years,effort,cache_day,south,north,west,east);
            CREATE TABLE IF NOT EXISTS shared_buildings (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES detection_runs(id),
                lat REAL NOT NULL, lon REAL NOT NULL, point_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS shared_buildings_location ON shared_buildings(lat,lon);
        """)


def start_run(owner_id, years, polygon):
    with connect() as db:
        row = db.execute(
            "INSERT INTO detection_runs(owner_id,status,years,polygon_json) VALUES (?,?,?,?)",
            (owner_id, "running", years, json.dumps(polygon, ensure_ascii=False)),
        )
        return row.lastrowid


def finish_run(run_id, status, points, message):
    buildings = [point for point in points if point.get("decision") in ("new", "review")
                 and isinstance(point.get("outline"), list) and len(point["outline"]) >= 3]
    with connect() as db:
        db.execute("""UPDATE detection_runs SET status=?,points_json=?,building_count=?,
                      message=?,completed_at=CURRENT_TIMESTAMP WHERE id=?""",
                   (status, json.dumps(buildings, ensure_ascii=False),
                    sum(point["decision"] == "new" for point in buildings), message, run_id))
        db.execute('DELETE FROM shared_buildings WHERE run_id=?', (run_id,))
        db.executemany('INSERT INTO shared_buildings(run_id,lat,lon,point_json) VALUES (?,?,?,?)',
                       [(run_id, p['lat'], p['lon'], json.dumps(p, ensure_ascii=False))
                        for p in buildings if p.get('decision') == 'new'])


def save_cell(years, effort, day, bounds, points):
    with connect() as db:
        db.execute('INSERT INTO compared_cells(years,effort,cache_day,south,west,north,east,points_json) VALUES (?,?,?,?,?,?,?,?)',
                   (years, effort, day, *bounds, json.dumps(points, ensure_ascii=False)))


def find_cells(years, effort, day, bounds):
    south, west, north, east = bounds
    with connect() as db:
        rows = db.execute('''SELECT south,west,north,east,points_json FROM compared_cells
                             WHERE years=? AND effort=? AND cache_day=?
                             AND north>=? AND south<=? AND east>=? AND west<=?''',
                          (years, effort, day, south, north, west, east)).fetchall()
    return [(tuple(row[k] for k in ('south','west','north','east')), json.loads(row['points_json'])) for row in rows]


def completed_areas(years, effort, day):
    # Older history did not record the AI setting; reuse it only for the normal setting.
    if effort != 'medium':
        return []
    with connect() as db:
        rows = db.execute('''SELECT polygon_json,points_json FROM detection_runs
                             WHERE status='done' AND years=? AND date(completed_at,'localtime')=?''',
                          (years, day)).fetchall()
    areas = []
    for row in rows:
        try:
            areas.append((json.loads(row['polygon_json']), json.loads(row['points_json'])))
        except (TypeError, ValueError):
            continue
    return areas


def shared_points(bounds):
    south, west, north, east = bounds
    with connect() as db:
        rows = db.execute('SELECT point_json FROM shared_buildings WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? LIMIT 10000',
                          (south, north, west, east)).fetchall()
    return [json.loads(row['point_json']) for row in rows]


def backfill_shared():
    with connect() as db:
        rows = db.execute('''SELECT id,points_json FROM detection_runs r WHERE status IN ('done','partial')
                             AND NOT EXISTS (SELECT 1 FROM shared_buildings b WHERE b.run_id=r.id)''').fetchall()
        for row in rows:
            try:
                points = json.loads(row['points_json'])
                db.executemany('INSERT INTO shared_buildings(run_id,lat,lon,point_json) VALUES (?,?,?,?)',
                               [(row['id'], p['lat'], p['lon'], json.dumps(p,ensure_ascii=False))
                                for p in points if p.get('decision')=='new' and 'lat' in p and 'lon' in p])
            except (ValueError, TypeError, KeyError):
                continue


def list_runs(owner_id):
    with connect() as db:
        rows = db.execute("""SELECT id,status,years,building_count,points_json,message,created_at,completed_at
                             FROM detection_runs WHERE owner_id=? AND status IN ('done','partial')
                             ORDER BY id DESC""", (owner_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["review_count"] = sum(point.get("decision") == "review"
                                   for point in json.loads(item.pop("points_json")))
        result.append(item)
    return result


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
    result["review_count"] = sum(point.get("decision") == "review" for point in result["points"])
    return result
