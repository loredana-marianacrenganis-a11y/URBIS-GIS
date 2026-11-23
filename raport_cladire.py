
import io
import math

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely.geometry import MultiPolygon, Point

import raport
from raport import STEREO70, NAVY, ACCENT, TIP_RO, fmt

CONTEXT_M = 100
NEAR_M = 50
MAX_VERTICES = 16


def regim(niv):
    if not niv:
        return "necunoscut"
    return "P" if niv == 1 else f"P+{niv - 1}"


def pick_building(buildings, lat, lon, tol_m=15):
    if buildings.empty:
        return None
    pt = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(STEREO70).iloc[0]
    b = buildings.to_crs(STEREO70)
    inside = b[b.contains(pt)]
    if len(inside):
        return inside.index[0]
    d = b.distance(pt)
    return d.idxmin() if d.min() <= tol_m else None


def _main_polygon(geom):
    return max(geom.geoms, key=lambda g: g.area) if isinstance(geom, MultiPolygon) else geom


def compute(idx, buildings, evolution=None, evo_field=None):
    b = buildings.to_crs(STEREO70).copy()
    row = b.loc[idx]
    geom = row.geometry
    poly = _main_polygon(geom)
    plan = poly if len(poly.exterior.coords) - 1 <= MAX_VERTICES else poly.simplify(0.5)

    rect = list(poly.minimum_rotated_rectangle.exterior.coords)
    sides = sorted({round(math.dist(rect[i], rect[i + 1]), 1) for i in range(2)})
    dx, dy = rect[1][0] - rect[0][0], rect[1][1] - rect[0][1]
    orient = (math.degrees(math.atan2(dx, dy)) + 360) % 180

    niv = raport._levels(row)
    c = geom.centroid
    c_wgs = gpd.GeoSeries([c], crs=STEREO70).to_crs(4326).iloc[0]
    ind = {
        "osm_id": row["osm_id"], "tip": TIP_RO.get(row["tip"], row["tip"]),
        "nume": row.get("nume"), "adresa": row.get("adresa"), "an": row.get("an"),
        "niveluri": niv, "regim": regim(niv),
        "niv_sursa": "OSM building:levels" if row.get("niveluri") else
                     ("estimat din înălțime" if row.get("inaltime") else None),
        "inaltime": row.get("inaltime"),
        "amprenta": geom.area, "perimetru": geom.length,
        "lungime": sides[-1], "latime": sides[0], "orientare": orient,
        "arie_desf": geom.area * niv if niv else None,
        "centru_s70": (c.x, c.y), "centru_wgs": (c_wgs.y, c_wgs.x),
        "varfuri": [(round(x, 2), round(y, 2)) for x, y in list(plan.exterior.coords)[:-1]],
        "geom_s70": geom, "plan_s70": plan,
    }

    others = b.drop(index=idx)
    dist = others.distance(geom)
    near = others[dist <= NEAR_M]
    ind["vecini_nr"] = len(near)
    if len(others):
        j = dist.idxmin()
        ind["vecin_dist"] = float(dist.min())
        ind["vecin_tip"] = TIP_RO.get(others.loc[j, "tip"], others.loc[j, "tip"])
    ctx_zone = gpd.GeoSeries([c.buffer(CONTEXT_M, 64)], crs=STEREO70).to_crs(4326).iloc[0]
    in_ctx = buildings[buildings.geometry.representative_point().within(ctx_zone)]
    ctx = raport.compute(ctx_zone, in_ctx, evolution, evo_field)
    ind["context"] = ctx
    ind["context_zone"] = ctx_zone
    if ctx.get("niv_mediu") and niv:
        ind["fata_de_context"] = niv - ctx["niv_mediu"]

    if evolution is not None and evo_field:
        e = evolution.to_crs(STEREO70)
        hit = e[e.intersects(geom)]
        ind["evolutie"] = sorted(set(hit[evo_field].astype(str))) if len(hit) else []
        ind["evolutie_camp"] = evo_field
    return ind


def summary_text(ind):
    parts = [f"Clădire {ind['tip']}" + (f" „{ind['nume']}”" if ind["nume"] else "") +
             (f", {ind['adresa']}" if ind["adresa"] else "") + ".",
             f"Amprentă {ind['amprenta']:.0f} m², aproximativ {ind['lungime']:.0f} × "
             f"{ind['latime']:.0f} m, regim de înălțime {ind['regim']}."]
    if ind["arie_desf"]:
        parts.append(f"Arie desfășurată estimată {ind['arie_desf']:.0f} m².")
    if ind["an"]:
        parts.append(f"An construcție (OSM): {ind['an']}.")
    parts.append(f"{ind['vecini_nr']} clădiri la sub {NEAR_M} m; cea mai apropiată la "
                 f"{ind.get('vecin_dist', 0):.1f} m ({ind.get('vecin_tip', '–')}).")
    ctx = ind["context"]
    if ctx["nr_cladiri"]:
        parts.append(f"Pe o rază de {CONTEXT_M} m: {ctx['nr_cladiri']} clădiri, POT estimat "
                     f"{ctx['pot']:.1f}%, CUT estimat {ctx['cut']:.2f}" +
                     (f", nivel mediu {ctx['niv_mediu']:.1f}" if ctx.get("niv_mediu") else "") + ".")
    if "evolutie" in ind:
        parts.append("Schimbări 2011–2024 pe amprenta clădirii: " +
                     (", ".join(ind["evolutie"]) or "niciuna înregistrată") + ".")
    return " ".join(parts)


def plan_figure(ind, dpi=170):
    plan = ind["plan_s70"]
    pts = np.array(plan.exterior.coords)
    fig, ax = plt.subplots(figsize=(6.5, 5.2), dpi=dpi)
    ax.fill(pts[:, 0], pts[:, 1], color="#c9d6e8", ec=NAVY, lw=1.8)
    cx, cy = plan.centroid.x, plan.centroid.y
    span = max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]))
    for i in range(len(pts) - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        L = math.dist((x0, y0), (x1, y1))
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        nx, ny = mx - cx, my - cy
        n = math.hypot(nx, ny) or 1
        if L >= span * 0.06:
            ax.text(mx + nx / n * span * 0.05, my + ny / n * span * 0.05, f"{L:.2f} m",
                    ha="center", va="center", fontsize=7.5, color="#333")
        ax.plot(x0, y0, "o", color=ACCENT, ms=4)
        ax.text(x0 + (x0 - cx) / (math.hypot(x0 - cx, y0 - cy) or 1) * span * 0.04,
                y0 + (y0 - cy) / (math.hypot(x0 - cx, y0 - cy) or 1) * span * 0.04,
                str(i + 1), fontsize=8, color=ACCENT, fontweight="bold",
                ha="center", va="center")
    pad = span * 0.18
    ax.set_xlim(pts[:, 0].min() - pad, pts[:, 0].max() + pad)
    ax.set_ylim(pts[:, 1].min() - pad, pts[:, 1].max() + pad)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.annotate("N", xy=(0.95, 0.95), xytext=(0.95, 0.83), xycoords="axes fraction",
                ha="center", fontsize=11, fontweight="bold",
                arrowprops=dict(arrowstyle="-|>", color="black"))
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def context_figure(ind, dpi=170):
    fig, ax = plt.subplots(figsize=(7, 6), dpi=dpi)
    ctx = ind["context"]["cladiri"]
    zone = gpd.GeoSeries([ind["context_zone"]], crs=4326).to_crs(STEREO70)
    if len(ctx):
        cls = pd.cut(ctx["niv"], raport.LEVEL_BINS, labels=False, include_lowest=True)
        colors = [raport.LEVEL_COLORS[int(c)] if pd.notna(c) else "#bdbdbd" for c in cls]
        ctx.plot(ax=ax, color=colors, edgecolor="#555", linewidth=0.3)
    gpd.GeoSeries([ind["geom_s70"]], crs=STEREO70).plot(ax=ax, color=ACCENT, edgecolor="black",
                                                        linewidth=1.2)
    zone.boundary.plot(ax=ax, color=NAVY, linewidth=1.4, linestyle="--")
    ax.set_axis_off()
    ax.set_aspect("equal")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in raport.LEVEL_COLORS + ["#bdbdbd", ACCENT]]
    ax.legend(handles, raport.LEVEL_LABELS + ["niveluri necunoscute", "clădirea analizată"],
              loc="lower right", fontsize=7)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def build_pdf(ind, title, ai_text=None, ai_sources=None):
    pdf = raport.Report(format="A4")
    pdf.zone_name = title
    pdf.doc_title = "Raport de construcție – Iași"
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("DejaVu", "", str(raport.FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(raport.FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.alias_nb_pages()

    pdf.add_page()
    pdf.set_fill_color(31, 42, 77)
    pdf.rect(0, 0, 210, 34, "F")
    pdf.set_text_color(255)
    pdf.set_xy(15, 9)
    pdf.set_font("DejaVu", "B", 18)
    pdf.cell(0, 9, "Raport de construcție – Iași", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 10)
    pdf.cell(0, 6, f"{title}  ·  generat la {ind['context']['data']}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.set_y(40)
    lat, lon = ind["centru_wgs"]
    pdf.para(f"Identificare: {ind['osm_id']} (OpenStreetMap). Centru: {lat:.6f} N, "
             f"{lon:.6f} E (WGS84)  ·  X = {fmt(ind['centru_s70'][0], 2)}, "
             f"Y = {fmt(ind['centru_s70'][1], 2)} (Stereo 70).")
    pdf.h1("Planul construcției")
    pdf.image(io.BytesIO(plan_figure(ind)), x=35, w=140)

    cards = [("Amprentă", f"{fmt(ind['amprenta'])} m²"),
             ("Regim înălțime", ind["regim"]),
             ("Arie desfășurată", f"{fmt(ind['arie_desf'])} m²" if ind["arie_desf"] else "–"),
             ("An construcție", ind["an"] or "–")]
    x, y, w = 15, pdf.get_y() + 2, 43.5
    if y > 255:
        pdf.add_page()
        y = pdf.get_y()
    for i, (lbl, val) in enumerate(cards):
        pdf.set_fill_color(244, 246, 250)
        pdf.rect(x + i * (w + 2), y, w, 20, "F")
        pdf.set_xy(x + i * (w + 2), y + 3)
        pdf.set_font("DejaVu", "", 8)
        pdf.set_text_color(90)
        pdf.cell(w, 4, lbl, align="C")
        pdf.set_xy(x + i * (w + 2), y + 9)
        pdf.set_font("DejaVu", "B", 13)
        pdf.set_text_color(31, 42, 77)
        pdf.cell(w, 7, str(val), align="C")
    pdf.set_text_color(0)
    pdf.set_y(y + 24)

    pdf.add_page()
    pdf.h1("1. Caracteristicile construcției")
    rows = [("Destinație (tip OSM)", ind["tip"]),
            ("Denumire", ind["nume"] or "–"),
            ("Adresă (OSM)", ind["adresa"] or "–"),
            ("Amprentă la sol", f"{fmt(ind['amprenta'], 1)} m²"),
            ("Perimetru", f"{fmt(ind['perimetru'], 1)} m"),
            ("Dimensiuni (dreptunghi încadrator)", f"{fmt(ind['lungime'], 1)} × {fmt(ind['latime'], 1)} m"),
            ("Orientarea laturii lungi", f"{fmt(ind['orientare'])}° față de nord"),
            ("Număr de niveluri", f"{ind['niveluri'] or '–'}" +
             (f" ({ind['niv_sursa']})" if ind["niv_sursa"] else "")),
            ("Regim de înălțime", ind["regim"]),
            ("Arie desfășurată estimată", f"{fmt(ind['arie_desf'])} m²" if ind["arie_desf"] else
             "– (lipsește numărul de niveluri)"),
            ("An construcție (OSM)", ind["an"] or "–")]
    pdf.table(rows, [80, 100])
    pdf.h1("2. Coordonatele colțurilor (Stereo 70)")
    v = ind["varfuri"]
    rows = []
    for i, (x_, y_) in enumerate(v):
        nx_, ny_ = v[(i + 1) % len(v)]
        rows.append((str(i + 1), fmt(x_, 2), fmt(y_, 2),
                     f"{i + 1}–{(i + 1) % len(v) + 1}: {fmt(math.dist((x_, y_), (nx_, ny_)), 2)} m"))
    pdf.table(rows, [15, 50, 50, 65], header=["Pct.", "X (m)", "Y (m)", "Latura"])
    pdf.para("Coordonatele provin din conturul OpenStreetMap, transformat în Stereo 70. "
             "Precizia este de ordinul metrilor; nu sunt coordonate măsurate topografic.",
             size=8.5)

    pdf.add_page()
    pdf.h1(f"3. Vecinătatea (rază {CONTEXT_M} m)")
    pdf.image(io.BytesIO(context_figure(ind)), x=35, w=140)
    ctx = ind["context"]
    rows = [("Clădiri la sub 50 m", fmt(ind["vecini_nr"])),
            ("Cea mai apropiată clădire", f"{fmt(ind.get('vecin_dist'), 1)} m "
                                          f"({ind.get('vecin_tip', '–')})")]
    if ctx["nr_cladiri"]:
        rows += [("Clădiri în raza de 100 m", fmt(ctx["nr_cladiri"])),
                 ("POT estimat al vecinătății", f"{fmt(ctx['pot'], 1)} %"),
                 ("CUT estimat al vecinătății", fmt(ctx["cut"], 2)),
                 ("Nivel mediu în vecinătate", fmt(ctx.get("niv_mediu"), 1))]
    if "fata_de_context" in ind:
        d = ind["fata_de_context"]
        rows.append(("Față de vecinătate", f"{'mai înaltă' if d > 0 else 'mai joasă'} cu "
                                           f"{fmt(abs(d), 1)} niveluri decât media"))
    pdf.table(rows, [90, 90])

    pdf.h1("4. Densificare 2011–2024")
    if "evolutie" in ind:
        pdf.para(f"Schimbări din stratul de evoluție (câmpul „{ind['evolutie_camp']}”) care se "
                 "suprapun cu amprenta clădirii: " + (", ".join(ind["evolutie"]) or
                                                      "nu există înregistrări.") + ".")
    else:
        pdf.para("Pentru această secțiune este necesar stratul RO-IAS-Evolution-2011-24 "
                 "(FLAIR-HUB2BF). Stratul nu este încă publicat; după publicare, încărcați-l în "
                 "platformă pentru a afla dacă clădirea a apărut, a dispărut sau s-a extins "
                 "între 2011 și 2024.")

    n = 5
    if ai_text:
        pdf.h1(f"{n}. Interpretare automată")
        pdf.para(ai_text)
        if ai_sources:
            pdf.para("Documente consultate: " + ", ".join(sorted(set(ai_sources))), size=8)
        pdf.para("Text generat automat; verificați concluziile.", size=8)
        n += 1
    pdf.h1(f"{n}. Surse și limitări")
    pdf.para("Surse: OpenStreetMap (© contribuitorii OpenStreetMap, licență ODbL), prin "
             "Overpass API la data generării; documentația fluxului FLAIR-HUB2BF (fluxul FLAIR-HUB2BF); straturi de evoluție încărcate de utilizator, dacă există.")
    pdf.para("Raport informativ: nu este emis de ANCPI sau de Primăria Iași, nu conține date "
             "despre proprietari sau situația juridică și nu înlocuiește extrasul de carte "
             "funciară, certificatul de urbanism, autorizația de construire sau releveul "
             "topografic. Datele OSM pot fi incomplete sau neactualizate.")
    return bytes(pdf.output())
