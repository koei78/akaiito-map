import csv
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

base = Path(__file__).resolve().parent
images = Path(os.environ.get("PHOTO_IMAGES_DIR", base / "google_earth_history" / "images"))
output_dir = Path(os.environ.get("PHOTO_OUTPUT_DIR", base))
old = max(images.glob("15years_ago_*.tif"), key=lambda p: p.stat().st_mtime, default=None)
new = max(images.glob("latest_*.tif"), key=lambda p: p.stat().st_mtime, default=None)
if old is None or new is None:
    raise SystemExit(f"比較する写真がありません。先に python get.py を実行してください。保存先: {images}")

codex = shutil.which("codex")
if not codex:
    codex_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    candidates = list(codex_dir.glob("*/codex.exe"))
    codex = str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None
if not codex:
    raise SystemExit("Codex CLI が見つかりません。Codex CLI をインストールしてください。")

def read(path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"画像を読み込めません: {path}")
    return image

old_image, new_image = read(old), read(new)

def geo_matrix(path):
    a, d, b, e, c, f = map(float, path.with_suffix(".tfw").read_text().split())
    return np.array([[a, b, c], [d, e, f], [0, 0, 1]], dtype=np.float64)

if not old.with_suffix(".tfw").exists() or not new.with_suffix(".tfw").exists():
    raise SystemExit("画像の位置合わせに必要な座標情報がありません。")
# 過去画像を現在画像の座標系へ変換して、同じ範囲を比較する。
warp = np.linalg.inv(geo_matrix(new)) @ geo_matrix(old)
old_coverage = cv2.warpAffine(np.full(old_image.shape[:2], 255, np.uint8), warp[:2],
                              (new_image.shape[1], new_image.shape[0]), flags=cv2.INTER_NEAREST)
old_image = cv2.warpAffine(old_image, warp[:2], (new_image.shape[1], new_image.shape[0]))
with tempfile.TemporaryDirectory() as folder:
    folder = Path(folder)
    old_png, new_png = folder / "old.png", folder / "new.png"
    cv2.imencode(".png", old_image)[1].tofile(old_png)
    cv2.imencode(".png", new_image)[1].tofile(new_png)
    schema = {"type": "object", "properties": {"buildings": {"type": "array", "items": {
        "type": "array", "minItems": 3, "items": {"type": "array", "minItems": 2, "maxItems": 2,
        "items": {"type": "integer", "minimum": 0, "maximum": 1000}}}}},
        "required": ["buildings"], "additionalProperties": False}
    schema_file, answer = folder / "schema.json", folder / "answer.json"
    schema_file.write_text(json.dumps(schema), encoding="utf-8")
    prompt = ("添付画像だけを使って判断し、コマンドやツールは使わず、指定のJSONを直接返してください。"
              "1枚目は過去、2枚目は現在の同じ地域の航空写真です。"
              "現在の画像で新しく建てられた可能性が高い建物を調べ、各建物の屋根の外周に沿う多角形を返してください。"
              "道路、車、樹木、影、既存の建物は除外し、判断が難しい場合は含めないでください。"
              "buildingsは多角形の配列で、各多角形は周囲を順番にたどる3点以上の[x,y]の配列です。"
              "座標は2枚目の画像の左上を[0,0]、右下を[1000,1000]とする整数です。"
              "建物を矩形で大まかに囲まず、屋根の見える輪郭にできるだけ沿ってください。"
              "該当なしならbuildingsを空配列にしてください。")
    effort = os.environ.get("PHOTO_REASONING_EFFORT", "medium")
    if effort not in ("medium", "high"):
        raise ValueError("AI設定が正しくありません")
    subprocess.run([codex, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                    "--model", "gpt-6-astra", "-c", f'model_reasoning_effort="{effort}"',
                    "--output-schema", str(schema_file), "-o", str(answer),
                    "-i", str(old_png), "-i", str(new_png)], input=prompt, encoding="utf-8", check=True, cwd=folder, timeout=180)
    buildings = json.loads(answer.read_text(encoding="utf-8"))["buildings"]


world_file = new.with_suffix(".tfw")
if not world_file.exists():
    raise SystemExit(f"座標情報がありません: {world_file}。python get.py で写真を再取得してください。")
a, d, b, e, c, f = map(float, world_file.read_text(encoding="utf-8").split())
height, width = new_image.shape[:2]
coordinates = []
for polygon in buildings:
    decision, reason = "new", "航空写真の比較による新築候補"
    if len(polygon) < 3 or any(len(point) != 2 or not all(0 <= value <= 1000 for value in point) for point in polygon):
        continue
    pixels = np.array([[round(x * (width - 1) / 1000), round(y * (height - 1) / 1000)] for x, y in polygon], dtype=np.int32)
    if cv2.contourArea(pixels) < 1:
        continue
    mask = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask, [pixels], 255)
    area = cv2.countNonZero(mask)
    if not area or cv2.countNonZero(cv2.bitwise_and(mask, old_coverage)) / area < 0.95:
        continue
    moments = cv2.moments(pixels)
    x, y = moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]
    longitude, latitude = a * x + b * y + c, d * x + e * y + f
    outline = [[d * px + e * py + f, a * px + b * py + c] for px, py in pixels]
    coordinates.append((len(coordinates) + 1, latitude, longitude, outline, decision, reason))
    color = (0, 0, 255)
    cv2.polylines(new_image, [pixels], True, color, 3)
    cv2.putText(new_image, str(len(coordinates)), tuple(pixels[0]),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
output = output_dir / "comparison.png"
cv2.imencode(".png", new_image)[1].tofile(output)
with (output_dir / "coordinates.csv").open("w", encoding="utf-8-sig", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["番号", "緯度", "経度", "外周", "判定", "理由"])
    for number, latitude, longitude, outline, decision, reason in coordinates:
        writer.writerow([number, f"{latitude:.7f}", f"{longitude:.7f}", json.dumps(outline), decision, reason])
        print(f"{number}: 緯度 {latitude:.7f}, 経度 {longitude:.7f} 判定: {decision} ({reason})")
# 頂点ごとに1行のCSVと、地図で開けるGeoJSONを保存する。
features = []
with (output_dir / "building_vertices.csv").open("w", encoding="utf-8-sig", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["建物番号", "頂点番号", "緯度", "経度", "判定", "外周の出典"])
    for number, latitude, longitude, outline, decision, reason in coordinates:
        for index, (lat, lon) in enumerate(outline, 1):
            writer.writerow([number, index, f"{lat:.9f}", f"{lon:.9f}", decision, "AIの推定輪郭"])
        ring = [[lon, lat] for lat, lon in outline]
        if ring[-1] != ring[0]:
            ring.append(ring[0])
        features.append({"type": "Feature", "properties": {"number": number, "decision": decision,
                        "reason": reason, "source": "AIの推定輪郭"},
                        "geometry": {"type": "Polygon", "coordinates": [ring]}})
(output_dir / "building_outlines.geojson").write_text(
    json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")
print(f"保存しました: {output}、{output_dir / 'building_vertices.csv'}、{output_dir / 'building_outlines.geojson'}（候補: {len(coordinates)}件）")
