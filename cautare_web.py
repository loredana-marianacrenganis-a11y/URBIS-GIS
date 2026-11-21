# cautare_web.py - agent de căutare pe web pentru URBIS GIS
#
# Caută, pentru o zonă și o temă (de ex. „construcții noi Iași Copou”):
#   - articole științifice în OpenAlex (gratuit, fără cheie; >250 milioane de lucrări);
#   - știri de presă din Bună Ziua Iași (RSS public), cu Google News și GDELT ca rezervă; toate gratuite, fără cheie.
# Apoi modelul de limbaj ales în platformă (Gemini gratuit, Claude sau Ollama) face o
# sinteză în română, cu trimitere la fiecare sursă [1], [2] ... Fără model, se afișează lista.

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
import streamlit as st

import rag_iasi as rag

UA = {"User-Agent": "URBIS-GIS/1.0 (TUIASI; cercetare)"}


def _abstract(inv):
    """OpenAlex dă rezumatul ca index inversat {cuvânt: [poziții]}."""
    if not inv:
        return ""
    pos = sorted((p, w) for w, ps in inv.items() for p in ps)
    return " ".join(w for _, w in pos)


@st.cache_data(ttl=3600, show_spinner=False)
def articole(query, n=8, de_la=2015):
    r = requests.get("https://api.openalex.org/works", headers=UA, timeout=30, params={
        "search": query, "per-page": n, "sort": "relevance_score:desc",
        "filter": f"from_publication_date:{de_la}-01-01",
        "select": "title,publication_year,doi,id,primary_location,authorships,abstract_inverted_index"})
    r.raise_for_status()
    out = []
    for w in r.json().get("results", []):
        src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
        aut = ", ".join(a["author"]["display_name"] for a in (w.get("authorships") or [])[:3])
        out.append({"tip": "articol", "titlu": w.get("title") or "", "an": w.get("publication_year"),
                    "sursa": src, "autori": aut, "url": w.get("doi") or w.get("id"),
                    "text": _abstract(w.get("abstract_inverted_index"))[:900]})
    return out


def _gdelt(query, n, luni):
    params = {"query": query + " sourcelang:romanian", "mode": "ArtList", "format": "json",
              "maxrecords": n, "sort": "DateDesc", "timespan": f"{luni * 30}d"}
    for incercare in range(2):  # GDELT cere cel mult o cerere la 5 secunde
        r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", headers=UA, timeout=30, params=params)
        if r.status_code == 429 and incercare == 0:
            time.sleep(6)
            continue
        r.raise_for_status()
        break
    try:
        arts = r.json().get("articles", [])
    except ValueError:  # răspuns text simplu la interogări prea scurte
        return []
    out = []
    for a in arts:
        d = a.get("seendate", "")[:8]
        out.append({"tip": "presă", "titlu": a.get("title", ""), "an": d[:4],
                    "data": f"{d[6:8]}.{d[4:6]}.{d[:4]}" if len(d) == 8 else "",
                    "sursa": a.get("domain", ""), "autori": "", "url": a.get("url", ""), "text": ""})
    return out


TEME_BZI = ["demolare", "construcții noi", "autorizație de construire", "bloc nou", "proiect imobiliar"]
CUVINTE_TEMA = re.compile(r"demol|desfiin|construi|construc|bloc|autoriz|imobil|dezvolt|cartier|ansamblu|mansard", re.I)


def _bzi(query, n, luni):
    """Bună Ziua Iași (bzi.ro): flux RSS de căutare WordPress, filtrat pe temă și dată.
    Presa locală e cea mai relevantă pentru construcții și demolări în Iași."""
    limita = datetime.now(timezone.utc) - timedelta(days=luni * 30)
    gasite = {}
    for tema in TEME_BZI:
        r = requests.get("https://www.bzi.ro/", params={"s": tema, "feed": "rss2"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        for it in ET.fromstring(r.content).iter("item"):
            titlu, link = it.findtext("title") or "", it.findtext("link") or ""
            if not link or link in gasite or not CUVINTE_TEMA.search(titlu):
                continue
            try:
                dt = parsedate_to_datetime(it.findtext("pubDate") or "")
            except Exception:
                continue
            if dt < limita:
                continue
            desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
            gasite[link] = {"tip": "presă", "titlu": titlu, "an": str(dt.year), "dt": dt,
                            "data": dt.strftime("%d.%m.%Y"), "sursa": "bzi.ro", "autori": "", "url": link,
                            "text": re.sub(r"\s+", " ", desc).strip()[:300]}
    rez = sorted(gasite.values(), key=lambda x: x["dt"], reverse=True)[:n]
    for x in rez:
        x.pop("dt")
    return rez


def _gnews(query, n, luni):
    """Google News (RSS public, în română): acoperă bine presa locală."""
    r = requests.get("https://news.google.com/rss/search", headers={"User-Agent": "Mozilla/5.0"}, timeout=30,
                     params={"q": f"{query} when:{luni * 30}d", "hl": "ro", "gl": "RO", "ceid": "RO:ro"})
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        if len(out) >= n:
            break
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate") or "")
            data, an = dt.strftime("%d.%m.%Y"), str(dt.year)
        except Exception:
            data, an = "", ""
        out.append({"tip": "presă", "titlu": it.findtext("title") or "", "an": an, "data": data,
                    "sursa": it.findtext("source") or "", "autori": "", "url": it.findtext("link") or "",
                    "text": ""})
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def presa(query, n=15, luni=12):
    erori = []
    for sursa in (_bzi, _gnews, _gdelt):
        try:
            rez = sursa(query, n, luni)
            if rez:
                return rez
        except Exception as e:
            erori.append(f"{sursa.__name__}: {e}")
    if erori:
        raise RuntimeError("; ".join(erori))
    return []


def _llm(prompt, provider, ollama_model=None):
    if provider == "gemini" and rag.gemini_key():
        return rag._ask_gemini(prompt)
    if provider == "ollama":
        return rag._ask_ollama(prompt, ollama_model or "llama3.1")
    if provider == "claude" and rag.api_key():
        import anthropic
        c = anthropic.Anthropic(api_key=rag.api_key())
        m = c.messages.create(model="claude-haiku-5-5", max_tokens=900,
                              messages=[{"role": "user", "content": prompt}])
        return m.content[0].text
    return None


def _cuvinte_en(intrebare, provider, ollama_model, zona=""):
    """Literatura științifică e mai ales în engleză: cere modelului 3-6 cuvinte-cheie în engleză.
    Fără model, folosește cuvinte-cheie implicite despre densificare + numele zonei."""
    try:
        t = _llm("Give 3-6 English search keywords (no quotes, space separated, nothing else) for "
                 f"finding scientific papers about: {intrebare}", provider, ollama_model)
        if t:
            return re.sub(r"[\"'\n]", " ", t).strip()[:120]
    except Exception:
        pass
    z = (zona or "").translate(str.maketrans("ăâîșşțţĂÂÎȘŞȚŢ", "aaisstt" + "AAISSTT"))
    return f"urban densification building footprints cadastre {z}".strip()


def sinteza(intrebare, rezultate, provider, ollama_model=None):
    lista = "\n".join(f"[{i}] ({r['tip']}, {r.get('data') or r['an']}, {r['sursa']}) {r['titlu']}. {r['text']}"
                      for i, r in enumerate(rezultate, 1))
    prompt = ("Ești agentul de documentare al platformei URBIS GIS (cadastru și densificare urbană, Iași). "
              "Pe baza listei de surse de mai jos, scrie în română o sinteză scurtă (maximum 12 rânduri) "
              f"care răspunde la: „{intrebare}”. Separă ce spun articolele științifice de ce spune presa. "
              "După fiecare afirmație pune numărul sursei, de ex. [2]. Folosește doar informațiile din listă; "
              "dacă sursele nu răspund la întrebare, spune asta.\n\nSurse:\n" + lista)
    return _llm(prompt, provider, ollama_model)


def render(header):
    header("Agent de documentare web", "M5", "Caută articole științifice (OpenAlex) și presă (BZI, Google News) despre ce "
                                              "s-a construit într-o zonă și face o sinteză cu sursele citate")
    with st.expander("📰 Context din presă și statistică: construcții și demolări în Iași (selecție)", expanded=True):
        st.markdown(
            "| Informație | Valoare | Sursa |\n|---|---|---|\n"
            "| Autorizații de construire, clădiri rezidențiale, județul Iași, ian.–apr. 2025 | 560 (−2,3% față de 2024) | "
            "[BZI, 27.06.2025](https://www.bzi.ro/se-construieste-mai-mult-la-iasi-in-aprilie-2025-dar-cum-stam-fata-de-anul-trecut-5294271) |\n"
            "| Idem, ian.–mai 2025 | 734; urban 68 (−30), rural 666 (+23) | "
            "[INS Iași, 11.07.2025](https://iasi.insse.ro/wp-content/uploads/2018/05/CP-17-din-11-07-2025-Autorizatii-Mai-2025.pdf) |\n"
            "| Construcții ilegale identificate pe blocurile din Iași | 622: 471 șarpante, 151 mansarde | "
            "[BZI, 09.04.2026](https://www.bzi.ro/sute-de-constructii-ilegale-de-pe-blocurile-din-iasi-vor-fi-demolate-de-primarie-mihai-chirica-procedam-in-regim-de-urgenta-la-desfiintarea-lor-5510686) |\n"
            "| Prima demolare din campania de desființare | o mansardă, zona Podu Metalurgiei (iulie 2026) | "
            "[BZI, 20.07.2026](https://www.bzi.ro/primaria-iasi-incepe-desfiintarea-mansardelor-neautorizate-prima-constructie-demolata-in-zona-podu-metalurgiei-5572973) |\n"
            "| Exemplu de demolare urmată de construcție nouă (propunere, 2021) | vilă din Copou → bloc P+3, ~20 apartamente | "
            "[BZI, 19.10.2021](https://www.bzi.ro/un-dezvoltator-imobiliar-din-iasi-demoleaza-vila-din-copou-ca-sa-faca-loc-unui-bloc-proiectul-lui-corneliu-petrea-stoicescu-boutique-apartments-este-la-mana-arhitectilor-traficul-rutier-din-zo-4291409) |"
        )
        st.caption("Cifrele INS sunt la nivel de județ și provin în cea mai mare parte din mediul rural. Nu există un total "
                   "public al clădirilor demolate sau reconstruite în municipiu; platforma îl calculează la nivel de clădire "
                   "(Indicatori densificare, tipologia schimbărilor).")
    c1, c2 = st.columns([3, 1])
    q = c1.text_input("Ce cauți?", "construcții noi și densificare urbană în Iași")
    zona = c2.text_input("Zonă (opțional)", "Iași")
    c3, c4, c5 = st.columns(3)
    surse = c3.multiselect("Surse", ["Articole științifice", "Presă"], ["Articole științifice", "Presă"])
    luni = c4.slider("Presă: ultimele luni", 1, 36, 12)
    de_la = c5.number_input("Articole: din anul", 2000, datetime.now().year, 2015)
    provider = st.session_state.get("llm", rag.llm_provider())
    if not st.button("🔎 Caută", type="primary"):
        st.caption("Sursele sunt publice și gratuite. Întrebarea nu conține date din baza firmei; "
                   "sinteza folosește modelul de limbaj ales la Asistent GIS-IA.")
        return
    intrebare = f"{q} {zona}".strip()
    rez, erori = [], []
    with st.spinner("Caut în surse publice…"):
        if "Articole științifice" in surse:
            en = _cuvinte_en(intrebare, provider, st.session_state.get("ollama_model"), zona)
            try:
                rez += articole(en, de_la=int(de_la))
            except Exception as e:
                erori.append(f"OpenAlex: {e}")
        if "Presă" in surse:
            try:
                pq = " ".join(w for w in re.findall(r"\w+", intrebare) if len(w) > 3)[:100]
                rez += presa(pq, luni=luni)
            except Exception as e:
                erori.append(f"Presă: {e}")
    for e in erori:
        st.warning(f"O sursă nu a răspuns: {e}")
    if not rez:
        st.info("Nu am găsit rezultate. Încearcă alte cuvinte sau o perioadă mai lungă.")
        return
    try:
        text = sinteza(intrebare, rez, provider, st.session_state.get("ollama_model"))
    except Exception as e:
        text, _ = None, st.warning(f"Modelul de limbaj nu a răspuns: {e}")
    if text:
        st.markdown("#### Sinteză")
        st.markdown(text)
    else:
        st.info("Fără model de limbaj configurat se afișează doar lista surselor "
                "(adaugă GEMINI_API_KEY pentru sinteză gratuită).")
    st.markdown("#### Surse")
    for i, r in enumerate(rez, 1):
        meta = " · ".join(x for x in [r["tip"], str(r.get("data") or r["an"] or ""), r["sursa"], r["autori"]] if x)
        st.markdown(f"**[{i}]** [{r['titlu'] or r['url']}]({r['url']})  \n<small>{meta}</small>",
                    unsafe_allow_html=True)
# cautare_web.py - agent de căutare pe web pentru URBIS GIS
#
# Caută, pentru o zonă și o temă (de ex. „construcții noi Iași Copou”):
#   - articole științifice în OpenAlex (gratuit, fără cheie; >250 milioane de lucrări);
#   - știri de presă în Google News (RSS public, în română), cu GDELT ca rezervă; ambele gratuite, fără cheie.
# Apoi modelul de limbaj ales în platformă (Gemini gratuit, Claude sau Ollama) face o
# sinteză în română, cu trimitere la fiecare sursă [1], [2] ... Fără model, se afișează lista.

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests
import streamlit as st

import rag_iasi as rag

UA = {"User-Agent": "URBIS-GIS/1.0 (TUIASI; cercetare)"}


def _abstract(inv):
    """OpenAlex dă rezumatul ca index inversat {cuvânt: [poziții]}."""
    if not inv:
        return ""
    pos = sorted((p, w) for w, ps in inv.items() for p in ps)
    return " ".join(w for _, w in pos)


@st.cache_data(ttl=3600, show_spinner=False)
def articole(query, n=8, de_la=2015):
    r = requests.get("https://api.openalex.org/works", headers=UA, timeout=30, params={
        "search": query, "per-page": n, "sort": "relevance_score:desc",
        "filter": f"from_publication_date:{de_la}-01-01",
        "select": "title,publication_year,doi,id,primary_location,authorships,abstract_inverted_index"})
    r.raise_for_status()
    out = []
    for w in r.json().get("results", []):
        src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
        aut = ", ".join(a["author"]["display_name"] for a in (w.get("authorships") or [])[:3])
        out.append({"tip": "articol", "titlu": w.get("title") or "", "an": w.get("publication_year"),
                    "sursa": src, "autori": aut, "url": w.get("doi") or w.get("id"),
                    "text": _abstract(w.get("abstract_inverted_index"))[:900]})
    return out


def _gdelt(query, n, luni):
    params = {"query": query + " sourcelang:romanian", "mode": "ArtList", "format": "json",
              "maxrecords": n, "sort": "DateDesc", "timespan": f"{luni * 30}d"}
    for incercare in range(2):  # GDELT cere cel mult o cerere la 5 secunde
        r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", headers=UA, timeout=30, params=params)
        if r.status_code == 429 and incercare == 0:
            time.sleep(6)
            continue
        r.raise_for_status()
        break
    try:
        arts = r.json().get("articles", [])
    except ValueError:  # răspuns text simplu la interogări prea scurte
        return []
    out = []
    for a in arts:
        d = a.get("seendate", "")[:8]
        out.append({"tip": "presă", "titlu": a.get("title", ""), "an": d[:4],
                    "data": f"{d[6:8]}.{d[4:6]}.{d[:4]}" if len(d) == 8 else "",
                    "sursa": a.get("domain", ""), "autori": "", "url": a.get("url", ""), "text": ""})
    return out


def _gnews(query, n, luni):
    """Google News (RSS public, în română): acoperă bine presa locală."""
    r = requests.get("https://news.google.com/rss/search", headers={"User-Agent": "Mozilla/5.0"}, timeout=30,
                     params={"q": f"{query} when:{luni * 30}d", "hl": "ro", "gl": "RO", "ceid": "RO:ro"})
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        if len(out) >= n:
            break
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate") or "")
            data, an = dt.strftime("%d.%m.%Y"), str(dt.year)
        except Exception:
            data, an = "", ""
        out.append({"tip": "presă", "titlu": it.findtext("title") or "", "an": an, "data": data,
                    "sursa": it.findtext("source") or "", "autori": "", "url": it.findtext("link") or "",
                    "text": ""})
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def presa(query, n=15, luni=12):
    erori = []
    for sursa in (_gnews, _gdelt):
        try:
            rez = sursa(query, n, luni)
            if rez:
                return rez
        except Exception as e:
            erori.append(f"{sursa.__name__}: {e}")
    if erori:
        raise RuntimeError("; ".join(erori))
    return []


def _llm(prompt, provider, ollama_model=None):
    if provider == "gemini" and rag.gemini_key():
        return rag._ask_gemini(prompt)
    if provider == "ollama":
        return rag._ask_ollama(prompt, ollama_model or "llama3.1")
    if provider == "claude" and rag.api_key():
        import anthropic
        c = anthropic.Anthropic(api_key=rag.api_key())
        m = c.messages.create(model="claude-haiku-5-5", max_tokens=900,
                              messages=[{"role": "user", "content": prompt}])
        return m.content[0].text
    return None


def _cuvinte_en(intrebare, provider, ollama_model, zona=""):
    """Literatura științifică e mai ales în engleză: cere modelului 3-6 cuvinte-cheie în engleză.
    Fără model, folosește cuvinte-cheie implicite despre densificare + numele zonei."""
    try:
        t = _llm("Give 3-6 English search keywords (no quotes, space separated, nothing else) for "
                 f"finding scientific papers about: {intrebare}", provider, ollama_model)
        if t:
            return re.sub(r"[\"'\n]", " ", t).strip()[:120]
    except Exception:
        pass
    z = (zona or "").translate(str.maketrans("ăâîșşțţĂÂÎȘŞȚŢ", "aaisstt" + "AAISSTT"))
    return f"urban densification building footprints cadastre {z}".strip()


def sinteza(intrebare, rezultate, provider, ollama_model=None):
    lista = "\n".join(f"[{i}] ({r['tip']}, {r.get('data') or r['an']}, {r['sursa']}) {r['titlu']}. {r['text']}"
                      for i, r in enumerate(rezultate, 1))
    prompt = ("Ești agentul de documentare al platformei URBIS GIS (cadastru și densificare urbană, Iași). "
              "Pe baza listei de surse de mai jos, scrie în română o sinteză scurtă (maximum 12 rânduri) "
              f"care răspunde la: „{intrebare}”. Separă ce spun articolele științifice de ce spune presa. "
              "După fiecare afirmație pune numărul sursei, de ex. [2]. Folosește doar informațiile din listă; "
              "dacă sursele nu răspund la întrebare, spune asta.\n\nSurse:\n" + lista)
    return _llm(prompt, provider, ollama_model)


def render(header):
    header("Agent de documentare web", "M5", "Caută articole științifice (OpenAlex) și presă (Google News, GDELT) despre ce "
                                              "s-a construit într-o zonă și face o sinteză cu sursele citate")
    with st.expander("📰 Context din presă și statistică: construcții și demolări în Iași (selecție)", expanded=True):
        st.markdown(
            "| Informație | Valoare | Sursa |\n|---|---|---|\n"
            "| Autorizații de construire, clădiri rezidențiale, județul Iași, ian.–apr. 2025 | 560 (−2,3% față de 2024) | "
            "[BZI, 27.06.2025](https://www.bzi.ro/se-construieste-mai-mult-la-iasi-in-aprilie-2025-dar-cum-stam-fata-de-anul-trecut-5294271) |\n"
            "| Idem, ian.–mai 2025 | 734; urban 68 (−30), rural 666 (+23) | "
            "[INS Iași, 11.07.2025](https://iasi.insse.ro/wp-content/uploads/2018/05/CP-17-din-11-07-2025-Autorizatii-Mai-2025.pdf) |\n"
            "| Construcții ilegale identificate pe blocurile din Iași | 622: 471 șarpante, 151 mansarde | "
            "[BZI, 09.04.2026](https://www.bzi.ro/sute-de-constructii-ilegale-de-pe-blocurile-din-iasi-vor-fi-demolate-de-primarie-mihai-chirica-procedam-in-regim-de-urgenta-la-desfiintarea-lor-5510686) |\n"
            "| Prima demolare din campania de desființare | o mansardă, zona Podu Metalurgiei (iulie 2026) | "
            "[BZI, 20.07.2026](https://www.bzi.ro/primaria-iasi-incepe-desfiintarea-mansardelor-neautorizate-prima-constructie-demolata-in-zona-podu-metalurgiei-5572973) |\n"
            "| Exemplu de demolare urmată de construcție nouă (propunere, 2021) | vilă din Copou → bloc P+3, ~20 apartamente | "
            "[BZI, 19.10.2021](https://www.bzi.ro/un-dezvoltator-imobiliar-din-iasi-demoleaza-vila-din-copou-ca-sa-faca-loc-unui-bloc-proiectul-lui-corneliu-petrea-stoicescu-boutique-apartments-este-la-mana-arhitectilor-traficul-rutier-din-zo-4291409) |"
        )
        st.caption("Cifrele INS sunt la nivel de județ și provin în cea mai mare parte din mediul rural. Nu există un total "
                   "public al clădirilor demolate sau reconstruite în municipiu; platforma îl calculează la nivel de clădire "
                   "(Indicatori densificare, tipologia schimbărilor).")
    c1, c2 = st.columns([3, 1])
    q = c1.text_input("Ce cauți?", "construcții noi și densificare urbană în Iași")
    zona = c2.text_input("Zonă (opțional)", "Iași")
    c3, c4, c5 = st.columns(3)
    surse = c3.multiselect("Surse", ["Articole științifice", "Presă"], ["Articole științifice", "Presă"])
    luni = c4.slider("Presă: ultimele luni", 1, 36, 12)
    de_la = c5.number_input("Articole: din anul", 2000, datetime.now().year, 2015)
    provider = st.session_state.get("llm", rag.llm_provider())
    if not st.button("🔎 Caută", type="primary"):
        st.caption("Sursele sunt publice și gratuite. Întrebarea nu conține date din baza firmei; "
                   "sinteza folosește modelul de limbaj ales la Asistent GIS-IA.")
        return
    intrebare = f"{q} {zona}".strip()
    rez, erori = [], []
    with st.spinner("Caut în surse publice…"):
        if "Articole științifice" in surse:
            en = _cuvinte_en(intrebare, provider, st.session_state.get("ollama_model"), zona)
            try:
                rez += articole(en, de_la=int(de_la))
            except Exception as e:
                erori.append(f"OpenAlex: {e}")
        if "Presă" in surse:
            try:
                pq = " ".join(w for w in re.findall(r"\w+", intrebare) if len(w) > 3)[:100]
                rez += presa(pq, luni=luni)
            except Exception as e:
                erori.append(f"Presă: {e}")
    for e in erori:
        st.warning(f"O sursă nu a răspuns: {e}")
    if not rez:
        st.info("Nu am găsit rezultate. Încearcă alte cuvinte sau o perioadă mai lungă.")
        return
    try:
        text = sinteza(intrebare, rez, provider, st.session_state.get("ollama_model"))
    except Exception as e:
        text, _ = None, st.warning(f"Modelul de limbaj nu a răspuns: {e}")
    if text:
        st.markdown("#### Sinteză")
        st.markdown(text)
    else:
        st.info("Fără model de limbaj configurat se afișează doar lista surselor "
                "(adaugă GEMINI_API_KEY pentru sinteză gratuită).")
    st.markdown("#### Surse")
    for i, r in enumerate(rez, 1):
        meta = " · ".join(x for x in [r["tip"], str(r.get("data") or r["an"] or ""), r["sursa"], r["autori"]] if x)
        st.markdown(f"**[{i}]** [{r['titlu'] or r['url']}]({r['url']})  \n<small>{meta}</small>",
                    unsafe_allow_html=True)
