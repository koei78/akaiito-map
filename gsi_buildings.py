import json
import math
import sys
import urllib.error
import urllib.request

import mapbox_vector_tile
from shapely.geometry import Point, shape
from shapely.ops import transform

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
    x, y = tile_of(p['lat'], p['lon'])
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            tiles.add((x + dx, y + dy))
buildings = []
for x, y in tiles:
    try:
        with urllib.request.urlopen(URL.format(ZOOM, x, y), timeout=20) as response:
            tile = mapbox_vector_tile.decode(response.read())
    except urllib.error.HTTPError as error:
        if error.code == 404:
            continue
        raise
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
    for building in buildings:
        if not (building.bounds[0] - 0.0001 <= lon <= building.bounds[2] + 0.0001 and
                building.bounds[1] - 0.0001 <= lat <= building.bounds[3] + 0.0001):
            continue
        scaled = transform(lambda x, y, z=None: (x * scale, y), building)
        distance = scaled.distance(point) * 111320
        if distance <= 5:
            nearby.append((distance, scaled.area, building))
    if nearby:
        distance, _, building = min(nearby, key=lambda v: (v[0], v[1]))
        if building.geom_type == 'MultiPolygon':
            building = min(building.geoms, key=lambda g: g.distance(Point(lon, lat)))
        p['outline'] = [[y, x] for x, y in building.exterior.coords]
        p['source'] = '国土地理院 地理院地図Vector（建物）'
        p['distance_m'] = round(distance, 1)
    else:
        p['outline'] = None
        p['source'] = '近くに建物データなし'
json.dump(points, sys.stdout, ensure_ascii=False)
