import csv
import json
import os
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from image_quality import final_decision, inspect_candidate, nearly_identical, refine_translation
from paths import codex_executable, configured_path

base = Path(__file__).resolve().parent
images = configured_path("PHOTO_IMAGES_DIR", base / "google_earth_history" / "images")
output_dir = configured_path("PHOTO_OUTPUT_DIR", base)
old = max(images.glob("15years_ago_*.tif"), key=lambda p: p.stat().st_mtime, default=None)
new = max(images.glob("latest_*.tif"), key=lambda p: p.stat().st_mtime, default=None)
if old is None or new is None:
    raise SystemExit(f"比較する写真がありません。先に python get.py を実行してください。保存先: {images}")

codex = codex_executable()
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
old_image, old_coverage, residual_shift = refine_translation(old_image, new_image, old_coverage)
print(f"位置合わせの補正: x={residual_shift[0]:.1f}px, y={residual_shift[1]:.1f}px")
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
              "過去には建物の屋根がなく、現在は完成した屋根がある場所だけを候補にしてください。"
              "屋根の色替え、改修、建物の影や撮影角度の違いを新築と扱わないでください。"
              "道路、車、樹木、季節差、既存の建物は除外してください。"
              "古い画像が不鮮明・遮蔽されていて判断できない場所も含めないでください。"
              "buildingsは多角形の配列で、各多角形は周囲を順番にたどる3点以上の[x,y]の配列です。"
              "座標は2枚目の画像の左上を[0,0]、右下を[1000,1000]とする整数です。"
              "建物を矩形で大まかに囲まず、屋根の見える輪郭にできるだけ沿ってください。"
              "該当なしならbuildingsを空配列にしてください。")
    effort = os.environ.get("PHOTO_REASONING_EFFORT", "medium")
    if effort not in ("medium", "high"):
        raise ValueError("AI設定が正しくありません")
    def ask_model(instruction, schema_path, output_path, first_image, second_image):
        subprocess.run([codex, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                        "--model", "gpt-6-astra", "-c", f'model_reasoning_effort="{effort}"',
                        "--output-schema", str(schema_path), "-o", str(output_path),
                        "-i", str(first_image), "-i", str(second_image)],
                       input=instruction, encoding="utf-8", check=True, cwd=folder, timeout=180)
        return json.loads(output_path.read_text(encoding="utf-8"))

    buildings = [] if nearly_identical(old_image, new_image, old_coverage) else ask_model(
        prompt, schema_file, answer, old_png, new_png)["buildings"]
    preliminary = []
    for polygon in buildings:
        pixels, quality, quality_reason = inspect_candidate(polygon, old_image, new_image, old_coverage)
        if quality != "reject":
            preliminary.append((pixels, quality, quality_reason))

    assessments = {}
    if preliminary:
        marked_old, marked_new = old_image.copy(), new_image.copy()
        for number, (pixels, _, _) in enumerate(preliminary, 1):
            x, y, _, _ = cv2.boundingRect(pixels)
            label_pos = (min(x + 4, new_image.shape[1] - 50), max(y - 6, 22))
            for image, color in ((marked_old, (255, 180, 0)), (marked_new, (0, 0, 255))):
                cv2.polylines(image, [pixels], True, color, 3)
                cv2.putText(image, f"#{number}", label_pos, cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 3)
        marked_old_png, marked_new_png = folder / "old-marked.png", folder / "new-marked.png"
        cv2.imencode(".png", marked_old)[1].tofile(marked_old_png)
        cv2.imencode(".png", marked_new)[1].tofile(marked_new_png)
        review_schema = {"type": "object", "properties": {"assessments": {"type": "array", "items": {
            "type": "object", "properties": {"id": {"type": "integer"},
            "decision": {"type": "string", "enum": ["new", "review", "reject"]},
            "reason": {"type": "string"}},
            "required": ["id", "decision", "reason"], "additionalProperties": False}}},
            "required": ["assessments"], "additionalProperties": False}
        review_schema_file, review_answer = folder / "review-schema.json", folder / "review-answer.json"
        review_schema_file.write_text(json.dumps(review_schema), encoding="utf-8")
        review_prompt = ("2枚の航空写真を独立に再確認してください。1枚目は過去、2枚目は現在です。"
                         "両画像の同じ番号の外周が同じ場所を示します。"
                         "各番号について、過去は建物がなく現在は新しい完成した屋根が見える場合だけ decision=new としてください。"
                         "過去にも建物がある、改修・建て替えか不明、撮影角度・季節・影・画質差だけの可能性がある場合は review または reject としてください。"
                         "判定根拠を短く具体的に書いてください。対象番号は1から" + str(len(preliminary)) + "までです。"
                         "指定のJSONだけを返し、コマンドやツールは使わないでください。")
        try:
            answers = ask_model(review_prompt, review_schema_file, review_answer, marked_old_png, marked_new_png)["assessments"]
            assessments = {item["id"]: item for item in answers if isinstance(item, dict)
                           and isinstance(item.get("id"), int) and item.get("decision") in ("new", "review", "reject")}
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
            print(f"再確認が完了しなかったため候補を要確認にします: {error}")


world_file = new.with_suffix(".tfw")
if not world_file.exists():
    raise SystemExit(f"座標情報がありません: {world_file}。python get.py で写真を再取得してください。")
a, d, b, e, c, f = map(float, world_file.read_text(encoding="utf-8").split())
coordinates = []
for candidate_id, (pixels, quality, quality_reason) in enumerate(preliminary, 1):
    decision, reason = final_decision(quality, quality_reason, assessments.get(candidate_id))
    if decision == "reject":
        continue
    moments = cv2.moments(pixels)
    x, y = moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]
    longitude, latitude = a * x + b * y + c, d * x + e * y + f
    outline = [[d * px + e * py + f, a * px + b * py + c] for px, py in pixels]
    coordinates.append((len(coordinates) + 1, latitude, longitude, outline, decision, reason))
    color = (0, 0, 255) if decision == "new" else (0, 165, 255)
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
print(f"保存しました: {output}、{output_dir / 'building_vertices.csv'}、{output_dir / 'building_outlines.geojson'}（新築候補: {sum(row[4] == 'new' for row in coordinates)}件、要確認: {sum(row[4] == 'review' for row in coordinates)}件）")
