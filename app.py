# app.py - URBIS GIS: platformă GIS-IA pentru managementul teritoriului și analiza
# densificării urbane, cu validare pe municipiul Iași.
# Contract 29CI/2026, cod PN-IV-P7-7.1-CI-2025-0106 (UEFISCDI, cec de inovare) · TUIASI × HURJUI & ASOCIAȚII S.R.L.
#
# Module (cererea de finanțare): M1 bază de date spațio-temporală · M2 import și validare ·
# M3 hartă Web-GIS · M4 interogare spațială · M5 asistent GIS-IA cu RAG · M6 indicatori și
# rapoarte · M7 mapathon · M8 administrare și jurnal · WP0 prezentarea proiectului.
#
# Rulare: streamlit run app.py   (sau dublu-clic pe porneste_platforma.bat)

import io
import os
import random
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
from shapely.geometry import box

st.set_page_config(page_title="URBIS GIS", page_icon="🏙️", layout="wide")

import db  # noqa: E402
import estimare  # noqa: E402
import harta_inaltimi  # noqa: E402
import cautare_web  # noqa: E402
import osm  # noqa: E402
import pagini_rapoarte as PR  # noqa: E402
import raport  # noqa: E402
import raport_cladire  # noqa: E402
import swipe  # noqa: E402
from common import (rag, read_layer, gpkg_layers, init_session, circle, area_ha,  # noqa: E402
                    zone_polygon_from_file, show_sources, NAVY)

db.init()
init_session()

ROLES = ["Vizitator", "Validator", "Operator cadastru", "Administrator"]
WRITE_ROLES = ("Operator cadastru", "Administrator")
CHANGE_COLORS = {"nouă": [227, 100, 20], "extinsă": [46, 111, 216], "modificată": [122, 79, 217],
                 "demolată": [140, 147, 163], "neschimbată": [221, 226, 234]}
TIPURI = ["casă", "bloc de locuințe", "comercial / birouri", "industrial / depozit", "public",
          "anexă / garaj", "altul"]
TIP_OSM = {"casă": "house", "bloc de locuințe": "apartments", "comercial / birouri": "commercial",
           "industrial / depozit": "industrial", "public": "public", "anexă / garaj": "garage",
           "altul": "yes"}

st.markdown(f"""
<style>
  .urbis-bar {{background:{NAVY};color:#fff;border-radius:12px;padding:14px 20px;margin:-8px 0 14px 0;
               display:flex;flex-wrap:wrap;align-items:center;gap:14px}}
  .urbis-logo {{width:36px;height:36px;border-radius:8px;background:#E36414;display:flex;align-items:center;
                justify-content:center;font-weight:700;font-size:17px}}
  .urbis-bar b {{font-size:1.15rem}} .urbis-bar span {{color:#C9D2E6;font-size:.85rem}}
  .tag {{display:inline-block;background:#E36414;color:#fff;border-radius:4px;padding:1px 6px;font-size:.75rem;
         font-weight:600;margin-left:6px}}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- rol și jurnal (M8)
def _demo_mode():
    return str(os.environ.get("URBIS_DEMO") or _secret("URBIS_DEMO") or "").strip().lower() in ("1", "true", "da", "yes")


def current_role():
    with st.sidebar:
        st.markdown("**Rol utilizator**")
        role = st.selectbox("Rol", ROLES, index=0 if _demo_mode() else 2, key="rol_ales", label_visibility="collapsed")
        need = os.environ.get("URBIS_PAROLA") or _secret("URBIS_PAROLA")
        if need and role in WRITE_ROLES:
            pw = st.text_input("Parolă pentru rolul ales", type="password", key="rol_parola")
            if pw != need:
                st.caption("Fără parola corectă lucrezi ca Vizitator.")
                role = "Vizitator"
        if role == "Validator":
            st.text_input("Numele tău (pentru mapathon)", key="autor", placeholder="ex. student 12")
    st.session_state.rol = role
    return role


def _secret(k):
    try:
        return st.secrets.get(k)
    except Exception:
        return None


def log(actiune, detalii="", t0=None):
    try:
        db.log(actiune, st.session_state.get("rol", ""), detalii,
               int((time.time() - t0) * 1000) if t0 else None)
    except Exception:
        pass


def header(title, module=None, subtitle=""):
    st.markdown(f"""<div class="urbis-bar"><div class="urbis-logo">U</div>
      <div style="display:flex;flex-direction:column"><b>URBIS GIS · {title}{f'<span class="tag">{module}</span>' if module else ''}</b>
      <span>{subtitle or 'GIS-IA Iași · densificare urbană · TUIASI × Hurjui &amp; Asociații'}</span></div></div>""",
                unsafe_allow_html=True)


# ---------------------------------------------------------------- date (cache pe versiunea bazei)
def _txt(df):
    """Coloanele text cu valori amestecate (de ex. anul construcției: 1975 sau „1975-1980”) devin text,
    altfel tabelul nu se poate afișa."""
    return df.assign(**{c: df[c].map(lambda x: "" if x is None or (isinstance(x, float) and x != x) else str(x))
                        for c in df.columns if df[c].dtype == object})


def db_version():
    v = db.read_sql("SELECT (SELECT COUNT(*) FROM constructie) a, (SELECT COUNT(*) FROM eveniment) b, "
                    "(SELECT COALESCE(MAX(id),0) FROM constructie) c")
    return tuple(v.iloc[0].tolist())


@st.cache_data(show_spinner=False)
def state_at(year, _ver):
    g = db.buildings_at(year)
    if g.empty:
        return g, {}
    g = g.copy()
    g["niv"] = pd.to_numeric(g["niveluri"], errors="coerce")
    g, info = estimare.estimate(g, "niv")
    g["niv_afis"] = g["niv_final"]
    g["niv_sursa_afis"] = np.where(g["niv"].notna(), g["niveluri_sursa"].fillna("fișier"), "estimat")
    return g, info


@st.cache_data(show_spinner=False)
def compare_years(y1, y2, _ver):
    return db.compare(y1, y2)


@st.cache_data(show_spinner=False)
def doc_chunks(_ver_docs):
    d = db.documents_rag()          # documentele cu date personale nu intră în index (GDPR)
    out = []
    for r in d.itertuples():
        if r.text:
            out += rag.split_text(f"{r.tip}: {r.titlu}", r.text)
    return out


def ask_assistant(q):
    t0 = time.time()
    ndocs = int(db.read_sql("SELECT COUNT(*) n FROM document").iloc[0, 0])
    with st.spinner("Caut în baza de date și în documente..."):
        try:
            text, found = rag.answer(q, extra=doc_chunks(ndocs), db_context=db.db_summary(),
                                     provider=st.session_state.get("llm", rag.llm_provider()),
                                     ollama_model=st.session_state.get("ollama_model"))
        except Exception as e:
            text, found = f"⚠️ Eroare: {e}", []
    log("întrebare asistent", {"intrebare": q[:300], "model": st.session_state.get("llm", rag.llm_provider())}, t0)
    return text, found


def deck_rows(g4326, colors, labels):
    rows = []
    for i, r in enumerate(g4326.itertuples()):
        geom = r.geometry
        if geom.geom_type == "MultiPolygon":
            geom = max(geom.geoms, key=lambda p: p.area)
        rows.append({"polygon": [list(c) for c in geom.exterior.coords], "color": colors[i],
                     "cod": r.cod_constructie, "info": labels[i]})
    return rows


def grid_cells(bounds, size=250):
    minx, miny, maxx, maxy = bounds
    xs = np.arange(minx - minx % size, maxx + size, size)
    ys = np.arange(miny - miny % size, maxy + size, size)
    cells = [box(x, y, x + size, y + size) for x in xs[:-1] for y in ys[:-1]]
    return gpd.GeoDataFrame({"unitate": [f"celula {i + 1}" for i in range(len(cells))]},
                            geometry=cells, crs=db.S70)


def aggregation_units(bounds, use_zones=True):
    z = db.zones()
    if use_zones and len(z):
        return z.rename(columns={"nume": "unitate"})[["unitate", "geometry"]]
    return grid_cells(bounds)


def pot_by_unit(g, units):
    if g.empty:
        return units.assign(pot=0.0, cladiri=0)
    pts = g.assign(geometry=g.geometry.representative_point(),
                   desf=g["amprenta_m2"] * g["niv_afis"].fillna(1))
    j = gpd.sjoin(pts[["amprenta_m2", "desf", "geometry"]], units, predicate="within")
    agg = j.groupby("index_right").agg(amprenta=("amprenta_m2", "sum"), cladiri=("amprenta_m2", "size"),
                                       desf=("desf", "sum"))
    u = units.join(agg).fillna({"amprenta": 0, "cladiri": 0, "desf": 0})
    u["pot"] = 100 * u["amprenta"] / u.area
    u["cut"] = u["desf"] / u.area
    return u


def pot_rgb(p):
    for lim, c in ((20, [244, 227, 211]), (35, [235, 185, 140]), (50, [217, 138, 74])):
        if p < lim:
            return c + [170]
    return [180, 72, 12, 190]


# ================================================================ M3 · HARTĂ
def page_harta():
    header("Hartă și căutare", "M3")
    ys = db.years()
    role = st.session_state.rol
    if not ys:
        st.info("Baza de date URBIS e goală. Încarcă un prim inventar de construcții pentru zona "
                "pilot – din OpenStreetMap acum, sau din fișierele voastre (ortofoto, ANCPI) în "
                "modulul **Import și validare (M2)**.")
        if role in WRITE_ROLES:
            c1, c2, c3 = st.columns([1.4, 0.8, 1])
            area = c1.selectbox("Zona pilot", list(harta_inaltimi.AREAS)[1:], key="pilot_area")
            an = c2.number_input("Anul datelor", 1990, 2100, 2024, key="pilot_an")
            if c3.button("📥 Importă construcțiile OSM ca inventar", type="primary"):
                t0 = time.time()
                with st.spinner("Descarc din OpenStreetMap și import în baza de date..."):
                    g = osm.fetch_buildings(box(*harta_inaltimi.AREAS[area]))
                    g = g.rename(columns={"an": "an_constructie"})
                    g["niveluri"] = g.apply(raport._levels, axis=1)
                    g["niveluri_sursa"] = np.where(g["niveluri"].notna(), "OSM", None)
                    sid = db.add_source(f"OpenStreetMap – {area}", "OSM", int(an), "metri", "ODbL")
                    rep = db.import_buildings(g, int(an), sid, utilizator=role)
                    db.set_zone(f"Zona pilot – {area}", "pilot", box(*harta_inaltimi.AREAS[area]))
                log("import OSM", rep, t0)
                st.success(f"Importate {rep['acceptate']} construcții.")
                st.rerun()
        return

    ver = db_version()
    c1, c2, c3 = st.columns([1.3, 1.6, 1.2])
    q = c1.text_input("Caută", placeholder="Nr. cadastral, nr. CF sau adresă", key="h_q")
    theme = c2.radio("Tematică", ["Înălțimi (niveluri)", "Densificare între ani", "POT pe zone"],
                     horizontal=True, key="h_theme")
    view3d = c3.toggle("Vedere 3D", key="h_3d")
    if len(ys) > 1:
        y1, y2 = st.select_slider("Perioada / data hărții", options=ys, value=(ys[0], ys[-1]), key="h_years")
    else:
        y1 = y2 = ys[0]
        st.caption(f"Date disponibile pentru anul {ys[0]}. Importă și alt an (ex. 2011) ca să "
                   "compari evoluția.")
    g, info = state_at(y2, ver)
    if g.empty:
        st.warning(f"Nu există construcții valabile în {y2}.")
        return

    g4 = g.to_crs(4326)
    center = g4.geometry.union_all().centroid
    view = (center.y, center.x, 14)
    hl = None
    if q:
        par = db.find_parcel(q.strip())
        if par is not None:
            p4 = par.to_crs(4326).geometry.iloc[0].centroid
            view = (p4.y, p4.x, 18)
            st.success(f"Imobil {q} găsit.")
        else:
            try:
                res = osm.geocode(q, limit=1)
                if res:
                    view = (res[0][1], res[0][2], 18)
                else:
                    st.warning("Nu am găsit nr. cadastral sau adresa.")
            except Exception:
                st.warning("Căutarea adresei nu a funcționat.")
        log("căutare", {"q": q})

    layers = []
    if theme.startswith("Densificare") and y1 != y2:
        cmp = compare_years(y1, y2, ver).to_crs(4326)
        cols = [CHANGE_COLORS[s] + [230] for s in cmp["schimbare"]]
        labels = [f"{s} · {t or ''} · {a:.0f} m²" for s, t, a in zip(cmp["schimbare"], cmp["tip"], cmp["amprenta_m2"])]
        data = deck_rows(cmp, cols, labels)
        legend = [(k, v) for k, v in CHANGE_COLORS.items()]
        vc = cmp["schimbare"].value_counts()
        k = st.columns(5)
        for i, s in enumerate(["nouă", "extinsă", "modificată", "demolată", "neschimbată"]):
            k[i].metric(s.capitalize(), int(vc.get(s, 0)))
    else:
        if theme.startswith("Densificare"):
            st.caption("Alege doi ani diferiți pe cursor ca să vezi schimbările.")
        est = g["niv"].isna().values
        cols = [harta_inaltimi.level_rgb(n) + [110 if e else 235] for n, e in zip(g["niv_afis"], est)]
        labels = [f"{(t or '')} · {'P' if n == 1 else f'P+{int(n) - 1}'} ({s}) · {a:.0f} m²"
                  for t, n, s, a in zip(g["tip"], g["niv_afis"], g["niv_sursa_afis"], g["amprenta_m2"])]
        data = deck_rows(g4, cols, labels)
        legend = None
        if theme.startswith("POT"):
            units = pot_by_unit(g, aggregation_units(g.total_bounds)).to_crs(4326)
            units = units[units["cladiri"] > 0]
            urows = [{"polygon": [list(c) for c in u.geometry.exterior.coords] if u.geometry.geom_type == "Polygon"
                      else [list(c) for c in max(u.geometry.geoms, key=lambda p: p.area).exterior.coords],
                      "color": pot_rgb(u.pot), "info": f"{u.unitate}: POT {u.pot:.1f}%, CUT {u.cut:.2f}",
                      "cod": ""} for u in units.itertuples()]
            layers.append(pdk.Layer("PolygonLayer", urows, id="zone", get_polygon="polygon",
                                    get_fill_color="color", get_line_color=[90, 60, 30], line_width_min_pixels=1,
                                    pickable=True))
            data = [dict(r, color=[60, 60, 60, 60]) for r in data]
    if view3d:
        lv = list(g["niv_afis"]) if not theme.startswith("Densificare") or y1 == y2 else \
            [float(n) if pd.notna(n) else 1.0 for n in cmp["niveluri"]]
        for r, n in zip(data, lv):
            r["elev"] = float(n if pd.notna(n) else 1) * 3
    layers.append(pdk.Layer("PolygonLayer", data, id="constructii", get_polygon="polygon",
                            get_fill_color="color", get_line_color=[60, 60, 60], line_width_min_pixels=0.4,
                            pickable=True, auto_highlight=True, extruded=view3d,
                            get_elevation="elev" if view3d else 0, stroked=not view3d))

    left, right = st.columns([2.3, 1], gap="medium")
    with left:
        ev = st.pydeck_chart(pdk.Deck(layers=layers, map_provider="carto", map_style="light",
                                      initial_view_state=pdk.ViewState(latitude=view[0], longitude=view[1],
                                                                       zoom=view[2], pitch=50 if view3d else 0),
                                      tooltip={"text": "{info}"}),
                             height=560, on_select="rerun", selection_mode="single-object", key="map_main")
        if legend:
            st.markdown(" ".join(f"<span style='display:inline-block;width:12px;height:12px;background:rgb"
                                 f"({c[0]},{c[1]},{c[2]});border:1px solid #999;margin:0 4px 0 10px'></span>{k}"
                                 for k, c in legend), unsafe_allow_html=True)
        else:
            st.markdown(harta_inaltimi.legend_html(), unsafe_allow_html=True)
        if info.get("estimate"):
            st.caption(f"{info['estimate']} construcții fără niveluri au valori estimate ({info['metoda']}"
                       + (f"; eroare medie ±{info['eroare_medie']:.1f} niveluri" if "eroare_medie" in info else "")
                       + "). Le poți valida în modulul Mapathon (M7).")
    sel = None
    try:
        objs = ev.selection.get("objects", {}).get("constructii", []) if ev else []
        if objs:
            sel = objs[0].get("cod")
    except Exception:
        pass
    if sel:
        st.session_state.sel_cod = sel
    with right:
        fisa(st.session_state.get("sel_cod"), g, y2)

    st.divider()
    st.markdown("##### 🤖 Asistent GIS-IA <span class='tag'>RAG · M5</span>", unsafe_allow_html=True)
    qa = st.chat_input("Întreabă despre construcții, indicatori sau reglementări…", key="dock_q")
    if qa:
        text, found = ask_assistant(qa)
        st.markdown(f"**Întrebare:** {qa}")
        st.markdown(text)
        if found:
            show_sources(found)


def fisa(cod, g, year):
    """Fișa construcției selectate: atribute, istoric, documente, raport, semnalare."""
    if not cod or cod not in set(g["cod_constructie"]):
        st.markdown("#### Fișa construcției")
        st.caption("Dă clic pe o construcție de pe hartă.")
        return
    r = g[g["cod_constructie"] == cod].iloc[0]
    niv = r["niv_afis"]
    st.markdown(f"#### {raport.TIP_RO.get(r['tip'], r['tip']).capitalize()}")
    st.caption(f"{cod}" + (f" · {r['adresa']}" if r.get("adresa") else "") + f" · stare la {year}")
    t1, t2, t3 = st.tabs(["Fișă", "Istoric", "Documente"])
    with t1:
        a, b = st.columns(2)
        a.metric("Regim de înălțime", ("P" if niv == 1 else f"P+{int(niv) - 1}") if pd.notna(niv) else "–")
        b.metric("Amprentă", f"{r['amprenta_m2']:.0f} m²")
        a.metric("Arie desfășurată", f"{r['amprenta_m2'] * niv:.0f} m²" if pd.notna(niv) else "–")
        b.metric("An construcție", r["an_constructie"] or "–")
        st.caption(f"Sursa nivelurilor: **{r['niv_sursa_afis']}** · sursa geometriei: {r.get('sursa') or '–'}")
    with t2:
        v, e = db.history(cod)
        st.dataframe(_txt(v.rename(columns={"valid_de_la": "de la", "valid_pana_la": "până la"})), hide_index=True,
                     width="stretch")
        st.dataframe(e[["an", "tip", "detalii", "sursa"]], hide_index=True, width="stretch")
    with t3:
        d = db.read_sql("SELECT tip, titlu, data FROM document WHERE cod_constructie=?", (cod,))
        st.dataframe(d, hide_index=True, width="stretch") if len(d) else st.caption("Niciun document atașat.")
    if st.button("📄 Generează raport PDF (construcție)", type="primary", width="stretch"):
        t0 = time.time()
        g4 = g.to_crs(4326)
        like = gpd.GeoDataFrame({"osm_id": g4["cod_constructie"], "tip": g4["tip"].fillna("yes"),
                                 "niveluri": g4["niv_afis"].map(lambda v: None if pd.isna(v) else str(int(v))),
                                 "inaltime": None, "an": g4["an_constructie"].astype(str).replace("None", None),
                                 "nume": g4["nume"], "adresa": g4["adresa"]}, geometry=g4.geometry, crs=4326)
        idx = like.index[like["osm_id"] == cod][0]
        ind = raport_cladire.compute(idx, like)
        ind["niv_sursa"] = r["niv_sursa_afis"]
        ind["osm_id"] = f"{cod} (baza de date URBIS)"
        pdf = raport_cladire.build_pdf(ind, r.get("adresa") or cod)
        st.session_state.fisa_pdf = (cod, pdf)
        log("raport PDF construcție", {"cod": cod}, t0)
    if st.session_state.get("fisa_pdf", (None,))[0] == cod:
        st.download_button("⬇️ Descarcă PDF-ul", st.session_state.fisa_pdf[1], f"raport_{cod}.pdf",
                           "application/pdf", width="stretch")
    with st.expander("🚩 Semnalează o eroare (mapathon)"):
        with st.form(f"obs_{cod}"):
            lv = st.number_input("Număr de niveluri corect", 1, 40, int(niv) if pd.notna(niv) else 1)
            tp = st.selectbox("Tip", TIPURI)
            ex = st.checkbox("Construcția există", True)
            com = st.text_input("Comentariu")
            if st.form_submit_button("Trimite observația"):
                c = r.geometry.representative_point()
                p = gpd.GeoSeries([c], crs=db.S70).to_crs(4326).iloc[0]
                db.add_observation(cod, st.session_state.get("autor") or st.session_state.rol, int(lv),
                                   TIP_OSM[tp], ex, com, p.y, p.x)
                log("observație mapathon", {"cod": cod})
                st.success("Observația a fost trimisă spre aprobare.")


# ================================================================ M4 · INTEROGARE
def page_interogare():
    header("Interogare spațială", "M4")
    ys = db.years()
    if not ys:
        st.info("Importă întâi date (modulul Hartă sau Import).")
        return
    ver = db_version()
    c1, c2, c3 = st.columns(3)
    an = c1.selectbox("Starea la anul", ys[::-1], key="q_an")
    g, _ = state_at(an, ver)
    tips = sorted(g["tip"].fillna("yes").map(lambda t: raport.TIP_RO.get(t, t)).unique())
    tip = c2.multiselect("Tip de construcție", tips, placeholder="toate")
    lo, hi = c3.slider("Niveluri (inclusiv estimate)", 1, 30, (1, 30))
    c4, c5, c6 = st.columns(3)
    src = c4.multiselect("Sursa nivelurilor", sorted(g["niv_sursa_afis"].unique()), placeholder="toate")
    amax = int(np.ceil(max(g["amprenta_m2"].max(), 10)))
    amp = c5.slider("Amprentă (m²)", 0, amax, (0, amax))
    chg = None
    if len(ys) > 1 and an != ys[0]:
        chg = c6.multiselect(f"Schimbare față de {ys[0]}", list(CHANGE_COLORS)[:-1], placeholder="oricare")
    st.markdown("**Filtru spațial**")
    s1, s2, s3 = st.columns([1, 1.6, 0.8])
    mode = s1.radio("Zona", ["toată baza", "în jurul unei adrese", "într-o zonă salvată"], key="q_sp",
                    label_visibility="collapsed")
    area = None
    if mode == "în jurul unei adrese":
        adr = s2.text_input("Adresă", key="q_adr")
        rad = s3.number_input("Rază (m)", 50, 2000, 300, 50)
        if adr:
            try:
                res = osm.geocode(adr, limit=1)
                if res:
                    area = gpd.GeoSeries([circle(res[0][1], res[0][2], rad)], crs=4326).to_crs(db.S70).iloc[0]
            except Exception:
                st.warning("Căutarea adresei nu a funcționat.")
    elif mode == "într-o zonă salvată":
        z = db.zones()
        if len(z):
            nm = s2.selectbox("Zonă", z["nume"])
            area = z[z["nume"] == nm].geometry.iloc[0]
        else:
            s2.caption("Nu există zone salvate; adaugă-le în Import (M2).")
    t0 = time.time()
    m = g["niv_afis"].between(lo, hi) & g["amprenta_m2"].between(*amp)
    tip_ro = g["tip"].fillna("yes").map(lambda t: raport.TIP_RO.get(t, t))
    if tip:
        m &= tip_ro.isin(tip)
    if src:
        m &= g["niv_sursa_afis"].isin(src)
    if chg:
        cmp = compare_years(ys[0], an, ver)
        m &= g["cod_constructie"].isin(cmp.loc[cmp["schimbare"].isin(chg), "cod_constructie"])
    if area is not None:
        m &= g.geometry.representative_point().within(area)
    res = g[m]
    log("interogare spațială", {"rezultate": int(len(res))}, t0)
    k = st.columns(4)
    k[0].metric("Construcții găsite", len(res))
    k[1].metric("Amprentă totală", f"{res['amprenta_m2'].sum():,.0f} m²".replace(",", "."))
    k[2].metric("Arie desfășurată", f"{(res['amprenta_m2'] * res['niv_afis']).sum():,.0f} m²".replace(",", "."))
    k[3].metric("Nivel mediu", f"{res['niv_afis'].mean():.1f}" if len(res) else "–")
    if len(res):
        r4 = res.to_crs(4326)
        rows = deck_rows(r4, [[227, 100, 20, 220]] * len(r4),
                         [f"{t} · P+{int(n) - 1}" for t, n in zip(tip_ro[m], res["niv_afis"])])
        c = r4.geometry.union_all().centroid
        st.pydeck_chart(pdk.Deck(layers=[pdk.Layer("PolygonLayer", rows, get_polygon="polygon",
                                                   get_fill_color="color", pickable=True)],
                                 map_provider="carto", map_style="light", tooltip={"text": "{info}"},
                                 initial_view_state=pdk.ViewState(latitude=c.y, longitude=c.x, zoom=14)),
                        height=380)
        table = pd.DataFrame(res.drop(columns=["geometry"]))[
            ["cod_constructie", "tip", "niv_afis", "niv_sursa_afis", "amprenta_m2", "an_constructie", "adresa",
             "valid_de_la", "sursa"]].rename(columns={"niv_afis": "niveluri", "niv_sursa_afis": "sursa_niveluri"})
        st.dataframe(_txt(table), hide_index=True, width="stretch", height=280)
        a, b = st.columns(2)
        a.download_button("⬇️ CSV", table.to_csv(index=False).encode("utf-8"), "interogare_urbis.csv", "text/csv")
        b.download_button("⬇️ GeoJSON", r4[["cod_constructie", "tip", "niv_afis", "amprenta_m2", "geometry"]]
                          .to_json().encode("utf-8"), "interogare_urbis.geojson", "application/geo+json")


# ================================================================ M5 · AGENT WEB
def page_cautare_web():
    cautare_web.render(header)


def page_swipe():
    swipe.render(header, db_version)


# ================================================================ M5 · ASISTENT
def page_asistent():
    header("Asistent GIS-IA", "M5", "Răspunde din baza de date URBIS, documentele încărcate și documentația "
                                    "tehnică a fluxului FLAIR-HUB2BF, cu sursa citată")
    with st.expander("⚙️ Model de limbaj și protecția datelor", expanded=False):
        opts = {"claude": "Claude (Anthropic, prin API)", "gemini": "Gemini (Google, cheie gratuită)", "ollama": "Model local (Ollama) – datele nu părăsesc calculatorul"}
        cur = st.session_state.get("llm", rag.llm_provider())
        st.session_state.llm = st.radio("Model", list(opts), format_func=opts.get,
                                        index=list(opts).index(cur) if cur in opts else 0, horizontal=True)
        if st.session_state.llm == "ollama":
            models = rag.ollama_models()
            if models:
                st.session_state.ollama_model = st.selectbox("Model local", models)
            else:
                st.warning(f"Ollama nu răspunde la {rag.OLLAMA_URL}. Instalează-l de pe ollama.com, apoi "
                           "rulează `ollama pull llama3.1`.")
        nd = db.documents()
        st.caption(f"Asistentul primește doar fragmentele relevante din documentele tehnice și un rezumat agregat al "
                   f"bazei de date (fără date despre persoane). Documente indexate: "
                   f"{int((nd['date_personale'] == 0).sum()) if len(nd) else 0}; excluse pentru că au date personale: "
                   f"{int((nd['date_personale'] == 1).sum()) if len(nd) else 0}.")
    _p = st.session_state.get("llm", rag.llm_provider())
    if (_p == "claude" and not rag.api_key()) or (_p == "gemini" and not rag.gemini_key()):
        st.warning("Asistentul nu are cheie API: răspunsurile se limitează la fragmentele găsite. "
                   "Adaug-o în cheie_api.txt (local) sau în Secrets (Streamlit Cloud), sau alege un model local.")
    if "messages" not in st.session_state:
        st.session_state.messages = []
    ex = ["Câte construcții noi au apărut între primul și ultimul an din baza de date?",
          "Ce pași are procesul FLAIR-HUB2BF?", "De ce nu s-au folosit clădirile ANCPI?",
          "Ce reglementări de urbanism sunt încărcate în platformă?"]
    cols = st.columns(len(ex))
    for c, e in zip(cols, ex):
        if c.button(e, width="stretch"):
            st.session_state.pending = e
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("found"):
                show_sources(msg["found"])
    q = st.chat_input("Scrie o întrebare…") or st.session_state.pop("pending", None)
    if q:
        st.session_state.messages.append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            text, found = ask_assistant(q)
            st.markdown(text)
            if found:
                show_sources(found)
        st.session_state.messages.append({"role": "assistant", "content": text, "found": found})


# ================================================================ M6 · INDICATORI
def page_indicatori():
    header("Indicatori de densificare", "M6")
    ys = db.years()
    if not ys:
        st.info("Importă întâi date.")
        return
    ver = db_version()
    if len(ys) > 1:
        y1, y2 = st.select_slider("Perioada", options=ys, value=(ys[0], ys[-1]), key="i_years")
    else:
        y1 = y2 = ys[0]
        st.caption("Un singur an în baza de date: indicatorii de evoluție apar după importul altui an.")
    t0 = time.time()
    g1, _ = state_at(y1, ver)
    g2, _ = state_at(y2, ver)
    bounds = pd.concat([g1, g2]).total_bounds
    has_z = len(db.zones()) > 0
    unit_kind = st.radio("Unități de analiză", ["zonele salvate (UTR, zone de studiu)", "grilă 250 × 250 m"],
                         index=0 if has_z else 1, horizontal=True, disabled=not has_z)
    units = aggregation_units(bounds, unit_kind.startswith("zonele"))
    u1 = pot_by_unit(g1, units)
    u2 = pot_by_unit(g2, units)
    t = pd.DataFrame({"unitate": units["unitate"], "suprafață (ha)": (units.area / 1e4).round(2),
                      f"clădiri {y1}": u1["cladiri"].astype(int), f"clădiri {y2}": u2["cladiri"].astype(int),
                      f"POT {y1} (%)": u1["pot"].round(1), f"POT {y2} (%)": u2["pot"].round(1),
                      "Δ POT (pp)": (u2["pot"] - u1["pot"]).round(1), f"CUT {y2}": u2["cut"].round(2)})
    if y1 != y2:
        cmp = compare_years(y1, y2, ver)
        pts = cmp.assign(geometry=cmp.geometry.representative_point())
        j = gpd.sjoin(pts, units, predicate="within")
        for s in ("nouă", "demolată", "extinsă"):
            t[f"{s}"] = j[j["schimbare"] == s].groupby("index_right").size().reindex(units.index).fillna(0).astype(int)
    t = t[(t[f"clădiri {y1}"] > 0) | (t[f"clădiri {y2}"] > 0)]
    log("calcul indicatori", {"perioada": f"{y1}-{y2}", "unitati": int(len(t))}, t0)
    tot1, tot2 = g1["amprenta_m2"].sum(), g2["amprenta_m2"].sum()
    k = st.columns(4)
    k[0].metric(f"Construcții {y2}", len(g2), len(g2) - len(g1) if y1 != y2 else None)
    k[1].metric(f"Amprentă {y2}", f"{tot2 / 1e4:.1f} ha", f"{(tot2 - tot1) / 1e4:+.1f} ha" if y1 != y2 else None)
    k[2].metric(f"Arie desfășurată {y2}", f"{(g2['amprenta_m2'] * g2['niv_afis']).sum() / 1e4:.1f} ha")
    k[3].metric("Unități de analiză", len(t), help="Zonele salvate (UTR, zone de studiu) sau, dacă nu există, "
                                                     "celule de 250 × 250 m.")
    st.dataframe(t, hide_index=True, width="stretch", height=360)
    if y1 != y2:
        tc = cmp["tip_schimbare"].value_counts()
        st.markdown("**Tipuri de schimbări între cei doi ani** (legăturile dintre clădiri, după modelul Bucher et al., 2025)")
        order = ["stabilitate", "extindere", "modificare", "construcție", "demolare", "înlocuire", "divizare",
                 "fuziune", "recompunere"]
        st.dataframe(pd.DataFrame({"tip de schimbare": [o for o in order if o in tc.index],
                                   "construcții": [int(tc[o]) for o in order if o in tc.index]}),
                     hide_index=True, width="content")
    if y1 != y2 and len(t):
        top = t.sort_values("Δ POT (pp)", ascending=False).head(15).set_index("unitate")["Δ POT (pp)"]
        st.markdown("**Unitățile cu cea mai mare creștere a POT**")
        st.bar_chart(top, color="#E36414", horizontal=True)
    a, b = st.columns(2)
    a.download_button("⬇️ Descarcă indicatorii (CSV)", t.to_csv(index=False).encode("utf-8"),
                      f"indicatori_{y1}_{y2}.csv", "text/csv")
    if st.session_state.rol in WRITE_ROLES and b.button("💾 Salvează indicatorii în baza de date"):
        with db.conn() as c:
            for r in t.itertuples(index=False):
                c.execute("INSERT INTO indicator(zona, tip, perioada, valoare, calculat_la) VALUES (?,?,?,?,?)",
                          (r[0], "POT", str(y2), float(r[5]), db.now()))
                c.execute("INSERT INTO indicator(zona, tip, perioada, valoare, calculat_la) VALUES (?,?,?,?,?)",
                          (r[0], "ΔPOT", f"{y1}-{y2}", float(r[6]), db.now()))
        log("salvare indicatori", {"n": int(len(t))})
        st.success("Indicatorii au fost salvați.")


# ================================================================ M6 · NECONCORDANȚE CADASTRALE
def page_neconcordante():
    header("Neconcordanțe cadastrale", "M6", "Diferențe între acte și măsurători, suprapuneri, construcții peste "
                                             "limita imobilului sau fără imobil")
    ys = db.years()
    py = db.read_sql("SELECT DISTINCT valid_de_la AS an FROM imobil")
    years_all = sorted(set(ys) | set(int(a) for a in py["an"].dropna()))
    if py.empty:
        st.info("Importă întâi imobilele (parcelele) beneficiarului în Import și validare › Imobile. "
                "Pentru diferențele de suprafață, mapează și coloana cu suprafața din acte.")
        return
    c1, c2 = st.columns(2)
    an = c1.selectbox("Anul", years_all, index=len(years_all) - 1)
    tol = c2.slider("Toleranță suprafață acte / măsurat (%)", 1.0, 20.0, 5.0, 0.5)
    t0 = time.time()
    with st.spinner("Verific imobilele și construcțiile..."):
        nc = db.inconsistencies(an, tol)
    log("raport neconcordanțe", {"an": an, "n": int(len(nc))}, t0)
    if nc.empty:
        st.success("Nicio neconcordanță găsită pentru criteriile alese.")
        return
    vc = nc["tip_neconcordanta"].value_counts()
    cols = st.columns(len(vc))
    for c, (k, v) in zip(cols, vc.items()):
        c.metric(k, int(v))
    import folium
    from streamlit_folium import st_folium
    from common import base_map
    colors = {"diferență suprafață acte / măsurată": "#E36414", "suprapunere de imobile": "#d7263d",
              "construcție peste limita imobilului": "#7B2CBF", "construcție fără imobil": "#1F77B4"}
    m = base_map()
    g = nc.to_crs(4326)
    folium.GeoJson(g, style_function=lambda f: {"color": colors.get(f["properties"]["tip_neconcordanta"], "#333"),
                                                "weight": 2, "fillOpacity": 0.35},
                   tooltip=folium.GeoJsonTooltip(fields=["tip_neconcordanta", "cod", "detalii"],
                                                 aliases=["Tip", "Cod", "Detalii"])).add_to(m)
    m.fit_bounds([[g.total_bounds[1], g.total_bounds[0]], [g.total_bounds[3], g.total_bounds[2]]])
    st_folium(m, height=460, use_container_width=True, returned_objects=[], key="nc_map")
    tab = pd.DataFrame(nc.drop(columns="geometry"))
    st.dataframe(tab, hide_index=True, width="stretch", height=300)
    st.download_button("⬇️ Raportul neconcordanțelor (CSV)", tab.to_csv(index=False).encode("utf-8"),
                       f"neconcordante_{an}.csv", "text/csv")
    st.caption("Rezultatul este informativ: semnalează situațiile de verificat, nu le corectează.")


# ================================================================ M8 · VALORI DE REFERINȚĂ PENTRU IMPACT
def page_impact():
    header("Valori de referință pentru impact", "M8", "Situația beneficiarului la finalul proiectului (2026), "
                                                       "de comparat cu 2027 în raportul de impact")
    if st.session_state.rol not in WRITE_ROLES:
        st.warning("Pagina e disponibilă pentru Operator cadastru și Administrator.")
        return
    ref = db.references()
    years_ref = sorted(set(ref["an"].astype(int))) if len(ref) else []
    an = st.selectbox("Anul pentru care completezi", sorted(set([2026, 2027] + years_ref)))
    st.caption("Completează valorile din evidența firmei. Jurnalul platformei (M8) dă automat numărul de rapoarte, "
               "importuri și întrebări; aici se adaugă indicatorii economici din cererea de finanțare (R1–R7).")
    with st.form("ref_form"):
        vals = {}
        for ind, unit, target in db.IMPACT_INDICATORS:
            prev = ref[(ref["an"] == an) & (ref["indicator"] == ind)]
            v0 = float(prev["valoare"].iloc[0]) if len(prev) and pd.notna(prev["valoare"].iloc[0]) else None
            vals[ind] = st.number_input(f"{ind} ({unit}) · țintă {target}", value=v0, min_value=0.0, step=1.0,
                                        format="%.1f", key=f"ref_{an}_{ind}")
        obs = st.text_input("Observații (sursa valorilor, ex. evidența contractelor 2026)")
        if st.form_submit_button("💾 Salvează", type="primary"):
            for ind, unit, _ in db.IMPACT_INDICATORS:
                db.set_reference(an, ind, vals[ind], unit, obs)
            log("valori de referință", {"an": an})
            st.success("Valorile au fost salvate.")
            st.rerun()
    if len(ref):
        piv = ref.pivot_table(index="indicator", columns="an", values="valoare", aggfunc="first")
        if 2026 in piv.columns and 2027 in piv.columns:
            piv["variație (%)"] = ((piv[2027] - piv[2026]) / piv[2026] * 100).round(1)
        st.markdown("**Comparație între ani**")
        st.dataframe(piv, width="stretch")
        st.download_button("⬇️ Exportă (CSV)", ref.to_csv(index=False).encode("utf-8"), "valori_referinta.csv",
                           "text/csv")
    j = db.read_sql("SELECT substr(ts,1,4) AS an, actiune, COUNT(*) n FROM jurnal GROUP BY 1, 2")
    if len(j):
        st.markdown("**Utilizarea platformei, din jurnal (M8)**")
        st.dataframe(j.pivot_table(index="actiune", columns="an", values="n", aggfunc="sum").fillna(0).astype(int),
                     width="stretch")


# ================================================================ M6 · RAPOARTE
def page_rapoarte():
    header("Rapoarte PDF", "M6", "Raport pe construcție sau pe zonă/parcelă, cu hartă, indicatori, "
                                 "interpretare AI și surse")
    st.caption("Pentru construcțiile din baza de date URBIS, raportul se generează din fișa construcției "
               "(Hartă și căutare). Aici: rapoarte pe orice construcție sau zonă din Iași, din OpenStreetMap.")
    t1, t2 = st.tabs(["🏠 Pe construcție (clic pe hartă)", "📄 Pe zonă sau parcelă"])
    with t1:
        PR.page_constructie_osm()
    with t2:
        PR.page_zona()


# ================================================================ M2 · IMPORT
GUESS = {"niveluri": ["niveluri", "levels", "building:levels", "nr_niv", "etaje", "niv"],
         "tip": ["tip", "type", "building", "destinatie", "functiune"],
         "an_constructie": ["an_constructie", "an", "year", "start_date", "an_constr"],
         "adresa": ["adresa", "address", "addr"], "nume": ["nume", "name", "denumire"],
         "nr_cadastral": ["nr_cadastral", "nrcad", "cadastral", "ie", "nr_cad"], "nr_cf": ["nr_cf", "cf", "carte"],
         "suprafata_acte": ["suprafata_acte", "s_acte", "supr_acte", "suprafata", "s_act", "area_acte"]}


def _guess(cols, key):
    low = {c.lower(): c for c in cols}
    for k in GUESS.get(key, [key]):
        if k in low:
            return low[k]
    return None


def page_import():
    header("Import și validare", "M2", "Fluxul de integrare a datelor multisursă (WP2): citire → Stereo 70 → "
                                       "validare → potrivire cu versiunile existente → versiuni și evenimente")
    if st.session_state.rol not in WRITE_ROLES:
        st.warning("Importul e disponibil pentru rolurile Operator cadastru și Administrator.")
        return
    with st.expander("Fluxul de lucru (WP2)", expanded=False):
        st.graphviz_chart("""digraph{rankdir=LR;node[shape=box,style="rounded,filled",fillcolor="#EEF1F6",fontname=Helvetica,fontsize=10];
        A[label="Sursă\\n(ortofoto, ANCPI,\\nprimărie, OSM, fișier)"];B[label="Citire\\nGPKG/SHP/DXF/\\nGeoJSON/KML"];
        C[label="Reproiectare\\nStereo 70"];D[label="Validare\\ngeometrii, duplicate,\\nsuprapuneri"];
        E[label="Potrivire cu versiunile\\nvalabile la anul datelor"];F[label="Versiuni + evenimente\\n(nouă, extinsă,\\ndemolată)",fillcolor="#FBE3D3"];
        G[label="Hartă, indicatori,\\nasistent RAG"];A->B->C->D->E->F->G}""")
    target = st.radio("Ce importi", ["Construcții", "Imobile (parcele)", "Zone (UTR, zonă pilot)",
                                     "Documente (normative, PUZ, CF)"], horizontal=True)
    if target.startswith("Documente"):
        return import_documents()
    c1, c2 = st.columns([1.3, 1])
    up = c1.file_uploader("Fișier (.gpkg, .geojson, .zip cu shapefile, .dxf, .kml)",
                          type=["gpkg", "geojson", "json", "zip", "dxf", "kml"])
    if not up:
        st.caption("Exemple: RO-IAS-Building-2011 și RO-IAS-Building-2024 (FLAIR-HUB2BF) – importate pe rând, cu "
                   "anul fiecăruia, produc automat stratul de schimbări 2011–2024.")
        return
    layer = None
    if up.name.lower().endswith(".gpkg"):
        lays = gpkg_layers(up.name, up.getvalue())
        layer = c2.selectbox("Strat din GeoPackage", lays)
    try:
        gdf, crs = read_layer(up.name, up.getvalue(), layer)
    except Exception as e:
        st.error(f"Nu am putut citi fișierul: {e}")
        return
    c2.caption(f"{len(gdf)} obiecte · sistem de coordonate original: {crs}")
    cols = [c for c in gdf.columns if c != gdf.geometry.name]
    d1, d2, d3 = st.columns(3)
    an = d1.number_input("Anul la care sunt valabile datele", 1900, 2100, 2024)
    sname = d2.text_input("Numele sursei", value=up.name.rsplit(".", 1)[0])
    stype = d3.selectbox("Tipul sursei", ["ortofoto", "fisier", "ANCPI", "primarie", "masuratori", "OSM"])
    if target == "Zone (UTR, zonă pilot)":
        f = st.selectbox("Câmpul cu numele zonei", ["(automat)"] + cols)
        ztip = st.selectbox("Tip zonă", ["UTR", "zonă de studiu", "cartier", "pilot"])
        if st.button("📥 Importă zonele", type="primary"):
            n = 0
            for i, r in gdf.iterrows():
                if r.geometry is not None and r.geometry.geom_type in ("Polygon", "MultiPolygon"):
                    db.set_zone(str(r[f]) if f != "(automat)" else f"{ztip} {i + 1}", ztip, r.geometry)
                    n += 1
            log("import zone", {"n": n})
            st.success(f"Importate {n} zone.")
        return
    keys = ["niveluri", "tip", "an_constructie", "adresa", "nume"] if target == "Construcții" else ["nr_cadastral", "nr_cf", "suprafata_acte"]
    st.markdown("**Corespondența câmpurilor**")
    mc = st.columns(len(keys))
    fmap = {}
    for c, k in zip(mc, keys):
        gs = _guess(cols, k)
        ch = c.selectbox(k, ["(lipsește)"] + cols, index=(cols.index(gs) + 1) if gs else 0, key=f"map_{k}")
        if ch != "(lipsește)":
            fmap[k] = ch
    complete = False
    if target == "Construcții":
        complete = st.checkbox("Fișierul acoperă complet perimetrul (construcțiile existente care lipsesc din "
                               "fișier sunt marcate demolate)", value=True)
    clean, rep = db.validate(gdf)
    st.markdown("**Validare**")
    v = st.columns(6)
    for c, (k, lbl) in zip(v, [("primite", "primite"), ("acceptate", "acceptate"), ("reparate", "geometrii reparate"),
                               ("duplicate", "duplicate eliminate"), ("prea_mici", "sub 5 m²"),
                               ("suprapuneri", "suprapuneri semnalate")]):
        c.metric(lbl, rep[k])
    if st.button("📥 Importă în baza de date", type="primary"):
        t0 = time.time()
        sid = db.add_source(sname, stype, int(an))
        with st.spinner("Import și potrivire cu versiunile existente..."):
            if target == "Construcții":
                g2 = gdf.copy()
                for k, col in fmap.items():
                    g2[k] = g2[col]
                if "niveluri" in fmap:
                    g2["niveluri_sursa"] = sname
                out = db.import_buildings(g2, int(an), sid, complete=complete, utilizator=st.session_state.rol)
            else:
                out = db.import_parcels(gdf, int(an), sid, fmap, st.session_state.rol)
        log(f"import {target.lower()}", out, t0)
        st.success("Import finalizat.")
        st.json({k: v for k, v in out.items() if v})


def import_documents():
    st.caption("Documentele intră în indexul asistentului RAG (M5). Exemplu: normativele de urbanism din "
               "domeniul XXIX al MDLPA (mdlpa.ro › Reglementări tehnice), PUZ/PUG, extrase CF.")
    ups = st.file_uploader("Documente (.pdf, .txt, .md)", type=["pdf", "txt", "md"], accept_multiple_files=True)
    tip = st.selectbox("Tip document", ["Normativ MDLPA", "PUG / PUZ", "Extras CF", "Autorizație de construire",
                                        "Plan de amplasament", "Altul"])
    leg = st.text_input("Legat de (nr. cadastral sau cod construcție, opțional)")
    pers = st.checkbox("Conține date personale (nume, CNP, adrese ale proprietarilor) – se păstrează în baza de date, "
                       "dar NU intră în indexul asistentului", value=(tip == "Extras CF"))
    if ups and st.button("📥 Adaugă documentele", type="primary"):
        t0 = time.time()
        n = 0
        for up in ups:
            if up.name.lower().endswith(".pdf"):
                try:
                    from pypdf import PdfReader
                    text = "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(up.getvalue())).pages)
                except Exception as e:
                    st.warning(f"{up.name}: nu am putut extrage textul ({e}).")
                    continue
            else:
                text = up.getvalue().decode("utf-8", errors="replace")
            if not text.strip():
                st.warning(f"{up.name}: fără text (posibil document scanat).")
                continue
            db.add_document(up.name, tip, text, cod_imobil=leg if leg and not leg.startswith("C-") else None,
                            cod_constructie=leg if leg.startswith("C-") else None, fisier=up.name,
                            date_personale=pers)
            n += 1
        log("import documente", {"n": n}, t0)
        st.success(f"Adăugate {n} documente în indexul asistentului.")
    d = db.documents()
    if len(d):
        st.dataframe(d.drop(columns=["text"]), hide_index=True, width="stretch")


# ================================================================ M1 · MODEL DE DATE
def page_model():
    header("Model de date", "M1", "Baza de date spațio-temporală – livrabil WP1")
    st.markdown("Nicio construcție sau parcelă nu se suprascrie: fiecare modificare închide versiunea veche "
                "(`valid_pana_la`) și deschide una nouă (`valid_de_la`), sub același cod, cu evenimentul și "
                "sursa ei. Așa se poate reconstitui starea fondului construit la orice an.")
    st.graphviz_chart(db.erd_dot(), width="stretch")
    counts = {t: int(db.read_sql(f"SELECT COUNT(*) n FROM {t}").iloc[0, 0]) for t in db.ENTITIES}
    st.markdown("**Înregistrări în baza de date**")
    st.dataframe(pd.DataFrame([(db.ENTITIES[k][0], v) for k, v in counts.items()],
                              columns=["Entitate", "Înregistrări"]), hide_index=True, width="stretch")
    a, b, c = st.columns(3)
    sql = (db.DB_PATH.parent / "schema_postgis.sql")
    if sql.exists():
        a.download_button("⬇️ Schema PostgreSQL + PostGIS (.sql)", sql.read_bytes(), "schema_urbis_postgis.sql")
    b.download_button("⬇️ Diagrama ERD (.dot)", db.erd_dot().encode("utf-8"), "erd_urbis.dot")
    if st.session_state.rol == "Administrator" and db.DB_PATH.exists():
        c.download_button("⬇️ Copie a bazei de date (.sqlite)", db.DB_PATH.read_bytes(), "urbis.sqlite")


# ================================================================ M7 · MAPATHON
def page_mapathon():
    header("Mapathon", "M7", "Validarea construcțiilor cu niveluri estimate sau lipsă, de către studenți și experți")
    ys = db.years()
    if not ys:
        st.info("Importă întâi date.")
        return
    role = st.session_state.rol
    obs = db.read_sql("SELECT * FROM observatie ORDER BY id DESC")
    k = st.columns(4)
    k[0].metric("Observații", len(obs))
    for i, s in enumerate(["nouă", "acceptată", "respinsă"]):
        k[i + 1].metric(s.capitalize(), int((obs["stare"] == s).sum()) if len(obs) else 0)
    tabs = st.tabs(["✅ Validează", "🗂️ Aprobare (administrator)"])
    with tabs[0]:
        g, _ = state_at(ys[-1], db_version())
        done = set(obs["cod_constructie"]) if len(obs) else set()
        todo = g[g["niv"].isna() & ~g["cod_constructie"].isin(done)]
        if todo.empty:
            st.success("Toate construcțiile au niveluri cunoscute sau au fost deja verificate.")
        else:
            rnd = random.Random(st.session_state.get("mp_seed", 1))
            cod = st.session_state.get("mp_cod")
            if cod not in set(todo["cod_constructie"]):
                cod = todo["cod_constructie"].iloc[rnd.randrange(len(todo))]
                st.session_state.mp_cod = cod
            r = todo[todo["cod_constructie"] == cod].iloc[0]
            st.caption(f"Rămase de verificat: {len(todo)} · construcția {cod}")
            r4 = gpd.GeoSeries([r.geometry], crs=db.S70).to_crs(4326)
            c = r4.iloc[0].centroid
            near = g.to_crs(4326).cx[c.x - 0.002:c.x + 0.002, c.y - 0.0015:c.y + 0.0015]
            rows = deck_rows(near, [[227, 100, 20, 230] if x == cod else [160, 170, 185, 120]
                                    for x in near["cod_constructie"]], [""] * len(near))
            st.pydeck_chart(pdk.Deck(layers=[pdk.Layer("PolygonLayer", rows, get_polygon="polygon",
                                                       get_fill_color="color", get_line_color=[40, 40, 40],
                                                       line_width_min_pixels=1)],
                                     map_provider="carto", map_style="light",
                                     initial_view_state=pdk.ViewState(latitude=c.y, longitude=c.x, zoom=18)),
                            height=300)
            est = int(r["niv_afis"]) if pd.notna(r["niv_afis"]) else 1
            with st.form("mp_form"):
                st.markdown(f"**Câte niveluri are construcția?** Estimare model: "
                            f"{'P' if est == 1 else f'P+{est - 1}'}")
                lv = st.radio("Niveluri", ["P", "P+1", "P+2", "P+3", "P+4", "P+5…P+7", "P+8 și peste"],
                              index=min(est - 1, 6), horizontal=True)
                tp = st.selectbox("Tip", TIPURI)
                ex = st.checkbox("Construcția există", True)
                com = st.text_input("Comentariu (opțional)")
                a, b = st.columns(2)
                send = a.form_submit_button("Trimite și treci la următoarea", type="primary")
                skip = b.form_submit_button("Sari peste")
            if send:
                n = {"P": 1, "P+1": 2, "P+2": 3, "P+3": 4, "P+4": 5, "P+5…P+7": 7, "P+8 și peste": 9}[lv]
                db.add_observation(cod, st.session_state.get("autor") or role, n, TIP_OSM[tp], ex, com, c.y, c.x)
                log("observație mapathon", {"cod": cod})
            if send or skip:
                st.session_state.mp_seed = st.session_state.get("mp_seed", 1) + 1
                st.session_state.mp_cod = None
                st.rerun()
    with tabs[1]:
        if role != "Administrator":
            st.info("Aprobarea observațiilor e disponibilă pentru Administrator.")
            return
        new = obs[obs["stare"] == "nouă"] if len(obs) else obs
        if new.empty:
            st.caption("Nicio observație de aprobat.")
        for o in new.head(30).itertuples():
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.markdown(f"**{o.cod_constructie}** · {o.autor or '–'} · niveluri {o.niveluri_propuse}, "
                        f"tip {o.tip_propus}, {'există' if o.exista else 'NU există'}"
                        + (f" · _{o.comentariu}_" if o.comentariu else ""))
            if c2.button("Acceptă", key=f"acc{o.id}"):
                db.resolve_observation(o.id, True, role, ys[-1])
                log("aprobare observație", {"id": o.id})
                st.rerun()
            if c3.button("Respinge", key=f"rej{o.id}"):
                db.resolve_observation(o.id, False, role, ys[-1])
                log("respingere observație", {"id": o.id})
                st.rerun()
        if len(obs):
            st.download_button("⬇️ Toate observațiile (CSV)", obs.to_csv(index=False).encode("utf-8"),
                               "observatii_mapathon.csv", "text/csv")


# ================================================================ M8 · JURNAL
def page_jurnal():
    header("Jurnal și statistici", "M8", "Date pentru raportul de impact (Etapa 2)")
    if st.session_state.rol not in WRITE_ROLES:
        st.warning("Jurnalul e disponibil pentru Operator cadastru și Administrator.")
        return
    j = db.read_sql("SELECT * FROM jurnal ORDER BY id DESC")
    if j.empty:
        st.caption("Jurnalul e gol.")
        return
    j["zi"] = j["ts"].str[:10]
    k = st.columns(5)
    k[0].metric("Acțiuni înregistrate", len(j))
    for c, (lbl, act) in zip(k[1:], [("Rapoarte PDF", "raport"), ("Întrebări asistent", "întrebare"),
                                     ("Importuri", "import"), ("Validări mapathon", "observație")]):
        c.metric(lbl, int(j["actiune"].str.contains(act).sum()))
    s = (j.groupby("actiune").agg(număr=("id", "size"), durata_medie_s=("durata_ms", lambda x: round(x.mean() / 1000, 2)))
         .sort_values("număr", ascending=False))
    st.markdown("**Pe tip de acțiune** (durata medie = timpul de lucru economisit se compară cu metoda manuală)")
    st.dataframe(s, width="stretch")
    st.markdown("**Activitate pe zile**")
    st.bar_chart(j.groupby("zi").size(), color="#1F2A4D")
    st.dataframe(j.drop(columns=["zi"]).head(200), hide_index=True, width="stretch", height=280)
    st.download_button("⬇️ Exportă jurnalul (CSV)", j.drop(columns=["zi"]).to_csv(index=False).encode("utf-8"),
                       "jurnal_urbis.csv", "text/csv")


# ================================================================ WP0 · DESPRE
def page_despre():
    header("Despre proiect", "WP0")
    st.markdown("""
**Dezvoltarea arhitecturii GIS pentru management cadastral și analiză urbană sustenabilă**

| | |
|---|---|
| Contract | 29CI/2026 |
| Cod proiect | PN-IV-P7-7.1-CI-2025-0106 |
| Program | PNCDI IV, Programul 5.7 Parteneriat pentru inovare, Subprogramul 5.7.1 – Cec de inovare |
| Finanțator | UEFISCDI |
| Furnizor | Universitatea Tehnică „Gheorghe Asachi” din Iași (TUIASI) |
| Beneficiar | HURJUI & ASOCIAȚII S.R.L. |
| Perioada | 15.07.2026 – 31.12.2026 |
| Categorie | A3 – Dezvoltare experimentală (prototip funcțional) |
""")
    st.markdown("#### Unde se vede fiecare livrabil")
    rows = [("WP1", "Model conceptual al bazei de date spațio-temporale", "Model de date (M1)", page_model),
            ("WP2", "Flux de lucru pentru integrarea datelor multisursă", "Import și validare (M2)", page_import),
            ("WP3", "Arhitectură GIS-IA cu RAG", "Asistent GIS-IA (M5)", page_asistent),
            ("WP4", "Implementare și validare pe Iași", "Hartă (M3), Interogare (M4), Indicatori și rapoarte (M6), "
                                                       "Mapathon (M7)", page_harta),
            ("WP0", "Management, diseminare, raport de impact", "Jurnal (M8), această pagină", page_jurnal)]
    st.dataframe(pd.DataFrame([r[:3] for r in rows], columns=["WP", "Livrabil", "În aplicație"]),
                 hide_index=True, width="stretch")
    st.markdown("""
#### Surse de date
- **FLAIR-HUB2BF** – documentația tehnică a fluxului de extragere a clădirilor și straturile RO-IAS (importate de operator).
- **OpenStreetMap** – construcții, tip, niveluri (© contribuitorii OSM, ODbL).
- **Documente încărcate** – normative MDLPA, PUG/PUZ, extrase CF, indexate pentru asistent.
- **Estimarea nivelurilor** – model învățat din construcțiile cu niveluri cunoscute; precizia e afișată pe hartă.

#### Limitări
Prototip de cercetare. Rapoartele sunt informative și nu înlocuiesc certificatul de urbanism, extrasul CF
sau măsurătorile topografice. Datele personale ale titularilor nu sunt stocate (doar tip și cotă-parte).

_Acest proiect a fost finanțat printr-un grant al Ministerului Educației și Cercetării, CCCDI – UEFISCDI, cod proiect PN-IV-P7-7.1-CI-2025-0106 (contract 29CI/2026), în cadrul PNCDI IV. Conținutul nu reprezintă poziția oficială a finanțatorului._
""")


# ================================================================ navigare

# ================================================================ VERSIUNE DEMONSTRATIVĂ (online)
DEMO = str(os.environ.get("URBIS_DEMO") or _secret("URBIS_DEMO") or "").strip().lower() in ("1", "true", "da", "yes")
DEMO_AREA = "Municipiul Iași (complet)"
DEMO_START = "Centru – Copou"   # se încarcă prima; restul municipiului vine în fundal


@st.cache_resource(show_spinner=False)
def demo_progres():
    """Starea încărcării în fundal (păstrată între rulările paginii)."""
    return {"total": 0, "gata": 0, "cladiri": 0, "erori": [], "terminat": False, "lipsa": 0}


def _demo_import_tile(bounds, sid):
    g = osm.fetch_buildings(box(*bounds))
    if g.empty:
        return 0
    g = g.rename(columns={"an": "an_constructie"})
    g["niveluri"] = g.apply(raport._levels, axis=1)
    g["niveluri_sursa"] = np.where(g["niveluri"].notna(), "OSM", None)
    return db.import_buildings(g, 2024, sid, utilizator="demo", inventar=True)["acceptate"]


def _demo_rest(tiles, sid, prog):
    """Descarcă restul municipiului pe bucăți (câte 2 în paralel) și le importă pe rând."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    rest = list(tiles)
    for _ in range(2):                               # a doua trecere reia bucățile cu eroare
        esuate = []
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut = {ex.submit(osm.fetch_buildings, box(*t)): t for t in rest}
            for f in as_completed(fut):
                try:
                    g = f.result()
                    if len(g):
                        g = g.rename(columns={"an": "an_constructie"})
                        g["niveluri"] = g.apply(raport._levels, axis=1)
                        g["niveluri_sursa"] = np.where(g["niveluri"].notna(), "OSM", None)
                        prog["cladiri"] += db.import_buildings(g, 2024, sid, utilizator="demo",
                                                               inventar=True)["acceptate"]
                    prog["gata"] += 1
                except Exception as e:  # serviciul OSM nu a răspuns pentru această bucată
                    esuate.append(fut[f])
                    prog["erori"].append(str(e)[:200])
        rest = esuate
        if not rest:
            break
    prog.update(terminat=True, lipsa=len(rest))
    db.log("demo: inventar OSM (fundal)", "sistem", {"cladiri": prog["cladiri"], "bucati_lipsa": len(rest)})


@st.cache_resource(show_spinner=False)
def demo_bootstrap():
    """În modul demo, la prima pornire încarcă automat inventarul OpenStreetMap al municipiului
    (date publice, ODbL): întâi zona centrală, ca platforma să pornească repede, apoi restul în fundal.
    Nu se încarcă niciun fel de date cadastrale ale beneficiarului."""
    import threading
    if db.years():
        surse = db.read_sql("SELECT nume FROM sursa")["nume"].tolist()
        if f"OpenStreetMap – {DEMO_AREA}" in surse:
            return "existent"
        # baza demo veche (altă zonă): o refacem de la zero; în demo nu există alte date
        db.DB_PATH.unlink(missing_ok=True)
        db.init()
    try:
        city = harta_inaltimi.AREAS[DEMO_AREA]
        s0 = harta_inaltimi.AREAS[DEMO_START]
        cx, cy = (s0[0] + s0[2]) / 2, (s0[1] + s0[3]) / 2
        tiles = sorted(osm._tiles(*city), key=lambda t: ((t[0] + t[2]) / 2 - cx) ** 2 + ((t[1] + t[3]) / 2 - cy) ** 2)
        sid = db.add_source(f"OpenStreetMap – {DEMO_AREA}", "OSM", 2024, "metri", "ODbL")
        db.set_zone(f"Zona pilot – {DEMO_AREA}", "pilot", box(*city))
        prog = demo_progres()
        prog.update(total=len(tiles), gata=0, cladiri=0, erori=[], terminat=False, lipsa=0)
        prog["cladiri"] = _demo_import_tile(tiles[0], sid)
        prog["gata"] = 1
        db.log("demo: inventar OSM (zona centrală)", "sistem", {"cladiri": prog["cladiri"]})
        threading.Thread(target=_demo_rest, args=(tiles[1:], sid, prog), daemon=True).start()
        return f"importate {prog['cladiri']}"
    except Exception as e:
        return f"eroare: {e}"


if DEMO:
    with st.spinner("Pregătesc versiunea demonstrativă (zona centrală a Iașiului, aproximativ un minut; "
                    "restul municipiului se încarcă apoi în fundal)..."):
        _demo_state = demo_bootstrap()
    with st.sidebar:
        st.info("**Versiune demonstrativă.** Date publice OpenStreetMap pentru tot municipiul Iași. "
                "Nu conține date cadastrale ale beneficiarului. Modificările nu se păstrează după repornire.")
        _prog = demo_progres()
        if _prog["total"] and not _prog["terminat"]:
            _n = f"{_prog['cladiri']:,}".replace(",", ".")
            st.caption(f"Se încarcă restul municipiului în fundal: {_prog['gata']}/{_prog['total']} bucăți, "
                       f"{_n} clădiri până acum. Reîmprospătează pagina peste câteva minute.")
        elif _prog["lipsa"]:
            st.caption(f"{_prog['lipsa']} din {_prog['total']} bucăți ale municipiului nu s-au putut descărca "
                       "din OpenStreetMap; repornirea aplicației le reîncearcă.")
        if _demo_state.startswith("eroare"):
            st.warning("OpenStreetMap nu a răspuns la pornire. Reîncarcă pagina peste un minut.")
            st.caption(_demo_state[:300])
            demo_bootstrap.clear()

current_role()
pages = {
    "Explorează": [st.Page(page_harta, title="Hartă și căutare · M3", icon="🗺️", default=True),
                   st.Page(harta_inaltimi.render, title="Harta înălțimilor (OSM) · M3", icon="🏢",
                           url_path="inaltimi"),
                   st.Page(page_swipe, title="Comparație cu swipe · M3", icon="↔️", url_path="swipe"),
                   st.Page(page_interogare, title="Interogare spațială · M4", icon="🔎", url_path="interogare"),
                   st.Page(page_asistent, title="Asistent GIS-IA · M5", icon="🤖", url_path="asistent"),
                   st.Page(page_cautare_web, title="Agent documentare web · M5", icon="🌐",
                           url_path="cautare-web")],
    "Analizează": [st.Page(page_indicatori, title="Indicatori densificare · M6", icon="📈", url_path="indicatori"),
                   st.Page(page_rapoarte, title="Rapoarte PDF · M6", icon="📄", url_path="rapoarte"),
                   st.Page(page_neconcordante, title="Neconcordanțe cadastrale · M6", icon="⚠️",
                           url_path="neconcordante")],
    "Date": [st.Page(page_import, title="Import și validare · M2", icon="📥", url_path="import"),
             st.Page(page_model, title="Model de date · M1", icon="🧩", url_path="model"),
             st.Page(PR.page_straturi, title="Straturi de lucru", icon="🗂️", url_path="straturi"),
             st.Page(page_mapathon, title="Mapathon · M7", icon="✅", url_path="mapathon")],
    "Administrare": [st.Page(page_jurnal, title="Jurnal și statistici · M8", icon="📊", url_path="jurnal"),
                     st.Page(page_impact, title="Valori de referință impact · M8", icon="🎯", url_path="impact"),
                     st.Page(page_despre, title="Despre proiect · WP0", icon="ℹ️", url_path="despre")],
}
pg = st.navigation(pages)
if st.session_state.get("_last_page") != pg.title:
    st.session_state._last_page = pg.title
    log("deschidere pagină", {"pagina": pg.title})
pg.run()
