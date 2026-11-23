import html as _html
import json
import re
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import requests

MDLPA = "https://datelocale.mdlpa.ro"
MDLPA_REGISTRU = MDLPA + "/ro/planning/registru-pug/"
URBAN_GIS = "https://urban-gis.ro"
URBAN_GIS_RESURSE = URBAN_GIS + "/resurse"
JUDET = "IAȘI"
CACHE = Path(tempfile.gettempdir()) / "urbis_surse_publice.json"
MAX_AGE = 7 * 24 * 3600
HEADERS = {"User-Agent": "URBIS-GIS/1.0 (prototip de cercetare TUIASI)"}
LUNI = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
        "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"}
SRC_MDLPA = "Registrul MDLPA (PUG/PUZ/PUD)"
SRC_URBAN = "urban-gis.ro"


def _get(url, timeout=60):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.content.decode("utf-8", errors="replace")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def _text(fragment):
    t = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def _data(s):
    m = re.match(r"(\d{1,2}) ([A-Za-z]{3})\w* (\d{4})$", s.strip())
    if m and m.group(2).lower() in LUNI:
        return f"{int(m.group(1)):02d}.{LUNI[m.group(2).lower()]}.{m.group(3)}"
    return s.strip()


def parse_registru(page, judet=JUDET, base=MDLPA):
    i = page.find('id="registru_documentatii_table"')
    if i < 0:
        raise ValueError("tabelul registrului nu a fost găsit")
    j = page.find("<tbody", i)
    if j < 0:
        raise ValueError("tabelul registrului nu are rânduri")
    body = page[j:]
    body = body[:body.find("</tbody>")]
    out = []
    for row in re.findall(r"<tr\b.*?</tr>", body, re.S):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 10:
            continue
        v = [_text(c) for c in cells]
        if judet and norm(v[3]) != norm(judet):
            continue
        links = [urljoin(base, h) for h in re.findall(r'href="([^"]+)"', cells[9])]
        out.append({
            "siruta": v[0], "tip": v[1], "denumire": v[2], "judet": v[3], "uat": v[4],
            "stadiu": v[5], "date_spatiale": v[6], "kit": v[7], "pachet": v[8],
            "fisa": next((h for h in links if "/planning/registru" in h), ""),
            "arhiva": next((h for h in links if h.lower().endswith(".zip")), ""),
        })
    return out


def parse_fisa(page, base=MDLPA):
    i = page.find("INFORMAȚII DESPRE DOCUMENTAȚIE")
    if i < 0:
        return {}
    sec = page[i:]
    sec = sec[:sec.find("</section>")] if "</section>" in sec else sec[:20000]
    sec = re.sub(r"<!--.*?-->", " ", sec, flags=re.S)
    out = {}
    for part in re.split(r'<span class="info-card-label[^"]*">', sec)[1:]:
        label, _, rest = part.partition("</span>")
        label = _text(label)
        value = _text(rest)
        links = [urljoin(base, h) for h in re.findall(r'href="([^"]+)"', rest)]
        if label:
            out[label] = {"valoare": value, "link": links[0] if links else ""}
    return out


def registru_mdlpa(judet=JUDET, detalii=True, workers=4):
    rows = parse_registru(_get(MDLPA_REGISTRU, 120), judet)
    if detalii:
        tinte = [r for r in rows if "/registru-documentatie/" in r["fisa"]]

        def una(r):
            try:
                r["detalii"] = parse_fisa(_get(r["fisa"], 45))
            except Exception:
                r["detalii"] = {}

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(una, tinte))
    return rows


class _JS:
    ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}

    def __init__(self, s, i=0):
        self.s, self.i = s, i

    def _ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \n\r\t":
            self.i += 1

    def value(self):
        self._ws()
        c = self.s[self.i]
        if c == "{":
            return self._obj()
        if c == "[":
            return self._arr()
        if c in "`\"'":
            return self._str(c)
        for lit, val in (("!0", True), ("!1", False), ("true", True), ("false", False),
                         ("null", None), ("void 0", None)):
            if self.s.startswith(lit, self.i):
                self.i += len(lit)
                return val
        m = re.compile(r"-?\d+(?:\.\d+)?(?:e-?\d+)?").match(self.s, self.i)
        if m:
            self.i = m.end()
            t = m.group(0)
            return float(t) if any(ch in t for ch in ".e") else int(t)
        raise ValueError(f"valoare neașteptată la {self.i}: {self.s[self.i:self.i + 20]!r}")

    def _str(self, q):
        self.i += 1
        out = []
        while True:
            c = self.s[self.i]
            if c == "\\":
                n = self.s[self.i + 1]
                if n == "u":
                    if self.s[self.i + 2] == "{":
                        j = self.s.index("}", self.i)
                        out.append(chr(int(self.s[self.i + 3:j], 16)))
                        self.i = j + 1
                    else:
                        out.append(chr(int(self.s[self.i + 2:self.i + 6], 16)))
                        self.i += 6
                    continue
                if n == "x":
                    out.append(chr(int(self.s[self.i + 2:self.i + 4], 16)))
                    self.i += 4
                    continue
                if n == "\n":
                    self.i += 2
                    continue
                out.append(self.ESC.get(n, n))
                self.i += 2
                continue
            if q == "`" and self.s.startswith("${", self.i):
                raise ValueError("șablon cu expresii")
            if c == q:
                self.i += 1
                return "".join(out)
            out.append(c)
            self.i += 1

    def _key(self):
        self._ws()
        c = self.s[self.i]
        if c in "`\"'":
            return self._str(c)
        m = re.compile(r"[A-Za-z_$][\w$]*|\d+").match(self.s, self.i)
        if not m:
            raise ValueError(f"cheie neașteptată la {self.i}")
        self.i = m.end()
        return m.group(0)

    def _obj(self):
        self.i += 1
        out = {}
        while True:
            self._ws()
            if self.s[self.i] == "}":
                self.i += 1
                return out
            k = self._key()
            self._ws()
            if self.s[self.i] != ":":
                raise ValueError(f"lipsește ':' la {self.i}")
            self.i += 1
            out[k] = self.value()
            self._ws()
            if self.s[self.i] == ",":
                self.i += 1

    def _arr(self):
        self.i += 1
        out = []
        while True:
            self._ws()
            if self.s[self.i] == "]":
                self.i += 1
                return out
            out.append(self.value())
            self._ws()
            if self.s[self.i] == ",":
                self.i += 1


def parse_articole(js):
    out, vazute = [], set()
    for m in re.finditer(r"\{id:[`\"'][^`\"']{1,40}[`\"'],slug:[`\"']", js):
        try:
            a = _JS(js, m.start()).value()
        except Exception:
            continue
        if not isinstance(a, dict) or not a.get("title") or a.get("slug") in vazute:
            continue
        if a.get("published") is False:
            continue
        vazute.add(a.get("slug"))
        out.append(a)
    return out


def resurse_urban_gis():
    page = _get(URBAN_GIS_RESURSE, 30)
    scripturi = re.findall(r'<script[^>]+src="([^"]+\.js)"', page)
    if not scripturi:
        raise ValueError("pagina de resurse nu are scripturi")
    arts = []
    for s in scripturi:
        arts = parse_articole(_get(urljoin(URBAN_GIS_RESURSE, s), 60))
        if arts:
            break
    return arts


def _str_list(v):
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def sectiuni_articol(a, base=URBAN_GIS):
    url = urljoin(base, a.get("href") or f"/resurse/{a.get('slug', '')}")
    titlu = a.get("title", "").strip()
    src = f"{SRC_URBAN} – {titlu}"
    head = f"[{titlu}] {url}"
    out = []
    intro = " ".join(_str_list(a.get("intro")) or _str_list(a.get("excerpt")))
    if intro:
        out.append((src, f"{head}\n{intro}"))
    for s in a.get("sections") or []:
        if not isinstance(s, dict):
            continue
        parts = _str_list(s.get("paragraphs"))
        items = _str_list(s.get("items"))
        if items:
            parts.append("; ".join(items) + ".")
        parts += _str_list(s.get("afterItems"))
        t = s.get("comparisonTable")
        if isinstance(t, dict):
            cols = _str_list(t.get("columns"))
            for r in t.get("rows") or []:
                if isinstance(r, dict):
                    vals = _str_list(r.get("values"))
                    parts.append(f"{r.get('label', '')}: " + "; ".join(f"{c} – {v}" for c, v in zip(cols, vals)) + ".")
        parts += _str_list(s.get("afterComparisonTable"))
        body = " ".join(p.strip() for p in parts if p.strip())
        if body:
            out.append((src, f"{head}\n## {s.get('title', '')}\n{body}"))
    return out


def _uat_scurt(uat):
    return re.sub(r"^(MUNICIPIUL|ORAȘ|ORAS|ORAȘUL|COMUNA)\s+", "", str(uat).strip(), flags=re.I)


def sectiune_registru(r):
    d = r.get("detalii") or {}

    def val(k):
        return (d.get(k) or {}).get("valoare", "").strip()

    titlu = r["denumire"]
    rand = [f"[{SRC_MDLPA}] {titlu}",
            f"Tip: {r['tip']}. UAT: {r['uat']} (SIRUTA {r['siruta']}), județul {r['judet']}.",
            f"Stadiu în registru: {r['stadiu']}. Date spațiale conforme Ordinului MDLPA nr. 904/2023: "
            f"{r['date_spatiale']}."]
    if r.get("kit") not in ("", "-"):
        rand.append(f"Validare: kit {r['kit']}, pachet de reguli {r['pachet']}.")
    if val("Număr HCL aprobare"):
        rand.append(f"Aprobat prin HCL nr. {val('Număr HCL aprobare')} din {_data(val('Dată HCL aprobare'))}.")
    if val("Număr HCL prelungire"):
        rand.append(f"Valabilitate prelungită prin HCL nr. {val('Număr HCL prelungire')} din "
                    f"{_data(val('Dată HCL prelungire'))}.")
    if val("Dată valabilitate documentație"):
        rand.append(f"Dată valabilitate documentație: {_data(val('Dată valabilitate documentație'))}.")
    if val("Descriere") and norm(val("Descriere")) != norm(titlu):
        rand.append(f"Descriere: {val('Descriere')}.")
    if r.get("fisa"):
        rand.append(f"Fișa în registru: {r['fisa']}")
    for k in ("Document HCL aprobare", "Document HCL prelungire"):
        if (d.get(k) or {}).get("link"):
            rand.append(f"{k}: {d[k]['link']}")
    if r.get("arhiva"):
        rand.append(f"Arhiva documentației: {r['arhiva']}")
    return (f"{SRC_MDLPA} – {_uat_scurt(r['uat']).title()} ({r['tip']})", "\n".join(rand))


def sinteza_registru(rows, judet=JUDET):
    if not rows:
        return []
    from collections import Counter
    tip = Counter(r["tip"] for r in rows)
    stadiu = Counter(r["stadiu"] for r in rows)
    spatiale = [r for r in rows if norm(r["date_spatiale"]) == "DISPONIBILE"]
    out = [(f"{SRC_MDLPA} – sinteză județul {judet.title()}",
            f"[{SRC_MDLPA}] Sinteză pentru județul {judet}: {len(rows)} documentații înregistrate ("
            + ", ".join(f"{n} {t}" for t, n in tip.most_common()) + "). Pe stadii: "
            + ", ".join(f"{s.lower()} – {n}" for s, n in stadiu.most_common())
            + f". Cu date spațiale disponibile (Ordinul MDLPA nr. 904/2023): {len(spatiale)}.\n"
            + f"Sursa: {MDLPA_REGISTRU}")]
    grupe = {}
    for r in rows:
        grupe.setdefault((r["tip"], r["stadiu"], norm(r["date_spatiale"]) == "DISPONIBILE"), []).append(r)
    for (t, s, ds), lst in sorted(grupe.items(), key=lambda x: -len(x[1])):
        nume = ", ".join(sorted(_uat_scurt(r["uat"]).title() for r in lst))
        out.append((f"{SRC_MDLPA} – {t}, {s.lower()}",
                    f"[{SRC_MDLPA}] Județul {judet}: {t} cu stadiul „{s}”"
                    f"{' și date spațiale disponibile' if ds else ', fără date spațiale publicate'}"
                    f" ({len(lst)}): {nume}.\nSursa: {MDLPA_REGISTRU}"))
    return out


def potriviri(intrebare, items, limita=3):
    q = norm(intrebare)
    if not re.search(r"PUG|PUZ|PUD|REGISTR|HCL|DOCUMENTATI|MDLPA|URBANISM|APROBA|VIGOARE|RLU", q):
        return []
    q = re.sub(r"JUDET\w*\s+(?:DE\s+)?IASI|JUD\.?\s*IASI", " ", q)
    out = []
    for src, txt in items:
        if not src.startswith(SRC_MDLPA) or "sinteză" in src or "," in src.split("–", 1)[-1]:
            continue
        nume = norm(src.split("–", 1)[-1].rsplit("(", 1)[0])
        if nume and re.search(rf"(?<![A-Z]){re.escape(nume)}(?![A-Z])", q):
            out.append((src, txt))
    return out[:limita]


def incarca(fortat=False, judet=JUDET, progres=None):
    progres = progres if progres is not None else {}
    if not fortat and CACHE.exists():
        try:
            d = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - d.get("timp", 0) < MAX_AGE and d.get("judet") == judet:
                return d
        except Exception:
            pass
    d = {"timp": time.time(), "judet": judet, "registru": [], "articole": [], "erori": {}}
    progres["etapa"] = "registrul MDLPA"
    try:
        d["registru"] = registru_mdlpa(judet)
    except Exception as e:
        d["erori"]["mdlpa"] = str(e)[:300]
    progres["etapa"] = "resursele urban-gis.ro"
    try:
        d["articole"] = resurse_urban_gis()
    except Exception as e:
        d["erori"]["urban_gis"] = str(e)[:300]
    if d["registru"] or d["articole"]:
        try:
            CACHE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return d


def sectiuni(d):
    out = sinteza_registru(d.get("registru") or [], d.get("judet", JUDET))
    out += [sectiune_registru(r) for r in d.get("registru") or []]
    for a in d.get("articole") or []:
        out += sectiuni_articol(a)
    return out


if __name__ == "__main__":
    d = incarca(fortat=True)
    s = sectiuni(d)
    print(f"registru: {len(d['registru'])} documentații, articole: {len(d['articole'])}, "
          f"secțiuni: {len(s)}, erori: {d['erori']}")
