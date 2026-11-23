from pathlib import Path

import altair as alt
import geopandas as gpd
import numpy as np
import pandas as pd
import streamlit as st

import db

CARTIERE_FILE = Path(__file__).parent / "docs" / "cartiere.geojson"
TOT = "Tot municipiul"
AFARA = "în afara cartierelor"
CLASE_NIV = [("P", 1, 1), ("P+1…P+2", 2, 3), ("P+3…P+4", 4, 5), ("P+5…P+8", 6, 9), ("P+9 și peste", 10, 999)]
NAVY = "#0d2a4f"
GRI = "#9aa8ba"
VERDE = "#22a55b"
ROSU = "#e0453f"
TIPURI = {"house": "casă", "detached": "casă", "semidetached_house": "casă", "residential": "locuințe",
          "apartments": "bloc de locuințe", "yes": "nespecificat", "garage": "garaj", "garages": "garaje",
          "shed": "anexă", "hut": "anexă", "commercial": "comercial", "retail": "comerț", "office": "birouri",
          "industrial": "industrial", "warehouse": "depozit", "school": "școală", "kindergarten": "grădiniță",
          "university": "universitate", "college": "învățământ", "hospital": "spital", "church": "lăcaș de cult",
          "chapel": "lăcaș de cult", "monastery": "lăcaș de cult", "public": "public", "civic": "public",
          "government": "public", "hotel": "hotel", "roof": "copertină", "service": "servicii tehnice",
          "construction": "în construcție", "greenhouse": "seră", "farm_auxiliary": "anexă agricolă",
          "sports_hall": "sală de sport", "transportation": "transport", "train_station": "transport"}


@st.cache_data(show_spinner=False)
def cartiere():
    if not CARTIERE_FILE.exists():
        return gpd.GeoDataFrame({"nume": []}, geometry=[], crs=db.S70)
    c = gpd.read_file(CARTIERE_FILE)
    if c.crs is None:
        c = c.set_crs(4326)
    return c[["nume", "geometry"]].to_crs(db.S70)


def _cartier(g):
    c = cartiere()
    if g.empty or c.empty:
        return pd.Series(AFARA, index=g.index)
    pts = gpd.GeoDataFrame(geometry=g.geometry.representative_point(), crs=g.crs).to_crs(db.S70)
    j = gpd.sjoin(pts, c, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    return j["nume"].reindex(g.index).fillna(AFARA)


def _clasa(n):
    if pd.isna(n) or n <= 0:
        return None
    for lab, a, b in CLASE_NIV:
        if a <= n <= b:
            return lab
    return CLASE_NIV[-1][0]


def _an(v):
    try:
        s = str(v).strip()
        y = int(s[:4]) if s[:4].isdigit() else None
    except Exception:
        y = None
    return y if y and 1700 <= y <= 2100 else None


@st.cache_data(show_spinner="Pregătesc graficele...")
def date_an(an, _ver, _state_at):
    g, _ = _state_at(an, _ver)
    if g.empty:
        return pd.DataFrame()
    df = pd.DataFrame({
        "cartier": _cartier(g),
        "tip": g["tip"].fillna("nespecificat").astype(str).str.strip().replace({"": "nespecificat", "None": "nespecificat"})
                       .map(lambda t: TIPURI.get(t.lower(), t)),
        "arie": pd.to_numeric(g["amprenta_m2"], errors="coerce").fillna(g.geometry.area),
        "niv": pd.to_numeric(g["niv_afis"], errors="coerce"),
        "sursa_niv": np.where(g["niv_sursa_afis"].astype(str) == "estimat", "estimat", "din date"),
        "an_c": g["an_constructie"].map(_an) if "an_constructie" in g else None,
    })
    df["desf"] = df["arie"] * df["niv"].fillna(1)
    df["clasa"] = df["niv"].map(_clasa)
    return df


@st.cache_data(show_spinner="Compar anii...")
def schimbari(y1, y2, _ver, _compare):
    c = _compare(y1, y2, _ver)
    if c.empty:
        return pd.DataFrame()
    return pd.DataFrame({"cartier": _cartier(c), "schimbare": c["schimbare"].values,
                         "arie": pd.to_numeric(c["amprenta_m2"], errors="coerce").fillna(0).values})


def _ha(v):
    return f"{v / 10000:,.1f} ha".replace(",", " ").replace(".", ",")


def _n(v):
    return f"{int(v):,}".replace(",", ".")


def _sel_cartier(ev, nume_camp="cartier"):
    try:
        pts = ev.selection.get("sel") or []
        if isinstance(pts, dict):
            pts = [pts]
        for p in pts:
            v = p.get(nume_camp)
            if isinstance(v, list):
                v = v[0] if v else None
            if v:
                return v
    except Exception:
        return None
    return None


def _altair(chart, key):
    try:
        return st.altair_chart(chart, width="stretch", on_select="rerun", key=key)
    except TypeError:
        st.altair_chart(chart, use_container_width=True)
        return None


def render(header, db_version, state_at, compare_years):
    header("Tablou de bord", "M6", "Grafice care se actualizează după anul și zona alese; "
                                   "apasă pe un cartier din graficul pe cartiere ca să-l selectezi")
    ys = db.years()
    if not ys:
        st.info("Graficele apar după ce în baza de date există cel puțin un inventar de construcții "
                "(vezi Hartă și căutare sau Import și validare).")
        return
    ver = db_version()
    nume = [TOT] + sorted(cartiere()["nume"].tolist(), key=str.casefold) + [AFARA]
    if st.session_state.get("tb_zona") not in nume:
        st.session_state.tb_zona = TOT
    pend = st.session_state.pop("tb_zona_noua", None)
    if pend in nume:
        st.session_state.tb_zona = pend
    c1, c2, c3 = st.columns([1, 1.4, 1.6])
    an = c1.selectbox("Anul", ys, index=len(ys) - 1, key="tb_an")
    zona = c2.selectbox("Zona", nume, key="tb_zona")
    df = date_an(an, ver, state_at)
    if df.empty:
        st.info(f"Nu există construcții valabile în {an}.")
        return
    z = df if zona == TOT else df[df["cartier"] == zona]
    c3.caption(f"{_n(len(z))} construcții în {zona.lower() if zona == TOT else zona}, anul {an}. "
               f"Nivelurile lipsă sunt estimate de platformă și marcate ca atare.")

    k = st.columns(4)
    k[0].metric("Construcții", _n(len(z)))
    k[1].metric("Amprentă la sol", _ha(z["arie"].sum()))
    k[2].metric("Suprafață desfășurată estimată", _ha(z["desf"].sum()))
    k[3].metric("Niveluri în medie", f"{z['niv'].mean():.1f}".replace(".", ",") if z["niv"].notna().any() else "–")

    g1, g2 = st.columns(2)
    with g1:
        st.markdown("##### Regimul de înălțime")
        h = z.dropna(subset=["clasa"]).groupby(["clasa", "sursa_niv"]).size().reset_index(name="n")
        if h.empty:
            st.caption("Nu există informații despre niveluri.")
        else:
            ordine = [c[0] for c in CLASE_NIV]
            st.altair_chart(alt.Chart(h).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                x=alt.X("clasa:N", sort=ordine, title=None, axis=alt.Axis(labelAngle=0, labelOverlap=False, labelFontSize=10)),
                y=alt.Y("n:Q", title="construcții"),
                color=alt.Color("sursa_niv:N", title="Niveluri", scale=alt.Scale(domain=["din date", "estimat"], range=[NAVY, GRI])),
                tooltip=[alt.Tooltip("clasa:N", title="Regim"), alt.Tooltip("sursa_niv:N", title="Niveluri"),
                         alt.Tooltip("n:Q", title="Construcții", format=",")]).properties(height=280), width="stretch")
    with g2:
        st.markdown("##### Tipuri de construcții")
        t = z.groupby("tip").agg(n=("arie", "size"), arie=("arie", "sum")).sort_values("n", ascending=False)
        if len(t) > 8:
            t = pd.concat([t.iloc[:7], pd.DataFrame({"n": [t["n"].iloc[7:].sum()], "arie": [t["arie"].iloc[7:].sum()]},
                                                    index=["altele"])])
        t = t.reset_index(names="tip")
        t["ha"] = t["arie"] / 10000
        st.altair_chart(alt.Chart(t).mark_arc(innerRadius=60, stroke="#fff").encode(
            theta=alt.Theta("n:Q"), color=alt.Color("tip:N", title="Tip", sort=t["tip"].tolist(),
                                                    scale=alt.Scale(scheme="tableau10")),
            tooltip=[alt.Tooltip("tip:N", title="Tip"), alt.Tooltip("n:Q", title="Construcții", format=","),
                     alt.Tooltip("ha:Q", title="Amprentă (ha)", format=".1f")]).properties(height=280), width="stretch")

    st.markdown("##### Construcții pe cartiere")
    pc = df[df["cartier"] != AFARA].groupby("cartier").agg(n=("arie", "size"), arie=("arie", "sum"),
                                                             desf=("desf", "sum")).reset_index()
    pc["ha"] = pc["arie"] / 10000
    pc["ha_desf"] = pc["desf"] / 10000
    pc["ales"] = np.where((pc["cartier"] == zona) | (zona == TOT), "da", "nu")
    sel = alt.selection_point(fields=["cartier"], name="sel")
    ch = alt.Chart(pc).mark_bar(cornerRadiusTopRight=3, cornerRadiusBottomRight=3).encode(
        y=alt.Y("cartier:N", sort="-x", title=None, axis=alt.Axis(labelOverlap=False, labelLimit=200)),
        x=alt.X("ha:Q", title="amprentă la sol (ha)"),
        color=alt.Color("ales:N", scale=alt.Scale(domain=["da", "nu"], range=[NAVY, "#c9d3df"]), legend=None),
        tooltip=[alt.Tooltip("cartier:N", title="Cartier"), alt.Tooltip("n:Q", title="Construcții", format=","),
                 alt.Tooltip("ha:Q", title="Amprentă (ha)", format=".1f"),
                 alt.Tooltip("ha_desf:Q", title="Desfășurată estimată (ha)", format=".1f")]
    ).add_params(sel).properties(height=max(260, 22 * len(pc)))
    ev = _altair(ch, "tb_cart") if len(pc) else None
    n_afara = int((df["cartier"] == AFARA).sum())
    if n_afara:
        st.caption(f"{_n(n_afara)} construcții sunt în afara limitelor celor {len(cartiere())} cartiere și nu apar în grafic.")
    ales = _sel_cartier(ev) if ev is not None else None
    if ales and ales != zona:
        st.session_state.tb_zona_noua = ales
        st.rerun()

    ani = z["an_c"].dropna() if "an_c" in z else pd.Series(dtype=float)
    if len(ani) >= max(20, 0.03 * len(z)):
        st.markdown("##### Perioada de construire (unde este cunoscută)")
        dec = (ani // 10 * 10).astype(int).value_counts().sort_index().reset_index()
        dec.columns = ["deceniu", "n"]
        dec["eticheta"] = dec["deceniu"].astype(str) + "–" + (dec["deceniu"] + 9).astype(str)
        st.altair_chart(alt.Chart(dec).mark_bar(color=NAVY, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
            x=alt.X("eticheta:N", sort=dec["eticheta"].tolist(), title=None, axis=alt.Axis(labelAngle=-40)),
            y=alt.Y("n:Q", title="construcții"),
            tooltip=[alt.Tooltip("eticheta:N", title="Deceniu"), alt.Tooltip("n:Q", title="Construcții", format=",")]
        ).properties(height=260), width="stretch")
        st.caption(f"Anul construirii este cunoscut pentru {_n(len(ani))} din {_n(len(z))} construcții.")

    if len(ys) > 1:
        st.markdown("##### Construcții noi și demolate pe cartiere")
        a1, a2 = st.columns(2)
        y1 = a1.selectbox("De la anul", ys[:-1], index=0, key="tb_y1")
        y2 = a2.selectbox("Până la anul", [y for y in ys if y > y1], index=len([y for y in ys if y > y1]) - 1, key="tb_y2")
        s = schimbari(y1, y2, ver, compare_years)
        if s.empty:
            st.caption("Nu există schimbări între anii aleși.")
        else:
            s = s[s["schimbare"].isin(["nouă", "demolată"])]
            agg = s.groupby(["cartier", "schimbare"]).size().reset_index(name="n")
            agg["val"] = np.where(agg["schimbare"] == "demolată", -agg["n"], agg["n"])
            st.altair_chart(alt.Chart(agg).mark_bar().encode(
                y=alt.Y("cartier:N", title=None, sort=alt.EncodingSortField(field="val", op="sum", order="descending"),
                        axis=alt.Axis(labelOverlap=False, labelLimit=200)),
                x=alt.X("val:Q", title="← demolate · noi →", axis=alt.Axis(format="~s")),
                color=alt.Color("schimbare:N", title=None, scale=alt.Scale(domain=["nouă", "demolată"], range=[VERDE, ROSU])),
                tooltip=[alt.Tooltip("cartier:N", title="Cartier"), alt.Tooltip("schimbare:N", title="Schimbare"),
                         alt.Tooltip("n:Q", title="Construcții", format=",")]
            ).properties(height=max(240, 22 * agg["cartier"].nunique())), width="stretch")
    st.caption("Cartierele: limitele folosite și în site-ul public Iași 2011 → 2024. "
               "Suprafața desfășurată este estimată ca amprentă × număr de niveluri.")
