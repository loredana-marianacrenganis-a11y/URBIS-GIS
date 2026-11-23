
import colorsys
import io

import geopandas as gpd
import pandas as pd
import pydeck as pdk
import streamlit as st
from shapely.geometry import MultiPolygon, box

import estimare
import osm
import raport

AREAS = {
    "Municipiul Iași (complet)": (27.500, 47.115, 27.670, 47.215),
    "Centru – Copou": (27.555, 47.155, 27.600, 47.190),
    "Tătărași – Țicău": (27.585, 47.150, 27.625, 47.180),
    "Nicolina – CUG": (27.520, 47.125, 27.590, 47.160),
    "Păcurari – Canta – Dacia": (27.520, 47.160, 27.570, 47.190),
    "Alexandru cel Bun – Mircea cel Bătrân": (27.530, 47.145, 27.575, 47.170),
}
PERIODS = [(0, 1950, "până în 1950"), (1951, 1970, "1951–1970"), (1971, 1990, "1971–1990"),
           (1991, 2010, "1991–2010"), (2011, 2100, "2011–prezent")]
MAX_LEVEL = 27
UNKNOWN_RGB = [245, 245, 245]


def level_rgb(n):
    if not n or pd.isna(n):
        return UNKNOWN_RGB
    n = min(int(n), MAX_LEVEL)
    hue = 60 * (n - 1) / 4 if n <= 5 else 60 + 240 * (n - 5) / (MAX_LEVEL - 5)
    r, g, b = colorsys.hsv_to_rgb(hue / 360, 0.75, 1.0)
    return [int(r * 255), int(g * 255), int(b * 255)]


def period(year):
    if pd.isna(year):
        return "an necunoscut"
    for a, b, lbl in PERIODS:
        if a <= year <= b:
            return lbl
    return "an necunoscut"


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_area(bounds):
    gdf = osm.fetch_buildings(box(*bounds))
    if gdf.empty:
        return gdf, {}
    gdf = gdf.copy()
    gdf["niv"] = gdf.apply(raport._levels, axis=1)
    gdf["an_nr"] = pd.to_numeric(gdf["an"].astype(str).str.extract(r"(\d{4})")[0],
                                 errors="coerce")
    gdf["perioada"] = gdf["an_nr"].map(period)
    gdf["tip_ro"] = gdf["tip"].map(lambda t: raport.TIP_RO.get(t, t))
    gdf["amprenta"] = gdf.to_crs(raport.STEREO70).area.round(0)
    gdf, info = estimare.estimate(gdf, "niv")
    return gdf, info


def to_deck_rows(gdf):
    rows = []
    for r in gdf.itertuples():
        g = r.geometry
        if isinstance(g, MultiPolygon):
            g = max(g.geoms, key=lambda p: p.area)
        niv = None if pd.isna(r.niv_afis) else int(r.niv_afis)
        est = niv is not None and pd.isna(r.niv)
        rows.append({
            "polygon": [list(c) for c in g.exterior.coords],
            "color": level_rgb(niv) + [120 if est else 235],
            "elev": (niv or 1) * 3.0,
            "tip": r.tip_ro,
            "niv_txt": (f"{niv} ({'P' if niv == 1 else f'P+{niv - 1}'})"
                        + (" – ESTIMAT" if est else " – OSM")) if niv else "necunoscut",
            "an_txt": str(int(r.an_nr)) if not pd.isna(r.an_nr) else "necunoscut",
            "adresa": r.adresa or "",
            "nume": r.nume or "",
            "amprenta": int(r.amprenta),
        })
    return rows


def legend_html():
    cells = []
    for n in (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 21, 24, 27):
        r, g, b = level_rgb(n)
        cells.append(f"<span style='display:inline-block;width:30px;text-align:center;"
                     f"background:rgb({r},{g},{b});font-size:.75rem;padding:2px 0;"
                     f"border-radius:3px;margin:1px'>{n}{'+' if n == MAX_LEVEL else ''}</span>")
    cells.append("<span style='display:inline-block;padding:2px 6px;background:#f5f5f5;"
                 "border:1px solid #ccc;font-size:.75rem;border-radius:3px;margin:1px'>"
                 "necunoscut</span>")
    return ("<div><b>Niveluri:</b> " + "".join(cells) + "</div><div style='font-size:.8rem;"
            "color:#555;margin-top:4px'>Culori pline = niveluri din OSM · culori estompate = "
            "niveluri estimate</div>")


def stats_table(gdf):
    lv = gdf["niv_afis"].map(lambda n: "necunoscut" if pd.isna(n) else
                        (f"{MAX_LEVEL}+" if n >= MAX_LEVEL else int(n)))
    t = pd.crosstab(lv, gdf["perioada"], margins=True, margins_name="Total")
    cols = [p for _, _, p in PERIODS if p in t.columns] + \
           [c for c in ("an necunoscut", "Total") if c in t.columns]
    order = sorted([i for i in t.index if isinstance(i, int)]) + \
        [i for i in (f"{MAX_LEVEL}+", "necunoscut", "Total") if i in t.index]
    t = t.loc[order, cols]
    t.index = [str(i) for i in t.index]
    t.index.name = "Niveluri"
    return t


def excel_bytes(gdf, stats):
    buf = io.BytesIO()
    cols = ["osm_id", "tip_ro", "niv", "niv_final", "niv_sursa", "an_nr", "perioada",
            "amprenta", "adresa", "nume"]
    out = pd.DataFrame(gdf[cols]).rename(columns={
        "tip_ro": "tip", "niv": "niveluri_osm", "niv_final": "niveluri_osm_sau_estimate",
        "niv_sursa": "sursa_nivelurilor", "an_nr": "an", "amprenta": "amprenta_m2"})
    c = gdf.geometry.representative_point()
    out["lat"], out["lon"] = c.y.round(6).values, c.x.round(6).values
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        out.to_excel(xw, sheet_name="Constructii", index=False)
        stats.to_excel(xw, sheet_name="Statistici")
    return buf.getvalue()


def render():
    st.markdown("Toate construcțiile din Iași colorate după **numărul de niveluri** "
                "(date OpenStreetMap). Treci cu mouse-ul peste o clădire pentru detalii; "
                "comută pe 3D ca să vezi volumele.")
    c1, c2, c3 = st.columns([1.4, 1, 1])
    area_name = c1.selectbox("Zona încărcată", list(AREAS), key="hi_area")
    view3d = c2.toggle("Vedere 3D", value=False, key="hi_3d")
    q = c3.text_input("Caută o adresă", placeholder="ex. Piața Unirii", key="hi_q")

    if not st.session_state.get(f"hi_loaded_{area_name}"):
        st.info("Datele se descarcă din OpenStreetMap la prima deschidere a zonei "
                "(pentru tot municipiul durează cam un minut; apoi rămân în memorie 24 h).")
        if not st.button("🏢 Încarcă harta construcțiilor", type="primary", key="hi_go"):
            return
        st.session_state[f"hi_loaded_{area_name}"] = True
        st.rerun()
    try:
        with st.spinner(f"Descarc construcțiile – {area_name}..."):
            gdf, info = load_area(AREAS[area_name])
    except Exception as e:
        st.session_state[f"hi_loaded_{area_name}"] = False
        st.error(f"Serviciul OpenStreetMap (Overpass) nu a răspuns: {e}. Încearcă o zonă mai "
                 "mică sau revino peste un minut.")
        return
    if gdf.empty:
        st.warning("Nu am găsit construcții în zona aleasă.")
        return

    use_est = st.toggle("Completează înălțimile lipsă cu estimări", value=True, key="hi_est")
    if use_est and info.get("estimate"):
        if "eroare_medie" in info:
            st.info(f"**Estimare:** {info['estimate']:,} clădiri fără niveluri în OSM au "
                    f"primit o valoare estimată de un {info['metoda']} "
                    f"({info['cunoscute']:,} clădiri). Testat pe clădirile cu niveluri "
                    f"cunoscute: eroare medie **±{info['eroare_medie']:.1f} niveluri**, exact "
                    f"în {info['exact_pct']:.0f}% din cazuri, cu cel mult un nivel diferență în "
                    f"{info['plus_minus_1_pct']:.0f}% (față de ±{info['eroare_fara_model']:.1f} "
                    "fără model).".replace(",", "."))
        else:
            st.info(f"**Estimare:** {info['estimate']:,} clădiri au primit o valoare estimată "
                    f"prin {info['metoda']}. Precizia nu poate fi măsurată în această zonă."
                    .replace(",", "."))
    gdf = gdf.copy()
    gdf["niv_afis"] = gdf["niv_final"] if use_est else gdf["niv"]

    f1, f2, f3 = st.columns([1.3, 1, 1])
    lo, hi = f1.slider("Niveluri", 1, MAX_LEVEL, (1, MAX_LEVEL), key="hi_lv")
    unknown = f1.checkbox("Include construcțiile cu înălțime necunoscută", True, key="hi_unk")
    per = f2.multiselect("Perioada construcției",
                         [p for _, _, p in PERIODS] + ["an necunoscut"], key="hi_per",
                         placeholder="toate")
    tops = gdf["tip_ro"].value_counts().index[:15].tolist()
    tip = f3.multiselect("Tip de clădire", tops, key="hi_tip", placeholder="toate")

    m = gdf["niv_afis"].between(lo, hi) | (unknown & gdf["niv_afis"].isna())
    if per:
        m &= gdf["perioada"].isin(per)
    if tip:
        m &= gdf["tip_ro"].isin(tip)
    sel = gdf[m]

    k = st.columns(4)
    k[0].metric("Construcții afișate", f"{len(sel):,}".replace(",", "."))
    known = sel["niv_afis"].notna()
    k[1].metric("Niveluri din OSM / estimate",
                f"{100 * sel['niv'].notna().mean():.0f}% / "
                f"{100 * (known & sel['niv'].isna()).mean():.0f}%" if len(sel) else "–")
    k[2].metric("Nivel mediu", f"{sel.loc[known, 'niv_afis'].mean():.1f}" if known.any() else "–")
    k[3].metric("Cea mai înaltă", f"{int(sel['niv_afis'].max())} niveluri" if known.any() else "–")

    center = (47.165, 27.585, 13)
    if q:
        try:
            res = osm.geocode(q, limit=1)
            if res:
                center = (res[0][1], res[0][2], 17)
        except Exception:
            st.caption("Căutarea adresei nu a funcționat.")
    view = pdk.ViewState(latitude=center[0], longitude=center[1], zoom=center[2],
                         pitch=50 if view3d else 0, bearing=-15 if view3d else 0)
    layer = pdk.Layer(
        "PolygonLayer", to_deck_rows(sel), get_polygon="polygon", get_fill_color="color",
        get_line_color=[80, 80, 80], line_width_min_pixels=0.3, pickable=True,
        auto_highlight=True, extruded=view3d, get_elevation="elev", stroked=not view3d,
        opacity=0.9)
    st.pydeck_chart(pdk.Deck(
        layers=[layer], initial_view_state=view, map_provider="carto", map_style="light",
        tooltip={"html": "<b>{tip}</b> {nume}<br/>Niveluri: {niv_txt}<br/>"
                         "An construcție: {an_txt}<br/>Amprentă: {amprenta} m²<br/>{adresa}",
                 "style": {"fontSize": "12px"}}), height=620)
    st.markdown(legend_html(), unsafe_allow_html=True)
    st.caption("Date © contribuitorii OpenStreetMap (ODbL). Numărul de niveluri și anul "
               "construcției nu sunt completate pentru toate clădirile; harta nu e completă "
               "și poate conține erori.")

    st.markdown("#### Construcții după număr de niveluri și perioada construcției")
    stats = stats_table(sel)
    st.dataframe(stats, width="stretch")
    st.download_button("⬇️ Descarcă baza de date (Excel)", excel_bytes(sel, stats),
                       file_name="constructii_iasi_inaltime.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key="hi_xlsx")
