
import json
import tempfile
import zipfile
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
import streamlit as st
from folium import plugins
from shapely.geometry import Point, shape
from shapely.ops import polygonize, unary_union
from streamlit_folium import st_folium

import osm
import raport
import raport_cladire

HERE = Path(__file__).parent
LAYER_DIR = HERE / "straturi"
ZONES_FILE = HERE / "zone_iasi.geojson"
MAX_FEATURES = 20000
MAX_ZONE_HA = 500
NAVY = "#1F2A4D"
PALETTE = ["#d7263d", "#1b998b", "#2e86ab", "#f49d37", "#7b2cbf",
           "#3f88c5", "#e36414", "#5c946e", "#c2185b", "#6d6875"]

ZONES = {
    "Copou": (47.1790, 27.5690),
    "City Center": (47.1620, 27.5850),
    "Student Campus": (47.1560, 27.6060),
    "Mixed area – CUG (vest)": (47.1600, 27.5280),
    "Mixed area – platforma industrială": (47.1480, 27.6250),
    "Mixed area – Bucium (est)": (47.1200, 27.6250),
}


@st.cache_resource(show_spinner="Pregătesc asistentul (documente + model)...")
def load_rag():
    import rag_iasi
    return rag_iasi


rag = load_rag()


def _prepare(gdf):
    if gdf.crs is None:
        minx, miny, maxx, maxy = gdf.total_bounds
        gdf = gdf.set_crs(4326 if (abs(maxx) <= 180 and abs(maxy) <= 90) else 3844)
    crs_name = gdf.crs.to_string()
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].to_crs(4326)
    for col in gdf.columns:
        if col != gdf.geometry.name and not pd.api.types.is_numeric_dtype(gdf[col]):
            gdf[col] = gdf[col].astype(str)
    return gdf, crs_name


@st.cache_data(show_spinner="Citesc fișierul...")
def read_layer(name, data, layer=None):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / name
        path.write_bytes(data)
        if path.suffix.lower() == ".zip":
            zipfile.ZipFile(path).extractall(d)
            shp = next(Path(d).rglob("*.shp"), None)
            if shp is None:
                raise ValueError("Arhiva nu conține niciun fișier .shp")
            path = shp
        gdf = gpd.read_file(path, layer=layer)
    return _prepare(gdf)


@st.cache_data
def gpkg_layers(name, data):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / name
        path.write_bytes(data)
        return list(gpd.list_layers(path)["name"])


def add_source(name, data):
    layers = gpkg_layers(name, data) if name.lower().endswith(".gpkg") else [None]
    for lyr in layers:
        key = f"{name} › {lyr}" if lyr and len(layers) > 1 else name
        if key not in st.session_state.layers:
            try:
                gdf, crs = read_layer(name, data, lyr)
                st.session_state.layers[key] = {"gdf": gdf, "crs": crs, "visible": True,
                                                "color_by": None}
            except Exception as e:
                st.warning(f"Nu am putut citi {key}: {e}")


def init_session():
    if "layers" not in st.session_state:
        st.session_state.layers = {}
        if LAYER_DIR.exists():
            for f in sorted(LAYER_DIR.iterdir()):
                if f.suffix.lower() in (".gpkg", ".geojson", ".json", ".zip"):
                    add_source(f.name, f.read_bytes())


def layers_panel():
    st.caption("Straturi de lucru doar pentru sesiunea ta (ex. RO-IAS-Evolution-2011-24). "
               "Pentru a le păstra în baza de date, folosește modulul Import.")
    uploads = st.file_uploader("Adaugă straturi (.gpkg, .geojson, .zip cu shapefile)",
                               type=["gpkg", "geojson", "json", "zip"],
                               accept_multiple_files=True, key="layers_up")
    for up in uploads or []:
        add_source(up.name, up.getvalue())
    for i, (key, lyr) in enumerate(list(st.session_state.layers.items())):
        gdf = lyr["gdf"]
        with st.expander(f"{key}  ({len(gdf):,} obiecte)".replace(",", ".")):
            lyr["visible"] = st.checkbox("Vizibil pe hartă", lyr["visible"], key=f"vis{i}")
            st.caption(f"Sistem de coordonate original: {lyr['crs']}")
            cats = [c for c in gdf.columns if c != gdf.geometry.name
                    and 1 < gdf[c].nunique() <= len(PALETTE)]
            opts = ["(o singură culoare)"] + cats
            cur = lyr["color_by"] if lyr["color_by"] in cats else opts[0]
            choice = st.selectbox("Colorează după", opts, opts.index(cur), key=f"col{i}")
            lyr["color_by"] = None if choice == opts[0] else choice
            if st.button("Elimină stratul", key=f"del{i}"):
                del st.session_state.layers[key]
                st.rerun()


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_osm(zone_wkt):
    from shapely import wkt
    return osm.fetch_buildings(wkt.loads(zone_wkt))


def circle(lat, lon, radius_m):
    p = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(3844)
    return p.buffer(radius_m, 64).to_crs(4326).iloc[0]


def zone_polygon_from_file(gdf):
    polys = gdf[gdf.geom_type.isin(["Polygon", "MultiPolygon"])].geometry
    if len(polys):
        return unary_union(polys.buffer(0))
    lines = gdf[gdf.geom_type.isin(["LineString", "MultiLineString", "LinearRing"])].geometry
    found = list(polygonize(unary_union(lines))) if len(lines) else []
    return unary_union(found) if found else None


def area_ha(geom):
    return gpd.GeoSeries([geom], crs=4326).to_crs(3844).area.iloc[0] / 1e4


def study_zone_polygons():
    if not ZONES_FILE.exists():
        return {}
    data = json.loads(ZONES_FILE.read_text(encoding="utf-8"))
    return {f["properties"].get("name", f"zona {i}"): shape(f["geometry"])
            for i, f in enumerate(data["features"])}


def show_sources(found):
    with st.expander(f"📄 Secțiunile găsite ({len(found)})"):
        for src, txt in found:
            st.markdown(f"**{src}**")
            st.text(txt)


def run_question(q):
    with st.spinner("Caut în documente..."):
        try:
            return rag.answer(q)
        except Exception as e:
            return f"⚠️ Eroare: {e}", []


def zone_text(name):
    key = name.split(" –")[0] + ":"
    for src, txt in rag.chunks:
        if "IasiStudy" in src and key in txt:
            return txt.split(key, 1)[1].split("\n")[0].strip()
    return ""


def color_map(gdf, field):
    values = sorted(gdf[field].dropna().unique(), key=str)
    return {v: PALETTE[i % len(PALETTE)] for i, v in enumerate(values)}


def base_map(location=(47.158, 27.590), zoom=12):
    m = folium.Map(location=list(location), zoom_start=zoom, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Light Gray", name="Hartă simplă (gri)").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery", name="Ortofoto (satelit)").add_to(m)
    return m


def evo_selector(key):
    layer_keys = list(st.session_state.layers)
    if not layer_keys:
        st.caption("Încarcă stratul RO-IAS-Evolution-2011-24 din bara laterală ca să apară "
                   "în raport secțiunea de densificare 2011–2024.")
        return None, None
    k = st.selectbox("Strat cu evoluția clădirilor 2011–2024", ["(fără)"] + layer_keys,
                     key=f"evo_{key}")
    if k == "(fără)":
        return None, None
    g = st.session_state.layers[k]["gdf"]
    f = st.selectbox("Câmpul cu tipul de schimbare",
                     [c for c in g.columns if c != g.geometry.name], key=f"evof_{key}")
    return g, f

