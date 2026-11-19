import json
import math
import re
import sys
import urllib.error
import urllib.request

import mapbox_vector_tile
from shapely.geometry import Polygon, box, shape, mapping
from shapely.ops import transform

ZOOM = 16
N = 2 ** ZOOM


def tile_of(lat, lon):
    return (int((lon + 180) / 360 * N),
            int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * N))


def to_latlon(x, y, tx, ty, extent):
    lon = (tx + x / extent) / N * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + (extent-y)/extent) / N))))
    return lon, lat


vertices = json.load(sys.stdin)
region = Polygon([(lon, lat) for lat, lon in vertices])
west, south, east, north = region.bounds
x0, y0 = tile_of(north, west)
x1, y1 = tile_of(south, east)
if (x1-x0+1) * (y1-y0+1) > 100:
    raise ValueError('範囲が広すぎます。小さく囲んでください。')
features = []
for x in range(x0, x1 + 1):
    for y in range(y0, y1 + 1):
        url = f'https://cyberjapandata.gsi.go.jp/xyz/experimental_bvmap/{ZOOM}/{x}/{y}.pbf'
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                tile = mapbox_vector_tile.decode(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            raise
        layer = tile.get('building', {})
        extent = layer.get('extent', 4096)
        tile_west, tile_north = to_latlon(0, extent, x, y, extent)
        tile_east, tile_south = to_latlon(extent, 0, x, y, extent)
        tile_bounds = box(tile_west, tile_south, tile_east, tile_north)
        for feature in layer.get('features', []):
            if feature['geometry']['type'] not in ('Polygon', 'MultiPolygon', 'LineString', 'MultiLineString'):
                continue
            geometry = transform(lambda px, py, z=None: to_latlon(px, py, x, y, extent), shape(feature['geometry']))
            if geometry.is_empty or not geometry.is_valid or not geometry.intersects(region):
                continue
            # 各タイルの外側にある重複したバッファを除く。
            geometry = geometry.intersection(tile_bounds)
            if geometry.is_empty or geometry.geom_type not in ('Polygon', 'MultiPolygon', 'LineString', 'MultiLineString'):
                continue
            features.append({'type':'Feature','geometry':mapping(geometry),'properties': {k: v for k, v in feature.get('properties', {}).items() if k in ('devDate', 'lfSpanFr', 'lfSpanTo')}})
snapshot = None
try:
    request = urllib.request.Request('https://raw.githubusercontent.com/gsi-cyberjapan/gsimaps-vector-experiment/master/README.md',
                                     headers={'User-Agent': 'AerialComparison/1.0'})
    with urllib.request.urlopen(request, timeout=8) as response:
        text = response.read().decode('utf-8')
    match = re.search(r'データ更新情報</t[dh]>\s*<td>\s*(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if match:
        snapshot = f'{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}'
except (OSError, UnicodeError):
    pass
json.dump({'type':'FeatureCollection','features':features,
           'metadata':{'snapshot_date':snapshot, 'building_date_note':'個別建物の作成・更新日は不明',
                       'source':'国土地理院 地理院地図Vector'}}, sys.stdout, ensure_ascii=False)
