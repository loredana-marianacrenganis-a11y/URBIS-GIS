
import io
import re
from datetime import date
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from fpdf import FPDF

STEREO70 = 3844
NAVY = "#1F2A4D"
ACCENT = "#d7263d"
LEVEL_BINS = [0, 1, 2, 4, 8, 200]
LEVEL_LABELS = ["P", "P+1", "P+2…P+3", "P+4…P+7", "P+8 și peste"]
LEVEL_COLORS = ["#c9d6e8", "#8fb0d9", "#4f84c4", "#2b5797", "#1F2A4D"]

TIP_RO = {
    "yes": "nespecificat", "house": "casă", "detached": "casă izolată",
    "semidetached_house": "casă cuplată", "terrace": "case înșiruite",
    "apartments": "bloc de locuințe", "residential": "rezidențial",
    "garage": "garaj", "garages": "garaje", "shed": "anexă", "commercial": "comercial",
    "retail": "comerț", "industrial": "industrial", "warehouse": "depozit",
    "office": "birouri", "school": "școală", "university": "universitate",
    "church": "biserică", "hospital": "spital", "public": "public",
    "construction": "în construcție", "roof": "acoperiș/copertină", "service": "tehnic",
    "kindergarten": "grădiniță", "hotel": "hotel", "dormitory": "cămin",
}


def _levels(row):
    for val, div in ((row.get("niveluri"), 1), (row.get("inaltime"), 3.0)):
        if val:
            m = re.search(r"\d+(\.\d+)?", str(val))
            if m:
                return max(1, round(float(m.group()) / div))
    return None


def compute(zone_wgs84, buildings, evolution=None, evo_field=None):
    zone = gpd.GeoSeries([zone_wgs84], crs=4326).to_crs(STEREO70)
    zone_area = float(zone.area.iloc[0])
    centroid = zone_wgs84.centroid

    b = buildings.to_crs(STEREO70).copy() if not buildings.empty else buildings
    ind = {"zona_m2": zone_area, "zona_ha": zone_area / 1e4,
           "centru": (centroid.y, centroid.x), "data": date.today().strftime("%d.%m.%Y")}

    n = len(b)
    ind["nr_cladiri"] = n
    if n:
        b["amprenta"] = b.area
        b["niv"] = b.apply(_levels, axis=1)
        known = b["niv"].notna()
        b["niv_est"] = b["niv"].fillna(1)
        b["arie_desf"] = b["amprenta"] * b["niv_est"]
        ind.update({
            "amprenta_m2": b["amprenta"].sum(),
            "amprenta_medie": b["amprenta"].mean(),
            "amprenta_mediana": b["amprenta"].median(),
            "pot": 100 * b["amprenta"].sum() / zone_area,
            "cut": b["arie_desf"].sum() / zone_area,
            "densitate_ha": n / (zone_area / 1e4),
            "niv_cunoscute_pct": 100 * known.mean(),
            "niv_mediu": b.loc[known, "niv"].mean() if known.any() else None,
            "niv_max": int(b["niv"].max()) if known.any() else None,
            "inalte": int((b["niv"] >= 5).sum()),
        })
        tip = b["tip"].map(lambda t: TIP_RO.get(t, t))
        ind["tipuri"] = (pd.DataFrame({"tip": tip, "amprenta": b["amprenta"]})
                         .groupby("tip").agg(cladiri=("amprenta", "size"),
                                             amprenta_m2=("amprenta", "sum"))
                         .sort_values("cladiri", ascending=False))
        ind["clase_niveluri"] = (pd.cut(b.loc[known, "niv"], LEVEL_BINS,
                                        labels=LEVEL_LABELS, include_lowest=True)
                                 .value_counts().reindex(LEVEL_LABELS).fillna(0).astype(int))
        years = pd.to_numeric(b["an"].astype(str).str.extract(r"(\d{4})")[0], errors="coerce")
        ind["an_cunoscut"] = int(years.notna().sum())
        ind["dupa_2011"] = int((years >= 2011).sum())
    ind["cladiri"] = b

    if evolution is not None and evo_field:
        e = evolution.to_crs(STEREO70)
        e = e[e.geometry.representative_point().within(zone.iloc[0])].copy()
        e["arie"] = e.area
        ind["evolutie"] = (e.groupby(e[evo_field].astype(str))
                           .agg(obiecte=("arie", "size"), arie_m2=("arie", "sum"))
                           .sort_values("obiecte", ascending=False))
        ind["evolutie_camp"] = evo_field
        ind["evolutie_gdf"] = e
    return ind


def summary_text(ind, zone_name):
    if not ind["nr_cladiri"]:
        return f"Zona {zone_name} ({ind['zona_ha']:.1f} ha) nu are clădiri cartografiate în OSM."
    lines = [
        f"Zona: {zone_name}, suprafață {ind['zona_ha']:.1f} ha.",
        f"Clădiri OSM: {ind['nr_cladiri']} ({ind['densitate_ha']:.1f}/ha), "
        f"amprentă totală {ind['amprenta_m2']:.0f} m², amprentă medie {ind['amprenta_medie']:.0f} m².",
        f"Grad de ocupare a terenului (estimare POT): {ind['pot']:.1f}%; "
        f"coeficient de utilizare estimat (CUT): {ind['cut']:.2f}.",
        f"Tipuri principale: " + ", ".join(f"{r.Index} ({r.cladiri})" for r in
                                           ind["tipuri"].head(5).itertuples()) + ".",
    ]
    if ind.get("niv_mediu"):
        lines.append(f"Niveluri cunoscute la {ind['niv_cunoscute_pct']:.0f}% din clădiri; "
                     f"medie {ind['niv_mediu']:.1f}, maxim {ind['niv_max']}, "
                     f"{ind['inalte']} clădiri cu cel puțin 5 niveluri.")
    if "evolutie" in ind:
        lines.append("Schimbări 2011–2024 (strat de evoluție): " + ", ".join(
            f"{r.Index}: {r.obiecte}" for r in ind["evolutie"].itertuples()) + ".")
    return " ".join(lines)


def map_figure(zone_wgs84, ind, dpi=170):
    zone = gpd.GeoSeries([zone_wgs84], crs=4326).to_crs(STEREO70)
    fig, ax = plt.subplots(figsize=(7, 6), dpi=dpi)
    b = ind["cladiri"]
    if len(b):
        cls = pd.cut(b["niv"], LEVEL_BINS, labels=False, include_lowest=True)
        colors = [LEVEL_COLORS[int(c)] if pd.notna(c) else "#bdbdbd" for c in cls]
        b.plot(ax=ax, color=colors, edgecolor="#555", linewidth=0.2)
    if "evolutie_gdf" in ind and len(ind["evolutie_gdf"]):
        e = ind["evolutie_gdf"]
        e.boundary.plot(ax=ax, color=ACCENT, linewidth=0.8)
    zone.boundary.plot(ax=ax, color=NAVY, linewidth=1.6, linestyle="--")
    ax.set_axis_off()
    ax.set_aspect("equal")
    x0, y0, x1, y1 = zone.total_bounds
    span = x1 - x0
    bar = next(s for s in (50, 100, 200, 500, 1000, 2000, 5000) if s > span / 6)
    bx, by = x0 + span * 0.04, y0 + (y1 - y0) * 0.04
    ax.plot([bx, bx + bar], [by, by], color="black", linewidth=3)
    ax.text(bx + bar / 2, by + (y1 - y0) * 0.015, f"{bar} m", ha="center", fontsize=8)
    ax.annotate("N", xy=(0.95, 0.95), xytext=(0.95, 0.85), xycoords="axes fraction",
                ha="center", fontsize=11, fontweight="bold",
                arrowprops=dict(arrowstyle="-|>", color="black"))
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in LEVEL_COLORS + ["#bdbdbd"]]
    labels = LEVEL_LABELS + ["niveluri necunoscute"]
    if "evolutie_gdf" in ind and len(ind["evolutie_gdf"]):
        handles.append(plt.Line2D([], [], color=ACCENT))
        labels.append("schimbări 2011–2024")
    ax.legend(handles, labels, loc="lower right", fontsize=7, frameon=True)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def bar_figure(series, title, color=NAVY, dpi=170):
    fig, ax = plt.subplots(figsize=(6.5, 2.8), dpi=dpi)
    series = series[::-1]
    ax.barh([str(i) for i in series.index], series.values, color=color)
    for i, v in enumerate(series.values):
        ax.text(v, i, f" {v:,.0f}".replace(",", "."), va="center", fontsize=8)
    ax.set_title(title, fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"


def fmt(x, d=0):
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",") if x is not None else "–"


class Report(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("DejaVu", "", 8)
        self.set_text_color(120)
        self.cell(0, 6, getattr(self, "doc_title", "Raport de densificare urbană – Iași"),
                  align="L")
        self.cell(0, 6, self.zone_name, align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(220)
        self.line(15, 22, 195, 22)
        self.ln(4)

    def footer(self):
        self.set_y(-14)
        self.set_font("DejaVu", "", 7)
        self.set_text_color(130)
        self.multi_cell(0, 3.5, "Raport informativ generat automat din date publice. "
                        "Nu înlocuiește certificatul de urbanism, extrasul de carte funciară "
                        "sau măsurătorile topografice.  ·  Pagina "
                        f"{self.page_no()}/{{nb}}", align="C")

    def h1(self, text):
        self.set_font("DejaVu", "B", 14)
        self.set_text_color(31, 42, 77)
        self.cell(0, 9, text, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0)

    def para(self, text, size=9.5):
        self.set_font("DejaVu", "", size)
        self.multi_cell(0, 5, text)
        self.ln(1)

    def table(self, rows, widths, header=None):
        self.set_font("DejaVu", "", 9)
        if header:
            self.set_fill_color(31, 42, 77)
            self.set_text_color(255)
            self.set_font("DejaVu", "B", 9)
            for h, w in zip(header, widths):
                self.cell(w, 7, h, border=0, fill=True)
            self.ln()
            self.set_text_color(0)
            self.set_font("DejaVu", "", 9)
        for i, r in enumerate(rows):
            self.set_fill_color(*((244, 246, 250) if i % 2 == 0 else (255, 255, 255)))
            for v, w in zip(r, widths):
                self.cell(w, 6.5, str(v), border=0, fill=True)
            self.ln()
        self.ln(2)


def build_pdf(zone_wgs84, zone_name, ind, ai_text=None, ai_sources=None):
    pdf = Report(format="A4")
    pdf.zone_name = zone_name
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.alias_nb_pages()

    pdf.add_page()
    pdf.set_fill_color(31, 42, 77)
    pdf.rect(0, 0, 210, 34, "F")
    pdf.set_text_color(255)
    pdf.set_xy(15, 9)
    pdf.set_font("DejaVu", "B", 18)
    pdf.cell(0, 9, "Raport de densificare urbană – Iași", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 10)
    pdf.cell(0, 6, f"{zone_name}  ·  generat la {ind['data']}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.set_y(40)

    lat, lon = ind["centru"]
    pdf.para(f"Suprafața zonei analizate: {fmt(ind['zona_m2'])} m² ({fmt(ind['zona_ha'], 2)} ha). "
             f"Centrul zonei: {lat:.5f} N, {lon:.5f} E (WGS84).")
    pdf.image(io.BytesIO(map_figure(zone_wgs84, ind)), x=25, w=160)
    pdf.ln(3)

    if ind["nr_cladiri"]:
        cards = [("Clădiri", fmt(ind["nr_cladiri"])),
                 ("Densitate", f"{fmt(ind['densitate_ha'], 1)} /ha"),
                 ("POT estimat", f"{fmt(ind['pot'], 1)} %"),
                 ("CUT estimat", fmt(ind["cut"], 2))]
        x, y, w = 15, pdf.get_y(), 43.5
        for i, (lbl, val) in enumerate(cards):
            pdf.set_fill_color(244, 246, 250)
            pdf.rect(x + i * (w + 2), y, w, 20, "F")
            pdf.set_xy(x + i * (w + 2), y + 3)
            pdf.set_font("DejaVu", "", 8)
            pdf.set_text_color(90)
            pdf.cell(w, 4, lbl, align="C")
            pdf.set_xy(x + i * (w + 2), y + 9)
            pdf.set_font("DejaVu", "B", 14)
            pdf.set_text_color(31, 42, 77)
            pdf.cell(w, 7, val, align="C")
        pdf.set_text_color(0)
        pdf.set_y(y + 24)
    else:
        pdf.para("În această zonă nu există clădiri cartografiate în OpenStreetMap.")

    if ind["nr_cladiri"]:
        pdf.add_page()
        pdf.h1("1. Fondul construit")
        rows = [
            ("Număr de clădiri", fmt(ind["nr_cladiri"])),
            ("Amprentă construită totală", f"{fmt(ind['amprenta_m2'])} m²"),
            ("Amprentă medie / mediană", f"{fmt(ind['amprenta_medie'])} / "
                                         f"{fmt(ind['amprenta_mediana'])} m²"),
            ("Densitate clădiri", f"{fmt(ind['densitate_ha'], 1)} clădiri/ha"),
            ("Grad de ocupare a terenului (≈ POT)", f"{fmt(ind['pot'], 1)} %"),
            ("Coeficient de utilizare estimat (≈ CUT)", fmt(ind["cut"], 2)),
            ("Clădiri cu nr. de niveluri cunoscut", f"{fmt(ind['niv_cunoscute_pct'])} %"),
            ("Număr mediu / maxim de niveluri",
             f"{fmt(ind['niv_mediu'], 1)} / {ind['niv_max'] or '–'}"),
            ("Clădiri cu cel puțin 5 niveluri", fmt(ind["inalte"])),
        ]
        pdf.table(rows, [105, 75])
        pdf.para("POT și CUT sunt calculate pe întreaga suprafață a zonei (inclusiv străzi și "
                 "spații verzi), nu pe parcelă, deci nu se compară direct cu valorile "
                 "maxime din PUG. Clădirile fără număr de niveluri în OSM sunt considerate "
                 "cu un singur nivel, așa că CUT este o estimare minimă.", size=8.5)
        top = ind["tipuri"]["cladiri"].head(8)
        pdf.image(io.BytesIO(bar_figure(top, "Clădiri după tip (OSM)")), x=20, w=170)
        if ind["clase_niveluri"].sum():
            pdf.image(io.BytesIO(bar_figure(ind["clase_niveluri"],
                                            "Clădiri după regim de înălțime (niveluri cunoscute)",
                                            color="#4f84c4")), x=20, w=170)

    pdf.add_page()
    pdf.h1("2. Densificare 2011–2024")
    if "evolutie" in ind:
        pdf.para(f"Schimbările clădirilor din stratul de evoluție încărcat, după câmpul "
                 f"„{ind['evolutie_camp']}”, pentru obiectele cu centrul în zonă:")
        rows = [(r.Index, fmt(r.obiecte), f"{fmt(r.arie_m2)} m²")
                for r in ind["evolutie"].itertuples()]
        pdf.table(rows, [80, 40, 60], header=["Tip de schimbare", "Obiecte", "Arie"])
    else:
        pdf.para("Pentru această secțiune este necesar stratul de evoluție a clădirilor "
                 "RO-IAS-Evolution-2011-24 (TUIASI), obținut prin compararea "
                 "ortofotoplanurilor din 2011 și 2024. Stratul nu este încă publicat; după "
                 "publicare, încărcați-l în platformă pentru a completa această secțiune.")
    if ind.get("an_cunoscut"):
        pdf.para(f"Informativ, din OSM: {ind['an_cunoscut']} clădiri au anul construcției "
                 f"completat, dintre care {ind['dupa_2011']} construite în 2011 sau după.",
                 size=8.5)

    if ai_text:
        pdf.ln(2)
        pdf.h1("3. Interpretare automată")
        pdf.para(ai_text)
        if ai_sources:
            pdf.para("Documente consultate: " + ", ".join(sorted(set(ai_sources))), size=8)
        pdf.para("Textul este generat automat pe baza indicatorilor de mai "
                 "sus și a documentației fluxului FLAIR-HUB2BF. Verificați concluziile înainte de utilizare.",
                 size=8)

    pdf.ln(2)
    pdf.h1(f"{4 if ai_text else 3}. Surse, metodă și limitări")
    pdf.para("Surse: clădiri OpenStreetMap (© contribuitorii OpenStreetMap, licență ODbL), "
             "descărcate prin Overpass API la data generării; documentația tehnică "
             "a fluxului FLAIR-HUB2BF; straturi de evoluție încărcate de utilizator, "
             "dacă există. Ariile sunt calculate în proiecția Stereo 70 (EPSG:3844).")
    pdf.para("Limitări: acoperirea OSM poate fi incompletă sau neactualizată (în special "
             "numărul de niveluri și anul construcției). Raportul este informativ: nu este emis "
             "de ANCPI sau de Primăria Iași, nu conține date despre proprietari și nu înlocuiește "
             "certificatul de urbanism, extrasul de carte funciară, PUG/PUZ sau măsurătorile "
             "topografice.")
    return bytes(pdf.output())
