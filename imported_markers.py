"""Read-only spatial access to the exported sales-map marker snapshot."""

import json
import math
import sqlite3
from pathlib import Path
from paths import data_dir

DEFAULT_DB = data_dir() / "imported_markers.sqlite3"
LAYERS = ("markers", "existing_customers", "parking_spots")


def import_json(source, target=DEFAULT_DB):
    source, target = Path(source), Path(target)
    data = json.loads(source.read_text(encoding="utf-8"))
    temporary = target.with_suffix(".building.sqlite3")
    temporary.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript("""
            CREATE TABLE markers (
                id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                layer TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                label TEXT NOT NULL,
                details TEXT NOT NULL,
                UNIQUE(layer, source_id)
            );
            CREATE VIRTUAL TABLE locations USING rtree(id, south, north, west, east);
        """)
        next_id = 0
        for layer in LAYERS:
            records = data[layer]
            if len(records) != data["counts"][layer]:
                raise ValueError(f"Count mismatch: {layer}")
            for record in records:
                lat, lng = record["lat"], record["lng"]
                if not (math.isfinite(lat) and math.isfinite(lng)
                        and -90 <= lat <= 90 and -180 <= lng <= 180):
                    raise ValueError(f"Invalid coordinates: {layer}")
                next_id += 1
                label = record.get("label") or record.get("name") or "名称なし"
                connection.execute(
                    "INSERT INTO markers VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (next_id, str(record["id"]), layer, lat, lng, label,
                     json.dumps(record, ensure_ascii=False, separators=(",", ":"))),
                )
                connection.execute(
                    "INSERT INTO locations VALUES (?, ?, ?, ?, ?)",
                    (next_id, lat, lat, lng, lng),
                )
        connection.commit()
    finally:
        connection.close()
    temporary.replace(target)
    return next_id


def query(bounds, db=DEFAULT_DB, point_limit=300):
    """Return only visible points, or compact counts for a crowded viewport."""
    south, west, north, east = bounds
    if not Path(db).is_file():
        return {"mode": "missing", "items": [], "count": 0}
    connection = sqlite3.connect(f"file:{Path(db).resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        where = "l.north >= ? AND l.south <= ? AND l.east >= ? AND l.west <= ?"
        args = (south, north, west, east)
        join = "FROM locations l JOIN markers m ON m.id = l.id"
        count = connection.execute(f"SELECT COUNT(*) {join} WHERE {where}", args).fetchone()[0]
        if count <= point_limit:
            rows = connection.execute(
                f"SELECT m.source_id, m.layer, m.lat, m.lng, m.label, m.details "
                f"{join} WHERE {where} ORDER BY m.id", args,
            ).fetchall()
            return {"mode": "points", "count": count, "items": [
                {"id": row["source_id"], "layer": row["layer"],
                 "lat": row["lat"], "lng": row["lng"], "label": row["label"],
                 "details": json.loads(row["details"])} for row in rows
            ]}
        cell_lat = max((north - south) / 10, 1e-9)
        cell_lng = max((east - west) / 10, 1e-9)
        rows = connection.execute(
            f"SELECT m.layer, CAST((m.lat - ?) / ? AS INTEGER) AS y, "
            f"CAST((m.lng - ?) / ? AS INTEGER) AS x, COUNT(*) AS count, "
            f"AVG(m.lat) AS lat, AVG(m.lng) AS lng {join} WHERE {where} "
            f"GROUP BY m.layer, y, x",
            (south, cell_lat, west, cell_lng, *args),
        ).fetchall()
        return {"mode": "clusters", "count": count, "items": [dict(row) for row in rows]}
    finally:
        connection.close()


def density(bounds, db=DEFAULT_DB, cells=12):
    """Count visible markers in a small fixed grid, including empty cells."""
    south, west, north, east = bounds
    if not Path(db).is_file():
        return {"mode": "missing", "count": 0, "grid": None}
    step_lat = (north - south) / cells
    step_lng = (east - west) / cells
    connection = sqlite3.connect(f"file:{Path(db).resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT min(?, max(0, CAST((m.lat - ?) / ? AS INTEGER))) AS y, "
            "min(?, max(0, CAST((m.lng - ?) / ? AS INTEGER))) AS x, "
            "COUNT(*) AS total "
            "FROM locations l JOIN markers m ON m.id = l.id "
            "WHERE l.north >= ? AND l.south <= ? AND l.east >= ? AND l.west <= ? "
            "GROUP BY y, x",
            (cells - 1, south, step_lat, cells - 1, west, step_lng,
             south, north, west, east),
        ).fetchall()
    finally:
        connection.close()
    counts = [0] * (cells * cells)
    for y, x, total in rows:
        counts[y * cells + x] = total
    return {
        "mode": "density", "count": sum(counts),
        "grid": {"south": south, "west": west, "north": north, "east": east,
                 "rows": cells, "columns": cells, "counts": counts},
    }
