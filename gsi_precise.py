import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import math
import sys
import urllib.error
import urllib.request

import mapbox_vector_tile
from paths import data_dir
from shapely.geometry import Point, Polygon, shape
from shapely.ops import transform
from outline_matching import footprint_agreement

ZOOM = 16
N = 2 ** ZOOM
URL = 'https://cyberjapandata.gsi.go.jp/xyz/experimental_bvmap/{}/{}/{}.pbf'


def tile_of(lat, lon):
    return (int((lon + 180) / 360 * N),
            int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * N))


def to_latlon(x, y, tx, ty, extent):
    lon = (tx + x / extent) / N * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + (extent-y)/extent) / N))))
    return lon, lat


points = json.load(sys.stdin)
tiles = set()
for p in points:
    outline = p.get('outline') or []
    try:
        coords = [(float(lat), float(lon)) for lat, lon in outline]
        if len(coords) < 3 or any(not (-85 <= lat <= 85 and -180 <= lon <= 180) for lat, lon in coords):
            continue
        xs, ys = zip(*(tile_of(lat, lon) for lat, lon in coords))
        if (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1) > 100:
            continue
        tiles.update((x, y) for x in range(min(xs), max(xs) + 1) for y in range(min(ys), max(ys) + 1))
    except (TypeError, ValueError):
        continue

cache = data_dir() / 'gsi_tiles'
cache.mkdir(parents=True, exist_ok=True)

def get_tile(tile_id):
    x, y = tile_id
    path = cache / f'{ZOOM}_{x}_{y}.pbf'
    try:
        if path.exists() and time.time() - path.stat().st_mtime < 3600:
            cached = path.read_bytes()
            try:
                mapbox_vector_tile.decode(cached)
                return x, y, cached
            except Exception:
                path.unlink(missing_ok=True)
        with urllib.request.urlopen(URL.format(ZOOM, x, y), timeout=20) as response:
            data = response.read()
        mapbox_vector_tile.decode(data)
        # 書き込み途中のタイルを別プロセスが読まないようにする。
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write(data)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return x, y, data
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return x, y, None
        raise

buildings = []
with ThreadPoolExecutor(max_workers=6) as pool:
    for x, y, data in pool.map(get_tile, tiles):
        if data is None:
            continue
        tile = mapbox_vector_tile.decode(data)
        layer = tile.get('building', {})
        extent = layer.get('extent', 4096)
        for feature in layer.get('features', []):
            if feature['geometry']['type'] not in ('Polygon', 'MultiPolygon'):
                continue
            geometry = transform(lambda px, py, z=None: to_latlon(px, py, x, y, extent), shape(feature['geometry']))
            if geometry.is_valid and not geometry.is_empty:
                buildings.append(geometry)

for p in points:
    lat, lon = p['lat'], p['lon']
    scale = math.cos(math.radians(lat))
    point = Point(lon * scale, lat)
    nearby = []
    ai_outline = p.get('outline') or []
    try:
        ai_polygon = Polygon([(longitude, latitude) for latitude, longitude in ai_outline])
        if not ai_polygon.is_valid or ai_polygon.area == 0:
            ai_polygon = None
    except (TypeError, ValueError):
        ai_polygon = None
    for building in buildings:
        components = list(building.geoms) if building.geom_type == 'MultiPolygon' else [building]
        for component in components:
            score = footprint_agreement(ai_polygon, component)
            if score is None:
                continue
            scaled = transform(lambda x, y, z=None: (x * scale, y), component)
            distance = scaled.distance(point) * 111320
            if distance <= 5:
                nearby.append((-score, distance, scaled.area, component))
    if nearby:
        _, distance, _, building = min(nearby, key=lambda v: (v[0], v[1], v[2]))
        if building.geom_type == 'MultiPolygon':
            building = min(building.geoms, key=lambda g: g.distance(Point(lon, lat)))
        p['outline'] = [[y, x] for x, y in building.exterior.coords]
        p['source'] = '国土地理院 地理院地図Vector（個別建物の更新日不明）'
        p['outline_type'] = 'gsi'
        p['distance_m'] = round(distance, 1)
    else:
        p['outline'] = ai_outline if ai_polygon is not None else None
        p['outline_type'] = 'ai' if p['outline'] else 'none'
        p['source'] = '国土地理院の外周と一致しないため、AIの推定輪郭（要確認）'
json.dump(points, sys.stdout, ensure_ascii=False)
