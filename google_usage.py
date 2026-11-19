"""アプリからの Google 地図表示回数を月ごとに数える。"""
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def reserve_map_load(limit=9500, db_path=None, now=None):
    # Google の利用集計に合わせて米国太平洋時間で月を区切る。
    month = (now or datetime.now(ZoneInfo("America/Los_Angeles"))).strftime("%Y-%m")
    path = Path(db_path or (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AerialComparison" / "google_usage.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=10)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS usage (month TEXT PRIMARY KEY, loads INTEGER NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("SELECT loads FROM usage WHERE month = ?", (month,)).fetchone()
        if count and count[0] >= limit:
            return False
        db.execute("INSERT INTO usage VALUES (?, 1) ON CONFLICT(month) DO UPDATE SET loads = loads + 1", (month,))
        return True
