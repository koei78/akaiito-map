import csv
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

from flask import Flask, Response, jsonify, request, send_file, render_template_string, redirect
from shapely.geometry import Polygon, Point, box
from google_usage import reserve_map_load

app = Flask(__name__)
base = Path(__file__).resolve().parent
exe = base / "GEHistoricalImagery" / "gdal" / "GEHistoricalImagery.exe"
state = {"status": "idle", "message": "地図上で範囲を選択してください。", "points": []}
lock = threading.Lock()
result_dir = None


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
    for point in points:
        try:
            shape = Polygon([(lon, lat) for lat, lon in point["outline"]]).buffer(0)
            if shape.is_empty or shape.area <= 0:
                continue
            duplicate = False
            for existing, other in unique:
                if not shape.intersects(other):
                    continue
                if shape.intersection(other).area / min(shape.area, other.area) >= 0.6:
                    duplicate = True
                    break
            if not duplicate:
                unique.append((point, shape))
        except (KeyError, TypeError, ValueError):
            continue
    return [point for point, _ in unique]


def run_comparison(polygon, cells, years, ai_effort="medium"):
    global result_dir
    folder = Path(tempfile.mkdtemp(prefix="aerial_"))
    result_dir = folder
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
            env.update(PHOTO_IMAGES_DIR=str(part), PHOTO_OUTPUT_DIR=str(part), PHOTO_REASONING_EFFORT=ai_effort)
            run = subprocess.run([sys.executable, str(base / "compare_precise.py")], cwd=base, env=env,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            if run.returncode:
                raise RuntimeError("比較失敗: " + run.stderr[-500:])
            with (part / "coordinates.csv").open(encoding="utf-8-sig", newline="") as file:
                points = [{"lat": float(row["緯度"]), "lon": float(row["経度"]), "tile": index,
                           "old_date": old.name[12:22], "new_date": new.name[7:17], "outline": json.loads(row["外周"]),
                           "decision": row["判定"], "reason": row["理由"]}
                          for row in csv.DictReader(file)]
            points = [point for point in points if polygon.covers(Point(point["lon"], point["lat"]))]
            if points:
                helper = base / ".map_data_venv" / "Scripts" / "python.exe"
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
        # 画像取得と AI の待ち時間を、最大 6 区画で重ねる。
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(process, (index, bounds)): (index, bounds, 0)
                       for index, bounds in enumerate(cells)}
            while futures:
                future = next(as_completed(futures))
                index, bounds, depth = futures.pop(future)
                try:
                    points = deduplicate(points + future.result())
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
                state.update(points=points.copy(), message=f"処理済み {completed}区画、残り {len(futures)}区画（取得不可 {len(failures)}）")
        state.update(status="done" if not failures else "partial", points=points,
                     message=f"完了 {completed}区画、候補 {len(points)}件。" +
                             (f" 取得・処理できなかった範囲: {len(failures)}区画。" + " / ".join(failures[:5]) if failures else ""))
    except Exception as error:
        state.update(status="error", message=str(error), points=[])


@app.post("/compare")
def compare():
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
        rows, cols = math.ceil((north-south)/dy), math.ceil((east-west)/dx)
        if rows * cols > 400:
            return jsonify(error="範囲が広すぎます。分割数が400以下になるようにしてください。"), 400
        cells = []
        for row in range(rows):
            for col in range(cols):
                s, w = south + row * dy, west + col * dx
                n, e = s + dy, w + dx
                if polygon.intersects(box(w, s, e, n)):
                    cells.append((s, w, n, e))
    except (TypeError, ValueError, KeyError, AttributeError):
        return jsonify(error="交差しない線で範囲を囲んでください。"), 400
    with lock:
        if state["status"] == "running":
            return jsonify(error="比較を実行中です。"), 409
        state.update(status="running", message=f"{len(cells)}区画を並行処理します。", points=[])
        threading.Thread(target=run_comparison, args=(polygon, cells, years, ai_effort), daemon=True).start()
    return jsonify(state)


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
        helper = base / ".map_data_venv" / "Scripts" / "python.exe"
        result = subprocess.run([str(helper), "-X", "utf8", str(base / "gsi_area.py")],
                                input=json.dumps(vertices), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=90, check=True)
        return jsonify(json.loads(result.stdout))
    except (TypeError, KeyError, ValueError, AttributeError) as error:
        return jsonify(error=str(error) or "範囲が正しくありません。"), 400
    except (OSError, subprocess.SubprocessError):
        return jsonify(error="建物の外周データを取得できませんでした。"), 503


@app.post("/clear")
def clear_results():
    global result_dir
    with lock:
        if state["status"] == "running":
            return jsonify(error="比較中は結果を消せません。完了までお待ちください。"), 409
        state.update(status="idle", message="結果を消しました。地図上で範囲を選択してください。", points=[])
        result_dir = None
    return jsonify(state)


@app.get("/status")
def status():
    return jsonify(state)


@app.get("/result/<int:tile>")
def result(tile):
    image = result_dir / str(tile) / "comparison.png" if result_dir else None
    if image is None or not image.is_file():
        return "結果がありません", 404
    return send_file(image, mimetype="image/png")


def output_outlines():
    """地図に表示している頂点を返す。中心座標は出力に使わない。"""
    records = []
    for item in list(state.get("points", [])):
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
    features = []
    for item, coords in output_outlines():
        properties = {k: item.get(k) for k in ("number", "source", "outline_type", "decision", "reason", "old_date", "new_date", "tile")}
        features.append({"type": "Feature", "properties": properties,
                         "geometry": {"type": "Polygon", "coordinates": [coords + [coords[0]]]}})
    return Response(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
                    mimetype="application/geo+json; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=building_outlines.geojson"})


@app.get("/google")
def google_map():
    # 専用キーは Google 側で API・サイト・日次枠を制限済み。
    key_file = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AerialComparison" / "google_maps_key.txt"
    try:
        key = key_file.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if not key and os.environ.get("GOOGLE_MAPS_QUOTA_VERIFIED") == "1":
        key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return redirect("/?google=unavailable")
    try:
        if not reserve_map_load():
            return redirect("/?google=limit")
    except Exception:
        return redirect("/?google=unavailable")
    template = (base / "google_map.html").read_text(encoding="utf-8-sig")
    response = app.make_response(render_template_string(template, google_key=key))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    return render_template_string('''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>航空写真の比較</title><link rel="icon" type="image/png" href="/static/new-building-icon.png">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css">
<style>
*{box-sizing:border-box}body{margin:0;background:#17211f;color:#f1f5f3;font-family:"Segoe UI",system-ui,sans-serif}#map{position:fixed;inset:0;background:#1b2827}
.panel{position:fixed;z-index:500;top:14px;left:14px;right:14px;display:flex;align-items:flex-start;gap:16px;padding:12px 14px;border:1px solid #ffffff1e;border-radius:14px;background:rgba(32,37,38,.96);box-shadow:0 8px 28px #0006}
.brand{display:flex;align-items:center;gap:10px;flex:none;min-height:40px;padding-right:16px;border-right:1px solid #ffffff20}.brand-icon{display:grid;place-items:center;width:38px;height:38px;border-radius:50%;background:linear-gradient(145deg,#85cfc0,#398eab);color:white}.brand-icon svg{width:22px;height:22px}.brand small{display:block;color:#a0b1ad;font-size:9px;font-weight:700;letter-spacing:.12em}.brand h1{font-size:15px;margin:3px 0 0;white-space:nowrap}
.content{display:flex;flex:1 1 auto;min-width:0;align-items:center;gap:9px 12px;flex-wrap:wrap}.eyebrow,.content h2,.description,.steps{display:none}.layer-select{flex:0 0 160px;width:160px;min-height:40px;padding:8px;border:1px solid #ffffff20;border-radius:8px;color:#f0f6f3;background:#394541;font:12px system-ui,sans-serif}.year-control{display:flex;flex:1 1 210px;align-items:center;gap:8px;min-width:180px;max-width:300px;color:#dce9e4;font-size:12px;white-space:nowrap}.year-control input{flex:1;min-width:70px;accent-color:#8bd3c0}.year-control output{min-width:44px;font-weight:700}.actions{display:flex;gap:8px;flex:none}button{min-height:40px;padding:9px 14px;border:1px solid #ffffff20;border-radius:8px;background:#394541;color:#eef5f2;font:600 12px system-ui,sans-serif;white-space:nowrap;cursor:pointer}button:hover:not(:disabled){background:#4a5d56}button:focus-visible{outline:3px solid white;outline-offset:2px}button:disabled{opacity:.42;cursor:not-allowed}#start{background:#8bd3c0;border-color:#8bd3c0;color:#122a24}#start:hover:not(:disabled){background:#a5e5d4}.google-link{flex:none;white-space:nowrap;color:#a6ddce;font-size:12px;text-decoration:none}.google-link:hover{text-decoration:underline}
.status{order:5;flex:1 1 300px;min-width:180px;max-height:50px;overflow:auto;padding:8px 10px;border-radius:8px;background:#151d1c;color:#c1d3cc;font-size:12px;line-height:1.4;overflow-wrap:anywhere}.status:before{content:"";display:inline-block;width:7px;height:7px;margin-right:7px;border-radius:50%;background:#8bd3c0}.legend{order:6;display:flex;align-items:center;gap:7px;flex:0 1 auto;color:#a9bbb5;font-size:11px}.legend i{width:12px;height:12px;border:2px solid #ff514b;border-radius:2px}#downloads{order:7;white-space:nowrap;font-size:12px}#downloads a{color:#a6ddce;margin-left:8px}.dates{position:fixed;z-index:500;left:14px;bottom:28px;max-width:min(420px,calc(100vw - 90px));padding:9px 11px;border-radius:9px;background:#192321e8;color:#d3e2dc;font-size:11px;line-height:1.5;box-shadow:0 4px 15px #0006}.dates strong{color:white}
.leaflet-control-zoom{border:0!important;box-shadow:0 4px 14px #0006!important;margin:0 18px 24px 0!important}.leaflet-control-zoom a{background:#26312f!important;color:white!important;border-color:#485450!important}.leaflet-control-attribution{background:#202928dd!important;color:#d0dfd9!important}.leaflet-control-attribution a{color:#a4ddcc!important}.leaflet-popup-content-wrapper,.leaflet-popup-tip{background:#26312f;color:#ecf3ef}.leaflet-popup-content a{color:#9be4cf}
@media(max-width:1050px){.panel{gap:10px}.brand{padding-right:10px}.content{gap:8px}.legend{display:none}.status{flex-basis:100%}}
@media(max-width:700px){.panel{top:8px;left:8px;right:8px;gap:9px;flex-wrap:wrap;padding:10px}.brand{width:100%;border:0;padding:0;min-height:32px}.brand-icon{width:32px;height:32px}.brand-icon svg{width:19px;height:19px}.brand h1{font-size:14px}.brand small{font-size:8px}.content{width:100%;flex:1 1 100%}.layer-select{flex:1 1 145px;width:auto;min-width:0}.year-control{flex:1 1 190px;max-width:none}.actions{flex:1 1 100%}.actions button{flex:1;min-height:42px}.google-link{margin-left:auto}.status{min-width:0;max-height:52px}.dates{left:8px;bottom:26px;max-width:calc(100vw - 85px);font-size:10px}.leaflet-control-zoom{margin-right:10px!important}}
@media(max-width:370px){.year-control{flex-basis:100%}.layer-select{flex-basis:60%}.panel{gap:6px}.content{gap:6px}}
</style></head><body><div id="map"></div>
<div class="dates" id="dates" role="status"><div><strong>建物外周：</strong><span id="building-date">候補の照合に使用・個別の更新日不明</span></div><div><strong>表示中の航空写真：</strong><span id="photo-date">Esri（撮影日は特定できません）</span></div></div><aside class="panel"><div class="brand"><div class="brand-icon"><img src="/static/new-building-icon.png" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:50%"></div><div><small>IMAGERY EXPLORER</small><h1>航空写真の比較</h1></div></div>
<div class="content"><div class="eyebrow">CHANGE DETECTION</div><h2>街の変化を見つける</h2><p class="description">撮影日は区画の中心で見つかった撮影日のうち、範囲全体を取得できた日を使用します。</p>
<div class="steps"><div class="step"><span>1</span>地図を移動して場所を選ぶ</div><div class="step"><span>2</span>自由に範囲を囲む</div><div class="step"><span>3</span>比較を開始する</div></div>
<label for="imagery" class="eyebrow">航空写真</label><select id="imagery" class="layer-select" aria-label="航空写真の種類"><option value="esri">Esriの航空写真</option><option value="gsi">国土地理院の航空写真</option><option value="google">Google航空写真</option><option value="google-map">Googleマップ</option></select><label class="year-control" for="years">比較時期 <input id="years" type="range" min="1" max="40" value="15"><output id="years-value">15年前</output></label><label>AI設定 <select id="ai-level"><option value="medium">使用量を抑える</option><option value="high">高精度（使用量多め）</option></select></label><div class="actions"><button id="draw">✎　自由に囲む</button><button id="start" disabled>比較する　→</button><button id="clear" title="比較結果と選択範囲を消す">結果を消す</button></div><div class="status" id="status" role="status">地図上で範囲を選択してください。</div><div class="legend"><i></i>赤い外周は新築の可能性が高い建物です。</div><span id="downloads" hidden><a href="/vertices.csv">頂点CSV</a> <a href="/outlines.geojson">外周GeoJSON</a></span></div></aside>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
<script>
const googleState = new URLSearchParams(location.search).get('google');
if (googleState) document.getElementById('status').textContent = googleState === 'limit' ? 'Google航空写真は今月のアプリ上限に達したため無料の航空写真を表示しています。' : 'Google側の安全設定を確認できないため無料の航空写真を表示しています。';
let savedView;try{savedView=JSON.parse(sessionStorage.getItem('aerial_view'));}catch(e){}
const validView=savedView&&Number.isFinite(savedView.lat)&&Number.isFinite(savedView.lng)&&Number.isFinite(savedView.zoom);
const map=L.map('map',{zoomControl:false}).setView(validView?[savedView.lat,savedView.lng]:[35.506600,137.816433333],validView?savedView.zoom:18);
map.on('moveend',()=>{const c=map.getCenter();sessionStorage.setItem('aerial_view',JSON.stringify({lat:c.lat,lng:c.lng,zoom:map.getZoom()}));});
L.control.zoom({position:'bottomright'}).addTo(map);
const imageryLayers={
 esri:L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:22,maxNativeZoom:18,attribution:'Tiles &copy; Esri — Esri, Maxar, Earthstar Geographics and the GIS User Community | 建物：国土地理院 地理院地図Vector'}),
 gsi:L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg',{minNativeZoom:14,maxNativeZoom:18,maxZoom:22,attribution:'航空写真：<a href="https://maps.gsi.go.jp/development/ichiran.html">国土地理院</a> | 建物：国土地理院 地理院地図Vector'})
};
let selectedLayer=imageryLayers.esri.addTo(map);
document.getElementById('imagery').onchange=e=>{if(e.target.value==='google'||e.target.value==='google-map'){const c=map.getCenter();sessionStorage.setItem('aerial_view',JSON.stringify({lat:c.lat,lng:c.lng,zoom:map.getZoom()}));location.href='/google?map='+ (e.target.value==='google-map'?'roadmap':'satellite');return;}map.removeLayer(selectedLayer);selectedLayer=imageryLayers[e.target.value].addTo(map);document.getElementById('photo-date').textContent=(e.target.value==='gsi'?'国土地理院':'Esri')+'（撮影日は場所により異なり特定できません）';};
if(new URLSearchParams(location.search).get('layer')==='gsi'){document.getElementById('imagery').value='gsi';document.getElementById('imagery').dispatchEvent(new Event('change'));}
const shapes=new L.FeatureGroup().addTo(map), pins=new L.FeatureGroup().addTo(map);
const button=document.getElementById('start'), message=document.getElementById('status'), draw=document.getElementById('draw'), clear=document.getElementById('clear');
const yearSlider=document.getElementById('years'), yearValue=document.getElementById('years-value');
yearSlider.oninput=()=>yearValue.textContent=yearSlider.value+'年前';
const aiLevel=document.getElementById('ai-level');aiLevel.value=sessionStorage.getItem('aerial_ai_effort')==='high'?'high':'medium';aiLevel.onchange=()=>sessionStorage.setItem('aerial_ai_effort',aiLevel.value);
let polygon=null, drawing=false, active=false, path=[], shape=null, comparisonActive=false;
clear.onclick=async()=>{try{const response=await fetch('/clear',{method:'POST'});const data=await response.json();if(!response.ok)throw Error(data.error);shapes.clearLayers();pins.clearLayers();polygon=null;drawing=false;active=false;comparisonActive=false;map.dragging.enable();element.style.touchAction='';button.disabled=true;document.getElementById('downloads').hidden=true;message.textContent=data.message;}catch(e){message.textContent=e.message;}};
draw.onclick=()=>{drawing=true;polygon=null;button.disabled=true;shapes.clearLayers();pins.clearLayers();document.getElementById('downloads').hidden=true;map.dragging.disable();element.style.touchAction='none';message.textContent='地図上をドラッグして囲んでください。';};
const element=map.getContainer();
element.addEventListener('pointerdown',e=>{if(!drawing || e.button!==0)return; e.preventDefault();e.stopPropagation();active=true;path=[];element.setPointerCapture(e.pointerId);shape=L.polygon([], {color:'red'}).addTo(shapes);},true);
element.addEventListener('pointermove',e=>{if(!active)return;const p=map.mouseEventToLatLng(e);path.push([p.lat,p.lng]);shape.setLatLngs(path);e.preventDefault();},true);
element.addEventListener('pointerup',e=>{if(!active)return;active=false;drawing=false;map.dragging.enable();element.style.touchAction='';if(path.length>=3){polygon=path;button.disabled=false;message.textContent='範囲を指定しました。比較するを押してください。';}else{shapes.clearLayers();message.textContent='範囲を囲んでください。';}e.preventDefault();},true);
button.onclick=async()=>{button.disabled=true; pins.clearLayers(); try {const r=await fetch('/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({polygon, years:Number(yearSlider.value), ai_effort:aiLevel.value})}); const data=await r.json(); if(!r.ok) throw Error(data.error); comparisonActive=true; poll();} catch(e){message.textContent=e.message;button.disabled=false;}};
function escapeText(value){const el=document.createElement('span');el.textContent=String(value || '');return el.innerHTML;}
async function poll(){try{
 const data=await (await fetch('/status')).json();message.textContent=data.message;
 if(data.status==='running'){draw.disabled=true;clear.disabled=true;button.disabled=true;setTimeout(poll,1500);return;}
 draw.disabled=false;clear.disabled=false;
 if(comparisonActive && data.status==='done'){shapes.clearLayers();comparisonActive=false;}
 if(data.status==='partial'){comparisonActive=false;}
 pins.clearLayers();
 const visible=data.points.filter(p=>p.decision==='new' && p.outline && p.outline.length>=3);
 if((data.status==='done'||data.status==='partial') && data.points.length)message.textContent += `（赤い外周 ${visible.length}件）`;
 document.getElementById('downloads').hidden=visible.length===0;
 visible.forEach(p=>{const layer=L.polygon(p.outline,{color:'red',weight:3,fillOpacity:0.12});layer.bindPopup(`新築候補 ${p.number}<br>外周の頂点：${p.outline.length}点<br>比較写真：${p.old_date} → ${p.new_date}<br>${escapeText(p.reason)}<br>${escapeText(p.source)}<br><a target="_blank" href="/result/${p.tile}">AIによる比較画像</a><br><a href="/vertices.csv">頂点座標をCSVで保存</a>`).addTo(pins);});
 button.disabled=!polygon;
 }catch(e){message.textContent='接続できません';button.disabled=!polygon;}}

poll();
</script></body></html>''')

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
    executable = shutil.which('ngrok') or str(Path(os.environ.get('LOCALAPPDATA', '')) / 'ngrok' / 'ngrok.exe')
    if not Path(executable).is_file():
        print('ngrok が見つかりません。ローカル画面は利用できます。', flush=True)
        return
    try:
        process = subprocess.Popen([executable, 'http', str(port)], stdout=subprocess.DEVNULL,
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
    port = int(os.environ.get("PORT", "5000"))
    restore = os.environ.get("AERIAL_RESTORE_SESSION")
    if restore:
        try:
            folder = Path(restore)
            state.update(json.loads((folder / 'state.json').read_text(encoding='utf-8')))
            result_dir = folder
        except (OSError, ValueError):
            print('保存された比較結果を読み込めませんでした。', flush=True)
    threading.Thread(target=start_ngrok, args=(port,), daemon=True).start()
    app.run(host="0.0.0.0", port=port, threaded=True)
