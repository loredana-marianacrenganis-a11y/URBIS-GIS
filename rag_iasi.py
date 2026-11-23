
import os
import re
from pathlib import Path

import requests
import numpy as np
from fastembed import TextEmbedding
import anthropic

RAW = "https://raw.githubusercontent.com/subdense/dashboard/master/"

DOCS = {
    "Studies/IasiStudy.md": None,
    "Processes/FLAIR-HUB2BF.md": None,
    "Processes/ComputeBuildingBinarization.md":
        "Rezultate intermediare; NU vor fi publicate open source.",
    "Processes/ComputeBuildingSegmentation.md":
        "Rezultate intermediare; NU vor fi publicate open source.",
    "Processes/GeneralizationBuildingFootprins.md":
        "Rezultate finale; VOR fi publicate open source.",
    "Data/Datasources.md": None,
    "Processes/BuildingsFootprintsAnnotation.md": None,
    "Processes/ManualBuildingFootprintsCorrection.md": None,
    "Processes/ComputeBuildingQuality.md": None,
    "Processes/BuildingQualityAssessmentIndicatorsComputation.md": None,
    "Processes/ComputeBuildingChange.md": None,
}

NOTES = [
    "Plan de publicare (iulie 2026): seturile RO-IAS-Building-2024 (rezultatul "
    "FLAIR-HUB2BF) și RO-IAS-Building-GT-2024 (corectarea manuală a FLAIR-HUB2BF) "
    "sunt gata și urmează să fie publicate în depozitul de date deschise "
    "recherche.data.gouv.fr. Codurile și celelalte seturi de date urmează după aceea.",
    "Statutul datelor: datele studiului Iași nu sunt încă open source, urmează să fie. "
    "Pentru prezentarea studiului către parteneri (de ex. ANCPI) se recomandă "
    "paginile FLAIR-HUB2BF.md și IasiStudy.md.",
    "Pașii de binarizare, segmentare și generalizare ai FLAIR-HUB2BF au fost "
    "documentați în august 2026, cu codul publicat în repository-ul dashboard. "
    "Binarizarea și segmentarea produc rezultate intermediare (nepublicate), "
    "generalizarea produce rezultatele finale (publicate open source).",
    "Procesul ComputeBuildingQuality aplicat în studiul Iași diferă de descrierea "
    "generală din pagină; detaliile sunt în curs de clarificare în echipă.",
]


def load_chunks():
    chunks = [("Note echipă (email 2026)", n) for n in NOTES]
    for path, status in DOCS.items():
        text = requests.get(RAW + path, timeout=30).text
        title = text.splitlines()[0].lstrip("# ").strip()
        if status:
            chunks.append((path, f"{title} - statutul rezultatelor: {status}"))
        for section in re.split(r"\n(?=##)", text):
            section = section.strip()
            if len(section) <= 30:
                continue
            heading = section.splitlines()[0]
            for part in split_long(section):
                if len(part.strip()) < 40:
                    continue
                if not part.startswith("#"):
                    part = heading + "\n" + part
                chunks.append((path, f"[{title}]\n{part.strip()}"))
    return chunks


def split_long(section, limit=800):
    if len(section) < limit:
        return [section]
    out = []
    for item in re.split(r"\n(?=\* )", section):
        if len(item) < limit:
            out.append(item)
            continue
        subs = re.split(r"\n(?=\s{2,}\* )", item)
        if len(subs) == 1:
            subs = re.split(r"\n(?=\d+\. )|\n\s*\n", item)
        parent = subs[0].splitlines()[0]
        out.append(subs[0])
        out += [parent + "\n" + s for s in subs[1:]]
    return out


chunks = load_chunks()
print(f"Am încărcat {len(chunks)} secțiuni din {len(DOCS)} documente.")

embedder = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


def embed(texts):
    v = np.array(list(embedder.embed(texts)))
    return v / np.linalg.norm(v, axis=1, keepdims=True)


vectors = embed([t for _, t in chunks])


_extra_cache = {}


def _extra_vectors(extra):
    key = tuple(t for _, t in extra)
    if key not in _extra_cache:
        _extra_cache.clear()
        _extra_cache[key] = embed([t for _, t in extra]) if extra else None
    return _extra_cache[key]


_ext = ([], None)


def set_external(items):
    global _ext
    items = list(items)
    _ext = (items, embed([t for _, t in items]) if items else None)
    return len(items)


def external_count():
    return len(_ext[0])


def retrieve(question, k=4, extra=None, external=True):
    q = embed([question])[0]
    pool = list(chunks)
    scores = list(vectors @ q)
    if extra:
        pool += list(extra)
        scores += list(_extra_vectors(extra) @ q)
    ext_items, ext_vec = _ext
    if external and ext_vec is not None:
        pool += ext_items
        scores += list(ext_vec @ q)
    top = np.argsort(np.array(scores))[::-1][:k]
    return [pool[i] for i in top]


def split_text(src, text, size=700):
    out, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) > size and cur:
            out.append((src, cur.strip()))
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        out.append((src, cur.strip()))
    return out


_client = None


def api_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    f = Path(__file__).with_name("cheie_api.txt")
    if not key and f.exists():
        key = f.read_text(encoding="utf-8").strip()
    return key


OLLAMA_URL = os.environ.get("URBIS_OLLAMA_URL", "http://localhost:11434")


def gemini_key():
    return os.environ.get("GEMINI_API_KEY", "").strip()


def llm_provider():
    p = os.environ.get("URBIS_LLM", "").strip().lower()
    if p:
        return p
    if not api_key() and gemini_key():
        return "gemini"
    return "claude"


GEMINI_MODEL = os.environ.get("URBIS_GEMINI_MODEL", "gemini-flash-latest")
GEMINI_REZERVE = [m.strip() for m in os.environ.get(
    "URBIS_GEMINI_REZERVE", "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.6-flash").split(",") if m.strip()]
_pauza_model = {}
_cache_raspunsuri = {}


class ServiciuOcupat(Exception):
    def __init__(self, tip, asteapta=0, detalii=""):
        super().__init__(detalii or tip)
        self.tip, self.asteapta, self.detalii = tip, int(asteapta or 0), detalii


def _retry_delay(r, implicit=60):
    try:
        for d in r.json().get("error", {}).get("details", []):
            v = str(d.get("retryDelay", ""))
            if v.endswith("s"):
                return max(5, int(float(v[:-1])) + 1)
    except Exception:
        pass
    try:
        return max(5, int(r.headers.get("Retry-After", implicit)))
    except Exception:
        return implicit


def _ask_gemini(prompt):
    import time
    import requests
    if prompt in _cache_raspunsuri:
        return _cache_raspunsuri[prompt]
    modele = [GEMINI_MODEL] + [m for m in GEMINI_REZERVE if m != GEMINI_MODEL]
    acum = time.time()
    disponibile = [m for m in modele if _pauza_model.get(m, 0) <= acum]
    if not disponibile:
        raise ServiciuOcupat("limita", min(_pauza_model[m] for m in modele) - acum)
    tip, asteapta, detalii = "indisponibil", 30, ""
    for m in disponibile:
        for incercare in range(2):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                    headers={"x-goog-api-key": gemini_key(), "Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": 0.1, "maxOutputTokens": 800}},
                    timeout=60)
            except requests.RequestException as e:
                detalii = str(e)[:200]
                break
            if r.status_code == 200:
                try:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts).strip()
                except Exception:
                    text = ""
                if text:
                    if len(_cache_raspunsuri) > 300:
                        _cache_raspunsuri.clear()
                    _cache_raspunsuri[prompt] = text
                    return text
                detalii = "răspuns gol"
                break
            if r.status_code in (401, 403):
                raise ServiciuOcupat("cheie", 0, f"HTTP {r.status_code}")
            if r.status_code == 429:
                w = _retry_delay(r)
                _pauza_model[m] = time.time() + w
                tip, asteapta, detalii = "limita", w, f"{m}: HTTP 429"
                break
            if r.status_code in (500, 502, 503, 504) and incercare == 0:
                time.sleep(1.5)
                continue
            if r.status_code in (500, 502, 503, 504):
                _pauza_model[m] = time.time() + 20
            detalii = f"{m}: HTTP {r.status_code}"
            break
    if tip == "limita":
        rest = [_pauza_model.get(m, 0) - time.time() for m in modele]
        asteapta = max(5, min(x for x in rest if x > 0)) if any(x > 0 for x in rest) else asteapta
    raise ServiciuOcupat(tip, asteapta, detalii)


def raspuns_fara_model(found, limita=4):
    rand = []
    for src, txt in found[:limita]:
        t = " ".join(re.sub(r"#+\s*|\*\*", "", str(txt)).split())
        rand.append(f"- **{src}**: {t[:420]}{'…' if len(t) > 420 else ''}")
    return "\n".join(rand)


def mesaj_serviciu(e):
    if e.tip == "cheie":
        return "⚠️ Cheia pentru serviciul online nu este acceptată (verifică GEMINI_API_KEY în Secrets)."
    if e.tip == "limita":
        m = max(1, round(e.asteapta / 60))
        return (f"⏳ Serviciul online gratuit a atins limita de întrebări; se poate reîncerca în circa "
                f"{m} {'minut' if m == 1 else 'minute'}. Dacă limita zilnică s-a epuizat, se resetează a doua zi.")
    return "⏳ Serviciul online este momentan supraîncărcat (eroare temporară); reîncearcă peste câteva momente."


def ollama_models():
    try:
        import requests
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def _ask_ollama(prompt, model):
    import requests
    r = requests.post(f"{OLLAMA_URL}/api/chat", timeout=300, json={
        "model": model, "stream": False, "options": {"temperature": 0.1},
        "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["message"]["content"]


def answer(question, extra=None, db_context=None, provider=None, ollama_model=None,
           external=True, prioritar=None):
    provider = (provider or llm_provider()).lower()
    prioritar = list(prioritar or [])
    found = retrieve(question, k=5 if external and external_count() else 4, extra=extra, external=external)
    found = prioritar + [f for f in found if f not in prioritar]
    if db_context:
        found = [("Baza de date URBIS", db_context)] + found
    if provider == "claude" and not api_key():
        return ("⚠️ Asistentul nu are cheie API. Pe Streamlit Cloud adaug-o la "
                "Settings → Secrets; local, pune-o în cheie_api.txt, sau alege varianta locală. "
                "Mai jos vezi totuși secțiunile găsite în documente."), found
    context = "\n\n---\n\n".join(f"Sursa: {src}\n{txt}" for src, txt in found)
    prompt = (
        "Ești asistentul platformei URBIS GIS (densificare urbană, Iași; voucher "
        "UEFISCDI TUIASI). Răspunde în română, concis, doar pe baza contextului. După fiecare "
        "afirmație indică sursa în paranteze drepte, de ex. [IasiStudy.md] sau "
        "[Baza de date URBIS]. Pentru sursele publice (registrul MDLPA, urban-gis.ro) dă și "
        "linkul din context. Dacă informația nu e în context, spune clar că nu există "
        "în datele platformei; nu inventa cifre.\n\n"
        f"Context:\n{context}\n\nÎntrebare: {question}"
    )
    if provider == "gemini":
        if not gemini_key():
            return ("⚠️ Lipsește cheia GEMINI_API_KEY (gratuită, de pe aistudio.google.com). "
                    "Mai jos vezi totuși secțiunile găsite în documente."), found
        try:
            return _ask_gemini(prompt), found
        except ServiciuOcupat as e:
            return (mesaj_serviciu(e) + "\n\nPână atunci, iată ce am găsit, fără formulare automată:\n\n"
                    + raspuns_fara_model(found)), found
        except Exception as e:
            return f"⚠️ Serviciul online nu a răspuns: {str(e)[:200]}", found
    if provider == "ollama":
        model = ollama_model or os.environ.get("URBIS_OLLAMA_MODEL", "llama3.1")
        try:
            return _ask_ollama(prompt, model), found
        except Exception as e:
            return f"⚠️ Varianta locală nu răspunde la {OLLAMA_URL}: {e}.", found
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=api_key())
    msg = _client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text, found


def ask(question):
    text, found = answer(question)
    sources = sorted({src for src, _ in found})
    return text + "\n\nSurse căutate: " + ", ".join(sources)


if __name__ == "__main__":
    print("Exemple: Ce pași are FLAIR-HUB2BF? Ce toleranță s-a folosit la Douglas-Peucker?")
    while True:
        q = input("\nÎntrebare (Enter gol = ieșire): ").strip()
        if not q:
            break
        print("\n" + ask(q))
