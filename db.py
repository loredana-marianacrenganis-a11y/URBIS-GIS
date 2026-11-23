
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely import wkt
from shapely.strtree import STRtree

S70 = 3844
DB_PATH = Path(__file__).with_name("urbis.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sursa (
  id INTEGER PRIMARY KEY, nume TEXT NOT NULL, tip TEXT NOT NULL,
  data_import TEXT NOT NULL, an_referinta INTEGER, precizie TEXT, licenta TEXT, detalii TEXT);
CREATE TABLE IF NOT EXISTS zona (
  id INTEGER PRIMARY KEY, nume TEXT NOT NULL, tip TEXT NOT NULL,
  cod_siruta TEXT, geom_wkt TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imobil (
  id INTEGER PRIMARY KEY, cod_imobil TEXT NOT NULL, nr_cadastral TEXT, nr_cf TEXT,
  suprafata_acte REAL, suprafata_masurata REAL, categorie_folosinta TEXT, intravilan INTEGER,
  geom_wkt TEXT NOT NULL, minx REAL, miny REAL, maxx REAL, maxy REAL,
  valid_de_la INTEGER NOT NULL, valid_pana_la INTEGER, sursa_id INTEGER REFERENCES sursa(id));
CREATE TABLE IF NOT EXISTS titular (
  id INTEGER PRIMARY KEY, cod_imobil TEXT NOT NULL, tip TEXT, cota_parte TEXT);
CREATE TABLE IF NOT EXISTS constructie (
  id INTEGER PRIMARY KEY, cod_constructie TEXT NOT NULL, cod_imobil TEXT,
  tip TEXT, niveluri INTEGER, niveluri_sursa TEXT, an_constructie INTEGER,
  adresa TEXT, nume TEXT, osm_id TEXT, amprenta_m2 REAL,
  geom_wkt TEXT NOT NULL, minx REAL, miny REAL, maxx REAL, maxy REAL,
  valid_de_la INTEGER NOT NULL, valid_pana_la INTEGER, sursa_id INTEGER REFERENCES sursa(id));
CREATE INDEX IF NOT EXISTS ix_constr_cod ON constructie(cod_constructie);
CREATE INDEX IF NOT EXISTS ix_constr_bbox ON constructie(minx, maxx, miny, maxy);
CREATE TABLE IF NOT EXISTS document (
  id INTEGER PRIMARY KEY, cod_imobil TEXT, cod_constructie TEXT, tip TEXT, titlu TEXT,
  data TEXT, fisier TEXT, text TEXT, date_personale INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS lucrare (
  id INTEGER PRIMARY KEY, cod_imobil TEXT, tip TEXT, client TEXT, data TEXT, stare TEXT,
  operator TEXT);
CREATE TABLE IF NOT EXISTS eveniment (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, entitate TEXT NOT NULL, cod TEXT NOT NULL,
  tip TEXT NOT NULL, an INTEGER, sursa_id INTEGER REFERENCES sursa(id), detalii TEXT,
  utilizator TEXT);
CREATE INDEX IF NOT EXISTS ix_ev_cod ON eveniment(cod);
CREATE TABLE IF NOT EXISTS observatie (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, cod_constructie TEXT, autor TEXT,
  niveluri_propuse INTEGER, tip_propus TEXT, exista INTEGER, comentariu TEXT,
  stare TEXT NOT NULL DEFAULT 'nouă', lat REAL, lon REAL, rezolvat_de TEXT, rezolvat_la TEXT);
CREATE TABLE IF NOT EXISTS indicator (
  id INTEGER PRIMARY KEY, zona TEXT, tip TEXT, perioada TEXT, valoare REAL, calculat_la TEXT);
CREATE TABLE IF NOT EXISTS referinta (
  id INTEGER PRIMARY KEY, an INTEGER NOT NULL, indicator TEXT NOT NULL, valoare REAL, unitate TEXT,
  observatii TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS jurnal (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, actiune TEXT NOT NULL, rol TEXT,
  durata_ms INTEGER, detalii TEXT);
"""

ENTITIES = {
    "zona": ("Zonă / UAT", ["id PK", "nume", "tip (pilot, UTR, cartier)", "cod_siruta", "geom (poligon)"]),
    "imobil": ("Imobil (parcelă)", ["id PK", "cod_imobil", "nr_cadastral", "nr_cf", "suprafata_acte / masurata",
                                     "categorie_folosinta", "intravilan", "geom (poligon)",
                                     "valid_de_la, valid_pana_la", "sursa_id FK"]),
    "constructie": ("Construcție (versionată)", ["id PK", "cod_constructie", "cod_imobil FK", "tip", "niveluri",
                                                 "niveluri_sursa", "an_constructie", "adresa", "amprenta_m2",
                                                 "geom (poligon)", "valid_de_la, valid_pana_la", "sursa_id FK"]),
    "titular": ("Titular (anonimizat)", ["id PK", "cod_imobil FK", "tip (fizic/juridic)", "cota_parte"]),
    "document": ("Document", ["id PK", "cod_imobil FK", "cod_constructie FK", "tip (CF, PUZ, AC…)", "titlu", "data",
                              "fisier", "text (pentru căutare)", "date_personale (exclus din căutare)"]),
    "lucrare": ("Lucrare cadastrală", ["id PK", "cod_imobil FK", "tip", "client", "data", "stare", "operator"]),
    "eveniment": ("Eveniment / versiune", ["id PK", "ts", "entitate", "cod", "tip (creare, extindere, demolare…)",
                                           "an", "sursa_id FK", "detalii", "utilizator"]),
    "sursa": ("Sursă de date", ["id PK", "nume", "tip (OSM, fișier, ortofoto…)", "data_import", "an_referinta",
                                "precizie", "licenta"]),
    "observatie": ("Observație de validare", ["id PK", "ts", "cod_constructie FK", "autor", "niveluri_propuse",
                                              "tip_propus", "exista", "stare", "geom (punct)"]),
    "indicator": ("Indicator calculat", ["id PK", "zona FK", "tip (POT, CUT, densitate)", "perioada", "valoare"]),
    "jurnal": ("Jurnal de utilizare", ["id PK", "ts", "actiune", "rol", "durata_ms", "detalii"]),
    "referinta": ("Valoare de referință (impact)", ["id PK", "an", "indicator", "valoare", "unitate", "observatii"]),
}
RELATIONS = [("zona", "imobil", "conține"), ("imobil", "constructie", "are"), ("imobil", "titular", "are"),
             ("imobil", "document", "are"), ("constructie", "document", "are"), ("imobil", "lucrare", "are"),
             ("constructie", "eveniment", "istoric"), ("imobil", "eveniment", "istoric"),
             ("sursa", "constructie", "proveniență"), ("sursa", "imobil", "proveniență"),
             ("sursa", "eveniment", "proveniență"), ("constructie", "observatie", "validată prin"),
             ("zona", "indicator", "agregă")]


def now():
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def conn(path=None):
    c = sqlite3.connect(str(path or DB_PATH), timeout=30)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init(path=None):
    with conn(path) as c:
        c.executescript(SCHEMA)
        cols = [r[1] for r in c.execute("PRAGMA table_info(document)")]
        if "date_personale" not in cols:
            c.execute("ALTER TABLE document ADD COLUMN date_personale INTEGER DEFAULT 0")


def erd_dot():
    lines = ['digraph ERD { rankdir=LR; graph [fontname="Helvetica", nodesep=0.4];',
             'node [shape=plaintext, fontname="Helvetica", fontsize=10]; edge [fontname="Helvetica", fontsize=9, color="#5B6680"];']
    for key, (title, fields) in ENTITIES.items():
        rows = "".join(f'<tr><td align="left">{f}</td></tr>' for f in fields)
        color = "#E36414" if key in ("constructie", "eveniment") else "#1F2A4D"
        lines.append(f'{key} [label=<<table border="1" cellborder="0" cellspacing="0" cellpadding="3" color="{color}">'
                     f'<tr><td bgcolor="{color}"><font color="white"><b>{title}</b></font></td></tr>{rows}</table>>];')
    for a, b, lbl in RELATIONS:
        lines.append(f'{a} -> {b} [label="{lbl}", arrowhead=crow];')
    lines.append("}")
    return "\n".join(lines)


def log(actiune, rol="", detalii="", durata_ms=None, path=None):
    with conn(path) as c:
        c.execute("INSERT INTO jurnal(ts, actiune, rol, durata_ms, detalii) VALUES (?,?,?,?,?)",
                  (now(), actiune, rol, durata_ms, detalii if isinstance(detalii, str) else json.dumps(detalii, ensure_ascii=False)))


def read_sql(q, params=(), path=None):
    with conn(path) as c:
        return pd.read_sql_query(q, c, params=params)


def add_source(nume, tip, an=None, precizie=None, licenta=None, detalii=None, path=None):
    with conn(path) as c:
        cur = c.execute("INSERT INTO sursa(nume, tip, data_import, an_referinta, precizie, licenta, detalii) "
                        "VALUES (?,?,?,?,?,?,?)", (nume, tip, now(), an, precizie, licenta, detalii))
        return cur.lastrowid


def set_zone(nume, tip, geom_wgs84, path=None):
    g = gpd.GeoSeries([geom_wgs84], crs=4326).to_crs(S70).iloc[0]
    with conn(path) as c:
        c.execute("DELETE FROM zona WHERE nume=? AND tip=?", (nume, tip))
        c.execute("INSERT INTO zona(nume, tip, geom_wkt) VALUES (?,?,?)", (nume, tip, g.wkt))


def zones(path=None):
    df = read_sql("SELECT * FROM zona", path=path)
    if df.empty:
        return gpd.GeoDataFrame(df, geometry=[], crs=S70)
    return gpd.GeoDataFrame(df, geometry=df["geom_wkt"].map(wkt.loads), crs=S70)


def _row_bbox(g):
    return g.bounds


def buildings_at(year, bbox=None, path=None):
    q = ("SELECT c.*, s.nume AS sursa FROM constructie c LEFT JOIN sursa s ON s.id=c.sursa_id "
         "WHERE c.valid_de_la<=? AND (c.valid_pana_la IS NULL OR c.valid_pana_la>?)")
    p = [year, year]
    if bbox is not None:
        q += " AND c.maxx>=? AND c.minx<=? AND c.maxy>=? AND c.miny<=?"
        p += [bbox[0], bbox[2], bbox[1], bbox[3]]
    df = read_sql(q, p, path=path)
    geom = df["geom_wkt"].map(wkt.loads) if len(df) else []
    return gpd.GeoDataFrame(df.drop(columns="geom_wkt"), geometry=list(geom), crs=S70)


def years(path=None):
    df = read_sql("SELECT DISTINCT valid_de_la AS an FROM constructie UNION "
                  "SELECT DISTINCT valid_pana_la FROM constructie WHERE valid_pana_la IS NOT NULL", path=path)
    return sorted(int(a) for a in df["an"].dropna())


def history(cod, path=None):
    v = read_sql("SELECT c.valid_de_la, c.valid_pana_la, c.niveluri, c.niveluri_sursa, c.amprenta_m2, c.tip, "
                 "s.nume AS sursa FROM constructie c LEFT JOIN sursa s ON s.id=c.sursa_id "
                 "WHERE cod_constructie=? ORDER BY valid_de_la", (cod,), path=path)
    e = read_sql("SELECT e.ts, e.an, e.tip, e.detalii, e.utilizator, s.nume AS sursa FROM eveniment e "
                 "LEFT JOIN sursa s ON s.id=e.sursa_id WHERE cod=? ORDER BY e.an, e.ts", (cod,), path=path)
    return v, e


def compare(y1, y2, bbox=None, path=None):
    a = buildings_at(y1, bbox, path)
    b = buildings_at(y2, bbox, path)
    ida = dict(zip(a["cod_constructie"], a["id"]))
    idb = dict(zip(b["cod_constructie"], b["id"]))
    rows = []
    for _, r in b.iterrows():
        cod = r["cod_constructie"]
        if cod not in ida:
            st = "nouă"
        elif ida[cod] != r["id"]:
            old = a.loc[a["cod_constructie"] == cod].iloc[0]
            st = "extinsă" if r["amprenta_m2"] > 1.15 * old["amprenta_m2"] else "modificată"
        else:
            st = "neschimbată"
        rows.append((cod, st, r.geometry, r["amprenta_m2"], r["niveluri"], r["tip"]))
    for _, r in a.iterrows():
        if r["cod_constructie"] not in idb:
            rows.append((r["cod_constructie"], "demolată", r.geometry, r["amprenta_m2"], r["niveluri"], r["tip"]))
    out = gpd.GeoDataFrame(rows, columns=["cod_constructie", "schimbare", "geometry", "amprenta_m2",
                                          "niveluri", "tip"], geometry="geometry", crs=S70)
    out["tip_schimbare"] = change_types(out, a)
    return out


CHANGE_LABELS = {"neschimbată": "stabilitate", "extinsă": "extindere", "modificată": "modificare"}


def _significant(g1, g2, min_area, min_frac):
    inter = g1.intersection(g2).area
    return inter >= min_area and inter >= min_frac * min(g1.area, g2.area)


def change_types(cmp, before=None, min_area=5.0, min_frac=0.1):
    res = pd.Series([CHANGE_LABELS.get(s, s) for s in cmp["schimbare"]], index=cmp.index, dtype=object)
    old = cmp[cmp["schimbare"] == "demolată"]
    new = cmp[cmp["schimbare"] == "nouă"]
    res[old.index] = "demolare"
    res[new.index] = "construcție"
    if old.empty or new.empty:
        return res
    tree = STRtree(old.geometry.values)
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, g in zip(new.index, new.geometry.values):
        for j in tree.query(g, predicate="intersects"):
            og = old.geometry.values[j]
            inter = g.intersection(og).area
            if inter >= min_area and inter >= min_frac * min(g.area, og.area):
                parent[find(("n", i))] = find(("o", old.index[j]))
    comps = {}
    for node in list(parent):
        comps.setdefault(find(node), []).append(node)
    for nodes in comps.values():
        no = sum(1 for k, _ in nodes if k == "o")
        nn = sum(1 for k, _ in nodes if k == "n")
        if no == 0 or nn == 0:
            continue
        lbl = ("înlocuire" if no == 1 and nn == 1 else "divizare" if no == 1 else
               "fuziune" if nn == 1 else "recompunere")
        for _, idx in nodes:
            res[idx] = lbl
    keep = cmp[cmp["schimbare"].isin(["extinsă", "modificată", "neschimbată"])]
    if len(keep):
        kt = STRtree(keep.geometry.values)
        for i, g in zip(old.index, old.geometry.values):
            if res[i] != "demolare":
                continue
            hits = [keep.index[j] for j in kt.query(g, predicate="intersects")
                    if _significant(g, keep.geometry.values[j], min_area, min_frac)]
            if hits:
                res[i] = "fuziune"
                res[hits] = "fuziune"
        if before is not None and len(before):
            prev = before.set_index("cod_constructie").geometry
            kprev = [(idx, prev[c]) for idx, c in zip(keep.index, keep["cod_constructie"]) if c in prev.index]
            if kprev:
                pt = STRtree([g for _, g in kprev])
                for i, g in zip(new.index, new.geometry.values):
                    if res[i] != "construcție":
                        continue
                    hits = [kprev[j][0] for j in pt.query(g, predicate="intersects")
                            if _significant(g, kprev[j][1], min_area, min_frac)]
                    if hits:
                        res[i] = "divizare"
                        res[hits] = "divizare"
    return res


def _insert_version(c, cod, rec, geom, an, sursa_id, cod_imobil=None):
    minx, miny, maxx, maxy = geom.bounds
    c.execute("INSERT INTO constructie(cod_constructie, cod_imobil, tip, niveluri, niveluri_sursa, an_constructie, "
              "adresa, nume, osm_id, amprenta_m2, geom_wkt, minx, miny, maxx, maxy, valid_de_la, sursa_id) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (cod, cod_imobil, rec.get("tip"), rec.get("niveluri"), rec.get("niveluri_sursa"),
               rec.get("an_constructie"), rec.get("adresa"), rec.get("nume"), rec.get("osm_id"),
               round(geom.area, 1), geom.wkt, minx, miny, maxx, maxy, an, sursa_id))


def _event(c, entitate, cod, tip, an, sursa_id, detalii="", utilizator=""):
    c.execute("INSERT INTO eveniment(ts, entitate, cod, tip, an, sursa_id, detalii, utilizator) VALUES (?,?,?,?,?,?,?,?)",
              (now(), entitate, cod, tip, an, sursa_id, detalii, utilizator))


def _clean(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or v == "" or str(v) in ("None", "nan"):
        return None
    return v


def validate(gdf):
    rep = {"primite": len(gdf)}
    g = gdf.to_crs(S70).copy()
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    g = g[g.geom_type.isin(["Polygon", "MultiPolygon"])]
    rep["fara_poligon"] = rep["primite"] - len(g)
    bad = ~g.geometry.is_valid
    rep["reparate"] = int(bad.sum())
    g.loc[bad, "geometry"] = g.loc[bad, "geometry"].buffer(0)
    small = g.area < 5
    rep["prea_mici"] = int(small.sum())
    g = g[~small]
    wk = g.geometry.map(lambda x: shapely.normalize(x).wkb)
    dup = wk.duplicated()
    rep["duplicate"] = int(dup.sum())
    g = g[~dup].reset_index(drop=True)
    tree = STRtree(g.geometry.values)
    pairs = tree.query(g.geometry.values, predicate="intersects")
    n_ov = 0
    for i, j in zip(*pairs):
        if i < j:
            inter = g.geometry.iloc[i].intersection(g.geometry.iloc[j]).area
            if inter > 0.2 * min(g.geometry.iloc[i].area, g.geometry.iloc[j].area):
                n_ov += 1
    rep["suprapuneri"] = n_ov
    rep["acceptate"] = len(g)
    return g, rep


def import_buildings(gdf, an, sursa_id, field_map=None, complete=False, utilizator="", path=None, inventar=None):
    fm = field_map or {}
    g, rep = validate(gdf)
    ext = g.total_bounds if len(g) else None
    existing = buildings_at(an, ext, path) if ext is not None else buildings_at(an, path=path).iloc[0:0]
    if len(existing) and len(g):
        hull = shapely.box(*ext)
        existing = existing[existing.intersects(hull)]
    if inventar:
        existing = existing.iloc[0:0]
    first_inventory = existing.empty if inventar is None else bool(inventar)
    tree = STRtree(existing.geometry.values) if len(existing) else None
    matched_existing = set()
    counts = {"inventar": 0, "noi": 0, "extinse": 0, "modificate": 0, "neschimbate": 0, "demolate": 0}

    def val(row, key):
        col = fm.get(key, key)
        return _clean(row[col]) if col in row.index else None

    with conn(path) as c:
        for _, row in g.iterrows():
            geom = row.geometry
            niv = val(row, "niveluri")
            rec = {"tip": val(row, "tip") or "yes",
                   "niveluri": int(float(niv)) if niv is not None and str(niv).replace(".", "", 1).isdigit() else None,
                   "an_constructie": val(row, "an_constructie"), "adresa": val(row, "adresa"),
                   "nume": val(row, "nume"), "osm_id": val(row, "osm_id")}
            rec["niveluri_sursa"] = val(row, "niveluri_sursa") or ("fișier" if rec["niveluri"] else None)
            best, best_iou = None, 0.0
            if tree is not None:
                for j in tree.query(geom, predicate="intersects"):
                    eg = existing.geometry.iloc[j]
                    inter = geom.intersection(eg).area
                    iou = inter / (geom.union(eg).area or 1)
                    cover = inter / (eg.area or 1)
                    score = max(iou, 0.0 if cover < 0.8 else 0.5 + 0.5 * iou)
                    if score > best_iou:
                        best, best_iou = j, score
            if best is not None and best_iou >= 0.5:
                old = existing.iloc[best]
                matched_existing.add(old["cod_constructie"])
                grew = geom.area > 1.15 * old["amprenta_m2"]
                changed_lv = rec["niveluri"] is not None and rec["niveluri"] != _clean(old["niveluri"])
                if grew or changed_lv:
                    c.execute("UPDATE constructie SET valid_pana_la=? WHERE id=?", (an, int(old["id"])))
                    if rec["niveluri"] is None:
                        rec["niveluri"], rec["niveluri_sursa"] = _clean(old["niveluri"]), _clean(old["niveluri_sursa"])
                    _insert_version(c, old["cod_constructie"], rec, geom, an, sursa_id)
                    tip = "extindere" if grew else "modificare"
                    _event(c, "constructie", old["cod_constructie"], tip, an, sursa_id,
                           f"amprentă {old['amprenta_m2']:.0f} → {geom.area:.0f} m²" if grew else
                           f"niveluri {old['niveluri']} → {rec['niveluri']}", utilizator)
                    counts["extinse" if grew else "modificate"] += 1
                else:
                    counts["neschimbate"] += 1
            else:
                cod = "C-" + uuid.uuid4().hex[:10]
                _insert_version(c, cod, rec, geom, an, sursa_id)
                kind = "inventar" if first_inventory else "creare"
                _event(c, "constructie", cod, kind, an, sursa_id,
                       "inventar inițial" if first_inventory else "construcție nouă", utilizator)
                counts["inventar" if first_inventory else "noi"] += 1
        if complete and len(existing):
            for _, old in existing.iterrows():
                if old["cod_constructie"] not in matched_existing:
                    c.execute("UPDATE constructie SET valid_pana_la=? WHERE id=?", (an, int(old["id"])))
                    _event(c, "constructie", old["cod_constructie"], "demolare", an, sursa_id,
                           "absentă din importul complet", utilizator)
                    counts["demolate"] += 1
    rep.update(counts)
    return rep


def import_parcels(gdf, an, sursa_id, field_map=None, utilizator="", path=None):
    fm = field_map or {}
    g, rep = validate(gdf)
    n_new = n_upd = 0
    with conn(path) as c:
        for _, row in g.iterrows():
            nr = _clean(row.get(fm.get("nr_cadastral", "nr_cadastral")))
            cf = _clean(row.get(fm.get("nr_cf", "nr_cf")))
            sa = _clean(row.get(fm.get("suprafata_acte", "suprafata_acte")))
            try:
                sa = float(str(sa).replace(",", ".")) if sa is not None else None
            except ValueError:
                sa = None
            geom = row.geometry
            cod = str(nr) if nr else "I-" + uuid.uuid4().hex[:10]
            old = c.execute("SELECT id FROM imobil WHERE cod_imobil=? AND valid_pana_la IS NULL", (cod,)).fetchone()
            if old:
                c.execute("UPDATE imobil SET valid_pana_la=? WHERE id=?", (an, old[0]))
                n_upd += 1
                tip = "modificare"
            else:
                n_new += 1
                tip = "creare"
            minx, miny, maxx, maxy = geom.bounds
            c.execute("INSERT INTO imobil(cod_imobil, nr_cadastral, nr_cf, suprafata_acte, suprafata_masurata, geom_wkt, "
                      "minx, miny, maxx, maxy, valid_de_la, sursa_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cod, nr, cf, sa, round(geom.area, 1), geom.wkt, minx, miny, maxx, maxy, an, sursa_id))
            _event(c, "imobil", cod, tip, an, sursa_id, "", utilizator)
    rep.update({"imobile_noi": n_new, "imobile_actualizate": n_upd})
    return rep


def parcels_at(year, path=None):
    df = read_sql("SELECT * FROM imobil WHERE valid_de_la<=? AND (valid_pana_la IS NULL OR valid_pana_la>?)",
                  (year, year), path=path)
    geom = df["geom_wkt"].map(wkt.loads) if len(df) else []
    return gpd.GeoDataFrame(df.drop(columns="geom_wkt"), geometry=list(geom), crs=S70)


def find_parcel(nr, path=None):
    df = read_sql("SELECT * FROM imobil WHERE (nr_cadastral=? OR nr_cf=? OR cod_imobil=?) "
                  "AND valid_pana_la IS NULL", (nr, nr, nr), path=path)
    if df.empty:
        return None
    return gpd.GeoDataFrame(df.drop(columns="geom_wkt"), geometry=df["geom_wkt"].map(wkt.loads), crs=S70)


def add_observation(cod, autor, niveluri, tip, exista, comentariu, lat, lon, path=None):
    with conn(path) as c:
        c.execute("INSERT INTO observatie(ts, cod_constructie, autor, niveluri_propuse, tip_propus, exista, "
                  "comentariu, lat, lon) VALUES (?,?,?,?,?,?,?,?,?)",
                  (now(), cod, autor, niveluri, tip, int(exista), comentariu, lat, lon))


def resolve_observation(obs_id, accept, utilizator, an, path=None):
    with conn(path) as c:
        o = c.execute("SELECT cod_constructie, niveluri_propuse, tip_propus, exista FROM observatie WHERE id=?",
                      (obs_id,)).fetchone()
        c.execute("UPDATE observatie SET stare=?, rezolvat_de=?, rezolvat_la=? WHERE id=?",
                  ("acceptată" if accept else "respinsă", utilizator, now(), obs_id))
    if not accept or not o:
        return
    cod, niv, tip, exista = o
    sid = add_source("Mapathon – validare", "mapathon", an, path=path)
    cur = read_sql("SELECT * FROM constructie WHERE cod_constructie=? AND valid_pana_la IS NULL", (cod,), path=path)
    if cur.empty:
        return
    old = cur.iloc[0]
    with conn(path) as c:
        if not exista:
            c.execute("UPDATE constructie SET valid_pana_la=? WHERE id=?", (an, int(old["id"])))
            _event(c, "constructie", cod, "corectare", an, sid,
                   "validare mapathon: construcția nu există (eroare de date)", utilizator)
            return
        c.execute("UPDATE constructie SET niveluri=?, niveluri_sursa='mapathon', tip=COALESCE(?, tip) WHERE id=?",
                  (niv, tip, int(old["id"])))
        _event(c, "constructie", cod, "validare", an, sid,
               f"niveluri {_clean(old['niveluri'])} → {niv}" + (f", tip {tip}" if tip else ""), utilizator)


def add_document(titlu, tip, text, cod_imobil=None, cod_constructie=None, fisier=None, date_personale=False,
                 path=None):
    with conn(path) as c:
        c.execute("INSERT INTO document(cod_imobil, cod_constructie, tip, titlu, data, fisier, text, date_personale) "
                  "VALUES (?,?,?,?,?,?,?,?)", (cod_imobil, cod_constructie, tip, titlu, now()[:10], fisier, text,
                                               int(bool(date_personale))))


def db_summary(path=None):
    ys = years(path)
    if not ys:
        return "Baza de date URBIS nu conține încă nicio construcție importată."
    parts = [f"Baza de date URBIS: construcții pentru anii {', '.join(map(str, ys))}."]
    for y in ys:
        b = buildings_at(y, path=path)
        if len(b):
            known = b["niveluri"].notna()
            parts.append(f"În {y}: {len(b)} construcții, amprentă totală {b['amprenta_m2'].sum():.0f} m², "
                         f"niveluri cunoscute la {100 * known.mean():.0f}%"
                         + (f", nivel mediu {b.loc[known, 'niveluri'].mean():.1f}" if known.any() else "") + ".")
    if len(ys) >= 2:
        cmp = compare(ys[0], ys[-1], path=path)
        vc = cmp["schimbare"].value_counts().to_dict()
        parts.append(f"Între {ys[0]} și {ys[-1]}: " + ", ".join(f"{k}: {v}" for k, v in vc.items()) + ".")
    ev = read_sql("SELECT tip, COUNT(*) n FROM eveniment GROUP BY tip", path=path)
    if len(ev):
        parts.append("Evenimente înregistrate: " + ", ".join(f"{r.tip} {r.n}" for r in ev.itertuples()) + ".")
    ob = read_sql("SELECT stare, COUNT(*) n FROM observatie GROUP BY stare", path=path)
    if len(ob):
        parts.append("Observații mapathon: " + ", ".join(f"{r.stare} {r.n}" for r in ob.itertuples()) + ".")
    return " ".join(parts)


def documents(path=None):
    return read_sql("SELECT id, tip, titlu, data, cod_imobil, cod_constructie, "
                    "COALESCE(date_personale, 0) AS date_personale, text FROM document", path=path)


def documents_rag(path=None):
    d = documents(path)
    return d[d["date_personale"] == 0]


def inconsistencies(year, tol_pct=5.0, path=None):
    P = parcels_at(year, path)
    rows = []
    if P.empty:
        return gpd.GeoDataFrame(columns=["tip_neconcordanta", "cod", "detalii", "geometry"], geometry="geometry", crs=S70)
    for r in P.itertuples():
        sa, sm = _clean(r.suprafata_acte), _clean(r.suprafata_masurata)
        if sa and sm and abs(sm - sa) / sa * 100 > tol_pct:
            rows.append(("diferență suprafață acte / măsurată", r.cod_imobil,
                         f"acte {sa:.0f} m², măsurat {sm:.0f} m² ({(sm - sa) / sa * 100:+.1f}%)", r.geometry))
    tree = STRtree(P.geometry.values)
    for i, j in zip(*tree.query(P.geometry.values, predicate="intersects")):
        if i < j:
            inter = P.geometry.iloc[i].intersection(P.geometry.iloc[j])
            if inter.area > 1.0:
                rows.append(("suprapunere de imobile", f"{P.cod_imobil.iloc[i]} / {P.cod_imobil.iloc[j]}",
                             f"suprafață comună {inter.area:.1f} m²", inter))
    B = buildings_at(year, P.total_bounds, path)
    if len(B):
        hull = P.geometry.union_all().convex_hull if hasattr(P.geometry, "union_all") else P.unary_union.convex_hull
        B = B[B.geometry.representative_point().within(hull)]
        for b in B.itertuples():
            hits = tree.query(b.geometry, predicate="intersects")
            if len(hits) == 0:
                rows.append(("construcție fără imobil", b.cod_constructie, f"amprentă {b.amprenta_m2:.0f} m²", b.geometry))
                continue
            best = max(hits, key=lambda k: b.geometry.intersection(P.geometry.iloc[k]).area)
            outside = b.geometry.area - b.geometry.intersection(P.geometry.iloc[best]).area
            if outside > 2.0 and outside > 0.1 * b.geometry.area:
                rows.append(("construcție peste limita imobilului", b.cod_constructie,
                             f"{outside:.1f} m² ({outside / b.geometry.area * 100:.0f}%) în afara imobilului "
                             f"{P.cod_imobil.iloc[best]}", b.geometry))
    return gpd.GeoDataFrame(rows, columns=["tip_neconcordanta", "cod", "detalii", "geometry"],
                            geometry="geometry", crs=S70)


IMPACT_INDICATORS = [
    ("Timpul mediu pentru o lucrare cadastrală (colectare și integrare date)", "ore", "R2: −30%"),
    ("Numărul de lucrări realizate pe an", "lucrări", "R1: creștere"),
    ("Numărul de tipuri de servicii oferite (inclusiv analize PUG/PUZ, POT/CUT)", "servicii", "R3: servicii noi"),
    ("Ponderea lucrărilor livrate în format GIS", "%", "R5: creștere"),
    ("Valoarea medie a unui contract", "lei", "R4: creștere"),
    ("Numărul de angajați cu specializare GIS", "persoane", "R7: +1"),
]


def set_reference(an, indicator, valoare, unitate="", observatii="", path=None):
    with conn(path) as c:
        c.execute("DELETE FROM referinta WHERE an=? AND indicator=?", (an, indicator))
        if valoare is not None:
            c.execute("INSERT INTO referinta(an, indicator, valoare, unitate, observatii, ts) VALUES (?,?,?,?,?,?)",
                      (an, indicator, valoare, unitate, observatii, now()))


def references(path=None):
    return read_sql("SELECT an, indicator, valoare, unitate, observatii, ts FROM referinta ORDER BY indicator, an",
                    path=path)
