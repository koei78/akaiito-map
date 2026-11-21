import csv
import gzip
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.request import urlopen

from flask import Flask, Response, jsonify, request, send_file, render_template, redirect, session
import secrets
import sqlite3
from shapely.geometry import Polygon, Point, box
from shapely.ops import unary_union
from google_usage import reserve_map_load
import pin_store
import detection_store
import imported_markers
from paths import imagery_executable, data_dir, configured_path

app = Flask(__name__)


def session_secret():
    configured = os.environ.get("AKAIITO_SECRET_KEY")
    if configured:
        return configured
    path = data_dir() / "session-secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as file:
            key = secrets.token_hex(32)
            file.write(key)
            return key
    except FileExistsError:
        return path.read_text(encoding="ascii").strip()


app.secret_key = session_secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("AKAIITO_HTTPS") == "1")
pin_store.init_db()
detection_store.init_db()
detection_store.backfill_shared()
base = Path(__file__).resolve().parent
exe = imagery_executable()
state = {"status": "idle", "message": "地図上で範囲を選択してください。", "points": []}
lock = threading.Lock()
ai_slots = threading.BoundedSemaphore(2)
result_dir = None
user_states = {}
user_result_dirs = {}


def get_user_state(user_id):
    if user_id not in user_states:
        if state.get('owner_id') == user_id:
            user_states[user_id] = state
            if result_dir is not None:
                user_result_dirs[user_id] = result_dir
        else:
            user_states[user_id] = {'owner_id':user_id, 'status':'idle', 'message':'地図上で範囲を選択してください。', 'points':[]}
    return user_states[user_id]


@app.before_request
def require_account():
    if request.endpoint in ("login", "register", "static", "health", "service_worker"):
        return None
    if not session.get("user_id"):
        if request.path.startswith("/api/"):
            return jsonify(error="ログインが必要です。"), 401
        token = request.args.get('shared_route', '')
        if request.path == '/google' and re.fullmatch(r'[A-Za-z0-9_-]{32}', token):
            session['pending_shared_route'] = token
        return redirect("/login")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-CSRF-Token") != session.get("csrf"):
            return jsonify(error="画面を再読み込みしてください。"), 403


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/sw.js")
def service_worker():
    response = send_file(base / "static" / "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user = pin_store.authenticate(username, request.form.get("password", ""))
        if user:
            pending = session.get('pending_shared_route')
            session.clear()
            session["user_id"], session["username"], session["csrf"] = user["id"], user["username"], secrets.token_urlsafe(32)
            return redirect('/google?shared_route=' + pending if pending else '/')
        error = "ユーザー名またはパスワードが違います。"
    return render_template("auth.html", action="login", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not 3 <= len(username) <= 40 or not all(c.isascii() and (c.isalnum() or c in "_-") for c in username):
            error = "ユーザー名は3〜40文字の英数字・_・-で入力してください。"
        elif len(password) < 8:
            error = "パスワードは8文字以上で入力してください。"
        else:
            try:
                user_id = pin_store.create_user(username, password)
                pending = session.get('pending_shared_route')
                session.clear()
                session["user_id"], session["username"], session["csrf"] = user_id, username, secrets.token_urlsafe(32)
                return redirect('/google?shared_route=' + pending if pending else '/')
            except sqlite3.IntegrityError:
                error = "そのユーザー名は使用されています。"
    return render_template("auth.html", action="register", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/api/me")
def me():
    account = pin_store.account(session["user_id"])
    return jsonify(id=session["user_id"], username=session["username"], csrf=session["csrf"],
                   show_shared_pins=bool(account and account["show_shared_pins"]))


@app.get("/api/account")
def account_details():
    return jsonify(pin_store.account(session["user_id"]))


@app.patch("/api/account/settings")
def account_settings():
    data = request.get_json(silent=True) or {}
    enabled = data.get("show_shared_pins")
    if not isinstance(enabled, bool):
        return jsonify(error="表示設定を確認してください。"), 400
    pin_store.set_show_shared_pins(session["user_id"], enabled)
    return jsonify(show_shared_pins=enabled)


@app.patch("/api/account/username")
def account_username():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("current_password", ""))
    if not 3 <= len(username) <= 40 or not all(c.isascii() and (c.isalnum() or c in "_-") for c in username):
        return jsonify(error="ユーザー名は3〜40文字の英数字・_・-で入力してください。"), 400
    try:
        if not pin_store.change_username(session["user_id"], username, password):
            return jsonify(error="現在のパスワードが違います。"), 403
    except sqlite3.IntegrityError:
        return jsonify(error="そのユーザー名は使用されています。"), 409
    session["username"] = username
    return jsonify(username=username)


@app.post("/api/account/password")
def account_password():
    data = request.get_json(silent=True) or {}
    current = str(data.get("current_password", ""))
    new = str(data.get("new_password", ""))
    if len(new) < 8:
        return jsonify(error="新しいパスワードは8文字以上で入力してください。"), 400
    if not pin_store.change_password(session["user_id"], current, new):
        return jsonify(error="現在のパスワードが違います。"), 403
    return jsonify(ok=True)


@app.get("/api/pins")
def pins_list():
    return jsonify(pin_store.list_pins(session["user_id"]))


@app.get("/api/imported-markers")
def imported_markers_viewport():
    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
        if not all(math.isfinite(value) for value in (south, west, north, east)):
            raise ValueError()
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        return jsonify(error="地図の表示範囲を確認してください。"), 400
    bounds = (south, west, north, east)
    if request.args.get("mode") == "density":
        response = jsonify(imported_markers.density(bounds))
    else:
        response = jsonify(imported_markers.query(bounds))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Accept-Encoding")
    if request.accept_encodings["gzip"] and response.content_length and response.content_length > 512:
        response.set_data(gzip.compress(response.get_data(), compresslevel=4))
        response.headers["Content-Encoding"] = "gzip"
    return response


@app.get("/api/imported-markers/extent")
def imported_markers_extent():
    return jsonify(imported_markers.extent())


@app.get("/api/pins/history")
def pins_recent_history():
    return jsonify(pin_store.recent_history(session["user_id"]))


def pin_values():
    data = request.get_json(silent=True) or {}
    lat, lng = float(data["lat"]), float(data["lng"])
    title, note = str(data["title"]).strip(), str(data.get("note", "")).strip()
    shared = data.get("shared", False)
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180 and 1 <= len(title) <= 100 and len(note) <= 2000 and isinstance(shared, bool)):
        raise ValueError()
    return dict(lat=lat, lng=lng, title=title, note=note, shared=int(shared))


@app.post("/api/pins")
def pins_create():
    try:
        return jsonify(pin_store.save_pin(session["user_id"], pin_values())), 201
    except (KeyError, TypeError, ValueError, OverflowError):
        return jsonify(error="ピンの内容を確認してください。"), 400


@app.put("/api/pins/<int:pin_id>")
def pins_update(pin_id):
    try:
        pin = pin_store.save_pin(session["user_id"], pin_values(), pin_id)
    except (KeyError, TypeError, ValueError, OverflowError):
        return jsonify(error="ピンの内容を確認してください。"), 400
    return (jsonify(pin) if pin else (jsonify(error="ピンが見つかりません。"), 404))


@app.delete("/api/pins/<int:pin_id>")
def pins_delete(pin_id):
    if not pin_store.delete_pin(session["user_id"], pin_id):
        return jsonify(error="ピンが見つかりません。"), 404
    return jsonify(ok=True)


@app.get("/api/pins/<int:pin_id>/history")
def pins_history(pin_id):
    rows = pin_store.history(pin_id, session["user_id"])
    return jsonify(rows) if rows is not None else (jsonify(error="履歴が見つかりません。"), 404)


def download(label, dates, zoom, bounds, folder):
    south, west, north, east = bounds
    for day in dates:
        output = folder / f"{label}_{day.isoformat()}_z{zoom}.tif"
        cmd = [str(exe), "download", "--provider=TM", f"--lower-left={south},{west}",
               f"--upper-right={north},{east}", f"--zoom={zoom}",
               f"--date={day:%Y/%m/%d}", f"--output={output}", "--exact-date"]
        result = subprocess.run(cmd, cwd=exe.parent, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180)
        match = re.search(r"(\d+)\s+out of\s+(\d+)\s+downloaded", result.stdout + result.stderr)
        if result.returncode == 0 and match and int(match[1]) > 0 and match[1] == match[2] and output.exists() and output.stat().st_size > 100:
            return output
        output.unlink(missing_ok=True)
    return None


class NoImagery(Exception):
    pass


def available_dates(bounds):
    south, west, north, east = bounds
    lat, lon = (south + north) / 2, (west + east) / 2
    result = subprocess.run([str(exe), "info", f"--location={lat},{lon}", "--zoom=20"],
                            cwd=exe.parent, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    if result.returncode:
        raise RuntimeError("撮影日の確認に失敗: " + (result.stderr or result.stdout)[-300:])
    found = set(re.findall(r"imagery_date\s*=\s*(\d{4}/\d{2}/\d{2})", (result.stdout + result.stderr)))
    return sorted(date.fromisoformat(day.replace("/", "-")) for day in found)


def deduplicate(points):
    """大きく重なる外周を同じ建物として扱う。"""
    unique = []
    buckets = {}
    large_shapes = set()
    step = 0.0005
    for point in points:
        try:
            shape = Polygon([(lon, lat) for lat, lon in point["outline"]]).buffer(0)
            if shape.is_empty or shape.area <= 0:
                continue
            minx, miny, maxx, maxy = shape.bounds
            x1, x2, y1, y2 = (math.floor(minx/step), math.floor(maxx/step),
                              math.floor(miny/step), math.floor(maxy/step))
            # Unusually large outlines fall back to the conservative full scan.
            cells = [(x,y) for x in range(x1,x2+1) for y in range(y1,y2+1)] if (x2-x1+1)*(y2-y1+1) <= 256 else []
            candidates = (large_shapes.union(*(buckets.get(cell, set()) for cell in cells))
                          if cells else set(range(len(unique))))
            duplicate = False
            for index in sorted(candidates):
                existing, other = unique[index]
                if not shape.intersects(other):
                    continue
                if shape.intersection(other).area / min(shape.area, other.area) >= 0.6:
                    duplicate = True
                    if point.get("decision") == "new" and existing.get("decision") != "new":
                        unique[index] = (point, shape)
                        if not cells:
                            large_shapes.add(index)
                        for cell in cells:
                            buckets.setdefault(cell, set()).add(index)
                    break
            if not duplicate:
                unique.append((point, shape))
                index = len(unique)-1
                if not cells:
                    large_shapes.add(index)
                for cell in cells:
                    buckets.setdefault(cell, set()).add(index)
        except (KeyError, TypeError, ValueError):
            continue
    return [point for point, _ in unique]


def run_comparison(polygon, cells, years, ai_effort="medium", run_id=None, job_state=None):
    global result_dir
    state = job_state if job_state is not None else globals()['state']
    folder = Path(tempfile.mkdtemp(prefix="aerial_"))
    if job_state is None:
        result_dir = folder
    else:
        user_result_dirs[state['owner_id']] = folder
    def save_history(status, points, message):
        if run_id is None:
            return
        try:
            detection_store.finish_run(run_id, status, points, message)
        except Exception as error:
            print(f"検出履歴の保存に失敗しました: {error}", file=sys.stderr)
    try:
        today = date.today()
        try:
            target = today.replace(year=today.year - years)
        except ValueError:
            target = today.replace(year=today.year - years, day=28)

        def process(item):
            index, bounds = item
            part = folder / str(index)
            part.mkdir()
            dates = available_dates(bounds)
            if len(dates) < 2:
                raise NoImagery("この範囲の中心では異なる2時期の画像が見つかりません")
            newest = sorted(dates, reverse=True)
            oldest = sorted(dates[:-1], key=lambda day: abs((day - target).days))
            with ThreadPoolExecutor(max_workers=2) as pool:
                new_future = pool.submit(download, "latest", newest, 20, bounds, part)
                old_future = pool.submit(download, "15years_ago", oldest, 20, bounds, part)
                new, old = new_future.result(), old_future.result()
            if not new or not old:
                raise NoImagery("ズーム20で異なる2時期の画像が範囲全体に揃いません")
            new_day = date.fromisoformat(new.name[7:17])
            old_day = date.fromisoformat(old.name[12:22])
            if old_day >= new_day:
                old.unlink(missing_ok=True)
                old = download("15years_ago", [d for d in oldest if d < new_day], 20, bounds, part)
                if old is None:
                    raise NoImagery("現在画像より古い画像が範囲全体に揃いません")
            env = os.environ.copy()
            env.update(PHOTO_IMAGES_DIR=str(part), PHOTO_OUTPUT_DIR=str(part), PHOTO_REASONING_EFFORT=ai_effort,
                       PYTHONIOENCODING="utf-8")
            with ai_slots:
                run = subprocess.run([sys.executable, str(base / "compare_precise.py")], cwd=base, env=env,
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            if run.returncode:
                detail = "AIの利用上限または混雑（429）。時間をおいて再試行してください" if "429" in run.stderr else run.stderr[-500:]
                raise RuntimeError("比較失敗: " + detail)
            with (part / "coordinates.csv").open(encoding="utf-8-sig", newline="") as file:
                points = [{"lat": float(row["緯度"]), "lon": float(row["経度"]), "tile": index,
                           "old_date": old.name[12:22], "new_date": new.name[7:17], "outline": json.loads(row["外周"]),
                           "decision": row["判定"], "reason": row["理由"]}
                          for row in csv.DictReader(file)]
            if points:
                helper = sys.executable
                try:
                    match = subprocess.run([str(helper), "-X", "utf8", str(base / "gsi_precise.py")],
                                           input=json.dumps(points), capture_output=True, text=True,
                                           encoding="utf-8", errors="replace", timeout=120, check=True)
                    points = json.loads(match.stdout)
                except (OSError, subprocess.SubprocessError, ValueError):
                    for point in points:
                        point.update(outline_type="ai", source="国土地理院のデータを取得できないため、AIの推定輪郭（要確認）")
            return points

        points, failures = [], []
        next_id = len(cells)
        completed = 0
        skipped = 0
        prior_areas = []
        for vertices, entries in detection_store.completed_areas(years, ai_effort, today.isoformat()):
            try:
                area = Polygon([(float(lon), float(lat)) for lat, lon in vertices])
                if area.is_valid and not area.is_empty and area.intersects(polygon):
                    prior_areas.append((area, entries))
            except (ValueError, TypeError):
                continue
        # 再確認を含む AI 判定の同時実行数を抑えて処理を安定させる。
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {}
            for index, bounds in enumerate(cells):
                cached = detection_store.find_cells(years, ai_effort, today.isoformat(), bounds)
                requested = polygon.intersection(box(bounds[1],bounds[0],bounds[3],bounds[2]))
                areas = [(box(b[1],b[0],b[3],b[2]), entries) for b, entries in cached] + prior_areas
                covers = unary_union([area for area, _ in areas]) if areas else None
                if covers is not None and covers.covers(requested):
                    reused = [dict(p, tile=None, reused=True) for _, entries in areas for p in entries
                              if polygon.covers(Point(p['lon'],p['lat'])) and requested.covers(Point(p['lon'],p['lat']))]
                    points = deduplicate(points + reused)
                    skipped += 1
                else:
                    futures[pool.submit(process, (index, bounds))] = (index, bounds, 0)
            while futures:
                future = next(as_completed(futures))
                index, bounds, depth = futures.pop(future)
                try:
                    result = future.result()
                    detection_store.save_cell(years, ai_effort, today.isoformat(), bounds, result)
                    points = deduplicate(points + [p for p in result if polygon.covers(Point(p['lon'], p['lat']))])
                    completed += 1
                except NoImagery as error:
                    if depth < 2:
                        south, west, north, east = bounds
                        mid_lat, mid_lon = (south + north) / 2, (west + east) / 2
                        for child in [(south, west, mid_lat, mid_lon), (south, mid_lon, mid_lat, east),
                                      (mid_lat, west, north, mid_lon), (mid_lat, mid_lon, north, east)]:
                            if polygon.intersects(box(child[1], child[0], child[3], child[2])):
                                futures[pool.submit(process, (next_id, child))] = (next_id, child, depth + 1)
                                next_id += 1
                    else:
                        failures.append(f"区画{index + 1}: {error}")
                except Exception as error:
                    failures.append(f"区画{index + 1}: {error}")
                for i, point in enumerate(points, 1):
                    point["number"] = i
                state.update(points=points.copy(), message=f"処理済み {completed}区画、再利用 {skipped}区画、残り {len(futures)}区画（取得不可 {len(failures)}）")
        for i, point in enumerate(points, 1):
            point['number'] = i
        final_status = "done" if not failures else "partial"
        new_count = sum(point.get("decision") == "new" for point in points)
        review_count = sum(point.get("decision") == "review" for point in points)
        final_message = f"完了 {completed}区画、再利用 {skipped}区画、新築候補 {new_count}件、要確認 {review_count}件。" + \
                        (f" 取得・処理できなかった範囲: {len(failures)}区画。" + " / ".join(failures[:5]) if failures else "")
        save_history(final_status, points, final_message)
        state.update(status=final_status, points=points, message=final_message)
    except Exception as error:
        saved_points = state.get("points", []).copy()
        save_history("partial" if saved_points else "error", saved_points, str(error))
        state.update(status="partial" if saved_points else "error", message=str(error), points=saved_points)


@app.post("/compare")
def compare():
    if not exe.is_file():
        return jsonify(error="航空写真の取得ツールがありません。AKAIITO_IMAGERY_EXE を設定してください。"), 503
    try:
        vertices = request.json["polygon"]
        years = int(request.json.get("years", 15))
        ai_effort = request.json.get("ai_effort", "medium")
        if ai_effort not in ("medium", "high"):
            raise ValueError()
        if not 1 <= years <= 40:
            raise ValueError()
        if not 3 <= len(vertices) <= 3000:
            raise ValueError()
        polygon = Polygon([(float(lon), float(lat)) for lat, lon in vertices])
        west, south, east, north = polygon.bounds
        if not polygon.is_valid or polygon.is_empty or not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
            raise ValueError()
        dy = 160 / 111320
        dx = 160 / (111320 * math.cos(math.radians((south + north) / 2)))
        # Keep each image at 160m while overlapping adjacent images by 16m.
        # This gives buildings on a cell edge another complete view.
        stride_y, stride_x = dy * 0.9, dx * 0.9
        margin_y, margin_x = dy * 0.05, dx * 0.05
        rows = max(1, math.ceil((north - south + margin_y - dy) / stride_y) + 1)
        cols = max(1, math.ceil((east - west + margin_x - dx) / stride_x) + 1)
        if rows * cols > 400:
            return jsonify(error="範囲が広すぎます。分割数が400以下になるようにしてください。"), 400
        cells = []
        for row in range(rows):
            for col in range(cols):
                s, w = south - margin_y + row * stride_y, west - margin_x + col * stride_x
                n, e = s + dy, w + dx
                if polygon.intersects(box(w, s, e, n)):
                    cells.append((s, w, n, e))
    except (TypeError, ValueError, KeyError, AttributeError):
        return jsonify(error="交差しない線で範囲を囲んでください。"), 400
    with lock:
        job_state = get_user_state(session['user_id'])
        if job_state["status"] == "running":
            return jsonify(error="比較を実行中です。"), 409
        try:
            run_id = detection_store.start_run(session["user_id"], years, vertices)
        except sqlite3.Error:
            return jsonify(error="検出履歴を準備できませんでした。"), 503
        job_state.update(owner_id=session["user_id"], status="running", message=f"{len(cells)}区画を並行処理します。", points=[])
        threading.Thread(target=run_comparison, args=(polygon, cells, years, ai_effort, run_id, job_state), daemon=True).start()
    return jsonify(job_state)


@app.get("/api/detections/history")
def detection_history():
    return jsonify(detection_store.list_runs(session["user_id"]))


@app.get('/api/shared-detections')
def shared_detections():
    try:
        south, west, north, east = (float(request.args[key]) for key in ('south','west','north','east'))
        if not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        return jsonify(error='表示範囲を確認してください。'), 400
    # Skip overly broad views rather than sending thousands of outlines to mobile devices.
    if north - south > .1 or east - west > .1:
        return jsonify(points=[], zoom_in=True)
    points = deduplicate(detection_store.shared_points((south,west,north,east)))
    for point in points:
        point.pop('tile', None)
        point['shared_result'] = True
    return jsonify(points=points, zoom_in=False)


@app.get("/api/detections/history/<int:run_id>")
def detection_history_detail(run_id):
    run = detection_store.get_run(session["user_id"], run_id)
    return jsonify(run) if run else (jsonify(error="検出履歴が見つかりません。"), 404)


@app.post("/buildings")
def buildings_in_area():
    try:
        vertices = request.get_json()["polygon"]
        if not 3 <= len(vertices) <= 3000:
            raise ValueError("範囲が正しくありません。")
        vertices = [[float(lat), float(lon)] for lat, lon in vertices]
        region = Polygon([(lon, lat) for lat, lon in vertices])
        west, south, east, north = region.bounds
        if not region.is_valid or region.is_empty or not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
            raise ValueError("交差しない線で範囲を囲んでください。")
        helper = sys.executable
        result = subprocess.run([str(helper), "-X", "utf8", str(base / "gsi_area.py")],
                                input=json.dumps(vertices), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=90, check=True)
        return jsonify(json.loads(result.stdout))
    except (TypeError, KeyError, ValueError, AttributeError) as error:
        return jsonify(error=str(error) or "範囲が正しくありません。"), 400
    except (OSError, subprocess.SubprocessError):
        return jsonify(error="建物の外周データを取得できませんでした。"), 503


@app.get("/status")
def status():
    return jsonify(get_user_state(session['user_id']))


@app.post('/api/shared-routes')
def create_shared_route():
    payload = request.get_json(silent=True)
    try:
        if not isinstance(payload, dict) or not isinstance(payload.get('routes'), list) or len(payload['routes']) not in (1, 2):
            raise ValueError()
        def valid_position(pos):
            return (isinstance(pos, dict) and isinstance(pos.get('lat'), (int, float))
                    and isinstance(pos.get('lng'), (int, float))
                    and math.isfinite(pos['lat']) and math.isfinite(pos['lng'])
                    and -90 <= pos['lat'] <= 90 and -180 <= pos['lng'] <= 180)
        if not valid_position(payload.get('origin')):
            raise ValueError()
        for route in payload['routes']:
            if (not isinstance(route, dict) or not isinstance(route.get('path'), list)
                    or not 1 <= len(route['path']) <= 100000 or not all(map(valid_position, route['path']))
                    or not isinstance(route.get('visits'), list) or not route['visits']
                    or not all(isinstance(v, dict) and valid_position(v.get('position'))
                               and isinstance(v.get('number'), int) for v in route['visits'])
                    or any(not isinstance(route.get(k), (int, float)) or not math.isfinite(route[k]) or route[k] < 0
                           for k in ('distance', 'duration'))):
                raise ValueError()
        shared = payload.get('shared', True)
        if not isinstance(shared, bool):
            raise ValueError()
        payload = {k: payload[k] for k in ('origin', 'routes')}
        data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        if len(data.encode('utf-8')) > 2_000_000:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify(error='経路の内容を確認してください。'), 400
    token = secrets.token_urlsafe(24)
    with pin_store.connect() as db:
        db.execute('INSERT INTO shared_routes(token,owner_id,payload,shared) VALUES (?,?,?,?)',
                   (token, session['user_id'], data, int(shared)))
    return jsonify(token=token), 201


@app.get('/api/shared-routes/<token>')
def get_shared_route(token):
    with pin_store.connect() as db:
        row = db.execute('SELECT payload,owner_id,shared FROM shared_routes WHERE token=?', (token,)).fetchone()
        if row and row['owner_id'] != session['user_id']:
            if not row['shared']:
                row = None
            else:
                db.execute('INSERT OR IGNORE INTO route_viewers(token,user_id) VALUES (?,?)', (token, session['user_id']))
    return (jsonify(json.loads(row['payload'])) if row else
            (jsonify(error='共有経路が見つかりません。'), 404))


@app.get('/api/route-history')
def route_history():
    with pin_store.connect() as db:
        rows = db.execute('''SELECT r.token,r.owner_id,r.payload,r.shared,r.created_at,u.username
                             FROM shared_routes r JOIN users u ON u.id=r.owner_id
                             LEFT JOIN route_viewers v ON v.token=r.token AND v.user_id=?
                             WHERE r.owner_id=? OR (v.user_id IS NOT NULL AND r.shared=1)
                             ORDER BY r.created_at DESC, r.rowid DESC''',
                          (session['user_id'], session['user_id'])).fetchall()
    return jsonify([{'token':r['token'], 'owner':r['username'], 'own':r['owner_id']==session['user_id'],
                     'shared':bool(r['shared']), 'created_at':r['created_at'],
                     'counts':[len(route['visits']) for route in json.loads(r['payload'])['routes']]}
                    for r in rows])


@app.get('/api/checked-buildings')
def checked_buildings():
    with pin_store.connect() as db:
        rows = db.execute('SELECT location_key FROM checked_buildings WHERE user_id=?', (session['user_id'],)).fetchall()
    return jsonify([row['location_key'] for row in rows])


@app.put('/api/checked-buildings')
def set_checked_building():
    data = request.get_json(silent=True) or {}
    key, checked = data.get('key'), data.get('checked')
    if not isinstance(key, str) or not re.fullmatch(r'-?\d{1,2}\.\d{7},-?\d{1,3}\.\d{7}', key) or not isinstance(checked, bool):
        return jsonify(error='建物の位置を確認してください。'), 400
    lat, lon = map(float, key.split(','))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify(error='建物の位置を確認してください。'), 400
    with pin_store.connect() as db:
        if checked:
            db.execute('INSERT OR IGNORE INTO checked_buildings(user_id,location_key) VALUES (?,?)', (session['user_id'], key))
        else:
            db.execute('DELETE FROM checked_buildings WHERE user_id=? AND location_key=?', (session['user_id'], key))
    return jsonify(checked=checked)


@app.post('/api/route-history/<token>/share')
def share_route(token):
    with pin_store.connect() as db:
        changed = db.execute('UPDATE shared_routes SET shared=1 WHERE token=? AND owner_id=?',
                             (token, session['user_id'])).rowcount
    return (jsonify(ok=True) if changed else (jsonify(error='経路が見つかりません。'), 404))


@app.delete('/api/route-history/<token>')
def delete_route_history(token):
    with pin_store.connect() as db:
        deleted = db.execute('DELETE FROM shared_routes WHERE token=? AND owner_id=?',
                             (token, session['user_id'])).rowcount
        if not deleted:
            deleted = db.execute('DELETE FROM route_viewers WHERE token=? AND user_id=?',
                                 (token, session['user_id'])).rowcount
    return (jsonify(ok=True) if deleted else (jsonify(error='経路が見つかりません。'), 404))


@app.get("/result/<int:tile>")
def result(tile):
    get_user_state(session['user_id'])
    folder = user_result_dirs.get(session['user_id'])
    image = folder / str(tile) / "comparison.png" if folder else None
    if image is None or not image.is_file():
        return "結果がありません", 404
    return send_file(image, mimetype="image/png")


def result_photo(tile):
    get_user_state(session['user_id'])
    result_folder = user_result_dirs.get(session['user_id'])
    if result_folder is None:
        return None
    folder = result_folder / str(tile)
    photos = list(folder.glob("latest_*.tif"))
    return max(photos, key=lambda path: path.stat().st_mtime) if photos else None


@app.get("/result/<int:tile>/bounds")
def result_bounds(tile):
    photo = result_photo(tile)
    if photo is None:
        return jsonify(error="比較写真が見つかりません。"), 404
    try:
        import cv2
        import numpy as np
        image = cv2.imdecode(np.fromfile(photo, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        height, width = image.shape[:2]
        a, d, b, e, c, f = map(float, photo.with_suffix('.tfw').read_text().split())
        if b != 0 or d != 0 or a <= 0 or e >= 0:
            raise ValueError('unsupported transform')
        return jsonify(south=f + e * (height - 0.5), west=c - a * 0.5,
                       north=f - e * 0.5, east=c + a * (width - 0.5))
    except (OSError, ValueError, AttributeError):
        return jsonify(error="比較写真の座標を読み取れません。"), 422


@app.get("/result/<int:tile>/photo")
def result_photo_image(tile):
    photo = result_photo(tile)
    if photo is None:
        return "比較写真が見つかりません", 404
    try:
        import cv2
        import numpy as np
        image = cv2.imdecode(np.fromfile(photo, dtype=np.uint8), cv2.IMREAD_COLOR)
        ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ValueError('encode failed')
        return Response(encoded.tobytes(), mimetype='image/jpeg')
    except (OSError, ValueError, cv2.error):
        return "比較写真を読み取れません", 422


def output_outlines():
    """地図に表示している頂点を返す。中心座標は出力に使わない。"""
    records = []
    for item in list(get_user_state(session['user_id']).get("points", [])):
        if item.get("decision") != "new":
            continue
        outline = item.get("outline") or []
        try:
            coords = [(float(lon), float(lat)) for lat, lon in outline]
            if len(coords) > 1 and coords[0] == coords[-1]:
                coords.pop()
            if len(coords) < 3 or any(not (-180 <= lon <= 180 and -90 <= lat <= 90) for lon, lat in coords):
                continue
        except (TypeError, ValueError):
            continue
        records.append((item, coords))
    return records


@app.get("/vertices.csv")
def vertices_csv():
    if get_user_state(session['user_id'])['status'] == 'idle':
        return "結果がありません", 404
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["建物番号", "頂点番号", "緯度", "経度", "外周データ", "判定", "過去の撮影日", "現在の撮影日", "区画"])
    for item, coords in output_outlines():
        for index, (lon, lat) in enumerate(coords, 1):
            writer.writerow([item.get("number", ""), index, f"{lat:.9f}", f"{lon:.9f}",
                             item.get("source", ""), item.get("decision", ""), item.get("old_date", ""),
                             item.get("new_date", ""), item.get("tile", "")])
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=building_vertices.csv"})


@app.get("/outlines.geojson")
def outlines_geojson():
    if get_user_state(session['user_id'])['status'] == 'idle':
        return "結果がありません", 404
    features = []
    for item, coords in output_outlines():
        properties = {k: item.get(k) for k in ("number", "source", "outline_type", "decision", "reason", "old_date", "new_date", "tile")}
        features.append({"type": "Feature", "properties": properties,
                         "geometry": {"type": "Polygon", "coordinates": [coords + [coords[0]]]}})
    return Response(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
                    mimetype="application/geo+json; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=building_outlines.geojson"})


def google_maps_key():
    # 専用キーは Google 側で API・サイト・日次枠を制限済み。
    key_file = configured_path("GOOGLE_MAPS_KEY_FILE", data_dir() / "google_maps_key.txt")
    candidates = [key_file]
    if os.name == "nt" and not os.environ.get("GOOGLE_MAPS_KEY_FILE"):
        # Codex などの隔離環境では LOCALAPPDATA が別の場所を指すことがある。
        user_key_file = Path.home() / "AppData" / "Local" / "AerialComparison" / "google_maps_key.txt"
        if user_key_file != key_file:
            candidates.append(user_key_file)
    key = ""
    for candidate in candidates:
        try:
            key = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if key:
            break
    if not key and os.environ.get("GOOGLE_MAPS_QUOTA_VERIFIED") == "1":
        key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    return key


@app.get("/google")
def google_map():
    key = google_maps_key()
    if not key:
        return redirect("/?google=missing-key")
    try:
        if not reserve_map_load():
            return redirect("/?google=limit")
    except Exception:
        return redirect("/?google=unavailable")
    response = app.make_response(render_template("google_map.html", google_key=key))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    return render_template("index.html", google_available=bool(google_maps_key()))

def start_ngrok(port):
    """同じポートが公開済みなら再利用し、なければ ngrok を起動する。"""
    def public_url():
        for api_port in range(4040, 4045):
            try:
                with urlopen(f'http://127.0.0.1:{api_port}/api/tunnels', timeout=1) as response:
                    tunnels = json.load(response).get('tunnels', [])
                for tunnel in tunnels:
                    if tunnel.get('config', {}).get('addr', '').rstrip('/').endswith(f':{port}'):
                        return tunnel.get('public_url')
            except (OSError, ValueError):
                continue
        return None
    url = public_url()
    if url:
        print('公開URL:', url, flush=True)
        return
    bundled_ngrok = base / 'tools' / ('ngrok.exe' if os.name == 'nt' else 'ngrok')
    executable = os.environ.get('AKAIITO_NGROK') or (str(bundled_ngrok) if bundled_ngrok.is_file() else shutil.which('ngrok'))
    if not executable and os.environ.get('LOCALAPPDATA'):
        executable = str(Path(os.environ['LOCALAPPDATA']) / 'ngrok' / 'ngrok.exe')
    if not executable or not Path(executable).is_file():
        print('ngrok が見つかりません。ローカル画面は利用できます。', flush=True)
        return
    try:
        command = [executable, 'http', str(port)]
        config = os.environ.get('AKAIITO_NGROK_CONFIG')
        if not config and os.name == 'nt':
            default_config = Path.home() / 'AppData' / 'Local' / 'ngrok' / 'ngrok.yml'
            if default_config.is_file():
                config = str(default_config)
        if config:
            command.extend(['--config', config])
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(10):
            time.sleep(1)
            url = public_url()
            if url:
                print('公開URL:', url, flush=True)
                return
            if process.poll() is not None:
                break
        print('ngrok の公開URLを確認できません。認証設定や同時起動数を確認してください。', flush=True)
    except OSError as error:
        print(f'ngrok を起動できません: {error}', flush=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    restore = os.environ.get("AERIAL_RESTORE_SESSION")
    if restore:
        try:
            folder = configured_path("AERIAL_RESTORE_SESSION", restore)
            state.update(json.loads((folder / 'state.json').read_text(encoding='utf-8')))
            result_dir = folder
        except (OSError, ValueError):
            print('保存された比較結果を読み込めませんでした。', flush=True)
    threading.Thread(target=start_ngrok, args=(port,), daemon=True).start()
    app.run(host="0.0.0.0", port=port, threaded=True)
