
import time

import pandas as pd
import requests
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_MIRRORS = [OVERPASS, "https://overpass.kumi.systems/api/interpreter",
                    "https://overpass.private.coffee/api/interpreter"]
TILE_DEG = 0.06
NOMINATIM = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "urbis-gis/1.0 (proiect de cercetare)"}

IASI_VIEWBOX = "27.45,47.25,27.72,47.08"


def geocode(query, limit=5):
    r = requests.get(NOMINATIM, headers=HEADERS, timeout=20, params={
        "q": query, "format": "json", "limit": limit, "countrycodes": "ro",
        "viewbox": IASI_VIEWBOX, "bounded": 1, "accept-language": "ro"})
    r.raise_for_status()
    return [(d["display_name"], float(d["lat"]), float(d["lon"])) for d in r.json()]


def _ring(geom):
    return [(p["lon"], p["lat"]) for p in geom]


def _to_polygon(el):
    if el["type"] == "way" and len(el.get("geometry", [])) >= 4:
        poly = Polygon(_ring(el["geometry"]))
        return poly if poly.is_valid else poly.buffer(0)
    if el["type"] == "relation":
        outers, inners = [], []
        for m in el.get("members", []):
            if m.get("type") != "way" or len(m.get("geometry", [])) < 4:
                continue
            ring = Polygon(_ring(m["geometry"]))
            (inners if m.get("role") == "inner" else outers).append(ring.buffer(0))
        if outers:
            shape = unary_union(outers)
            return shape.difference(unary_union(inners)) if inners else shape
    return None


def parse_overpass(data):
    rows = []
    for el in data.get("elements", []):
        geom = _to_polygon(el)
        if geom is None or geom.is_empty or not isinstance(geom, (Polygon, MultiPolygon)):
            continue
        t = el.get("tags", {})
        rows.append({
            "osm_id": f"{el['type']}/{el['id']}",
            "tip": t.get("building", "yes"),
            "niveluri": t.get("building:levels"),
            "inaltime": t.get("height"),
            "an": t.get("start_date"),
            "nume": t.get("name"),
            "adresa": " ".join(x for x in (t.get("addr:street"), t.get("addr:housenumber")) if x) or None,
            "geometry": geom,
        })
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326) if rows else \
        gpd.GeoDataFrame(columns=["osm_id", "tip", "niveluri", "inaltime", "an", "nume",
                                  "adresa", "geometry"], geometry="geometry", crs=4326)
    return gdf


def _post_overpass(q, start=0):
    erori = []
    ordine = OVERPASS_MIRRORS[start % len(OVERPASS_MIRRORS):] + OVERPASS_MIRRORS[:start % len(OVERPASS_MIRRORS)]
    for incercare, url in enumerate(ordine * 2):
        try:
            r = requests.post(url, data={"data": q}, headers=HEADERS, timeout=95)
            if r.status_code in (429, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            erori.append(f"{url.split('/')[2]}: {e}")
            time.sleep(3 + 2 * incercare)
    raise RuntimeError("Overpass indisponibil (" + "; ".join(erori[-3:]) + ")")


def _tiles(minx, miny, maxx, maxy, step=TILE_DEG):
    nx = max(1, int(-(-(maxx - minx) // step)))
    ny = max(1, int(-(-(maxy - miny) // step)))
    dx, dy = (maxx - minx) / nx, (maxy - miny) / ny
    return [(minx + i * dx, miny + j * dy, minx + (i + 1) * dx, miny + (j + 1) * dy)
            for i in range(nx) for j in range(ny)]


def fetch_buildings(zone_wgs84):
    from concurrent.futures import ThreadPoolExecutor

    tiles = _tiles(*zone_wgs84.bounds)

    def one(i_t):
        i, (x0, y0, x1, y1) = i_t
        t0 = time.time()
        q = (f'[out:json][timeout:90];(way["building"]({y0},{x0},{y1},{x1});'
             f'relation["building"]({y0},{x0},{y1},{x1}););out geom;')
        g = parse_overpass(_post_overpass(q))
        print(f"[osm] bucata {i + 1}/{len(tiles)}: {len(g)} cladiri in {time.time() - t0:.0f} s", flush=True)
        return g

    with ThreadPoolExecutor(max_workers=min(2, len(tiles))) as ex:
        parts = [g for g in ex.map(one, enumerate(tiles)) if not g.empty]
    if not parts:
        return parse_overpass({})
    gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=4326)
    gdf = gdf.drop_duplicates("osm_id").reset_index(drop=True)
    return gdf[gdf.geometry.representative_point().within(zone_wgs84)].reset_index(drop=True)


ROAD_TYPES = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
              "service", "living_street", "pedestrian"]
POI_TAGS = {"amenity": ["school", "kindergarten", "university", "college", "hospital", "clinic", "doctors",
                        "pharmacy", "townhall", "police", "fire_station", "post_office", "library",
                        "place_of_worship", "marketplace"]}
POI_RO = {"school": "școală", "kindergarten": "grădiniță", "university": "universitate", "college": "colegiu",
          "hospital": "spital", "clinic": "clinică", "doctors": "cabinet medical", "pharmacy": "farmacie",
          "townhall": "primărie", "police": "poliție", "fire_station": "pompieri", "post_office": "poștă",
          "library": "bibliotecă", "place_of_worship": "lăcaș de cult", "marketplace": "piață"}


def _overpass(q):
    return _post_overpass(q)


def fetch_roads(zone_wgs84):
    from shapely.geometry import LineString
    minx, miny, maxx, maxy = zone_wgs84.bounds
    rx = "|".join(ROAD_TYPES)
    data = _overpass(f'[out:json][timeout:90];way["highway"~"^({rx})$"]({miny},{minx},{maxy},{maxx});out geom;')
    rows = []
    for el in data.get("elements", []):
        pts = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
        if len(pts) < 2:
            continue
        t = el.get("tags", {})
        rows.append({"osm_id": f"way/{el['id']}", "tip": t.get("highway"), "nume": t.get("name"),
                     "geometry": LineString(pts)})
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326) if rows else \
        gpd.GeoDataFrame(columns=["osm_id", "tip", "nume", "geometry"], geometry="geometry", crs=4326)
    return gdf[gdf.intersects(zone_wgs84)].reset_index(drop=True) if len(gdf) else gdf


def fetch_poi(zone_wgs84):
    from shapely.geometry import Point
    minx, miny, maxx, maxy = zone_wgs84.bounds
    vals = "|".join(POI_TAGS["amenity"])
    data = _overpass(f'[out:json][timeout:90];nwr["amenity"~"^({vals})$"]({miny},{minx},{maxy},{maxx});out center;')
    rows = []
    for el in data.get("elements", []):
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None:
            continue
        t = el.get("tags", {})
        rows.append({"osm_id": f"{el['type']}/{el['id']}", "categorie": POI_RO.get(t.get("amenity"), t.get("amenity")),
                     "nume": t.get("name"), "geometry": Point(lon, lat)})
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326) if rows else \
        gpd.GeoDataFrame(columns=["osm_id", "categorie", "nume", "geometry"], geometry="geometry", crs=4326)
    return gdf[gdf.within(zone_wgs84)].reset_index(drop=True) if len(gdf) else gdf
