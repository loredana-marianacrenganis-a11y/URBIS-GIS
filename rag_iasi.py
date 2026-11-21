# rag_iasi.py - RAG simplu peste documentația fluxului FLAIR-HUB2BF pentru studiul Iași
#
# Descarcă fișierele .md din documentația tehnică a fluxului FLAIR-HUB2BF, le împarte pe
# secțiuni (titluri markdown), face embeddings și răspunde la întrebări cu un LLM,
# indicând fișierul sursă.
#
# Instalare:  pip install fastembed numpy anthropic requests
# Cheie API:  variabila ANTHROPIC_API_KEY (pe Streamlit Cloud: Settings -> Secrets),
#             sau fișierul cheie_api.txt lângă script (local)
# Rulare:     python rag_iasi.py

import os
import re
from pathlib import Path

import requests
import numpy as np
from fastembed import TextEmbedding
import anthropic

RAW = "https://raw.githubusercontent.com/subdense/dashboard/master/"

# Documentele folosite + statutul rezultatelor (notele echipei)
DOCS = {
    "Studies/IasiStudy.md": None,
    "Processes/FLAIR-HUB2BF.md": None,
    "Processes/ComputeBuildingBinarization.md":
        "Rezultate intermediare; NU vor fi publicate open source.",
    "Processes/ComputeBuildingSegmentation.md":
        "Rezultate intermediare; NU vor fi publicate open source.",
    "Processes/GeneralizationBuildingFootprins.md":
        "Rezultate finale; VOR fi publicate open source.",
    # pagini adăugate de Ana-Maria Raimond (email iulie 2026)
    "Data/Datasources.md": None,
    "Processes/BuildingsFootprintsAnnotation.md": None,
    "Processes/ManualBuildingFootprintsCorrection.md": None,
    "Processes/ComputeBuildingQuality.md": None,
    "Processes/BuildingQualityAssessmentIndicatorsComputation.md": None,
    "Processes/ComputeBuildingChange.md": None,
}

# Informații din corespondența echipei (iulie–august 2026) care nu sunt în pagini
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


# 1. Descarcă documentele și le împarte pe secțiuni (## titlu)
def load_chunks():
    chunks = [("Note echipă (email 2026)", n) for n in NOTES]  # (sursa, text)
    for path, status in DOCS.items():
        text = requests.get(RAW + path, timeout=30).text
        title = text.splitlines()[0].lstrip("# ").strip()
        if status:
            chunks.append((path, f"{title} - statutul rezultatelor: {status}"))
        # împarte la fiecare titlu de nivel 2 (## ...)
        for section in re.split(r"\n(?=##)", text):
            section = section.strip()
            if len(section) <= 30:
                continue
            heading = section.splitlines()[0]
            for part in split_long(section):
                if len(part.strip()) < 40:  # doar un titlu, fără conținut
                    continue
                if not part.startswith("#"):
                    part = heading + "\n" + part
                # titlul documentului păstrează contextul fiecărei bucăți
                chunks.append((path, f"[{title}]\n{part.strip()}"))
    return chunks


# Modelul de embedding citește doar începutul unui text lung (~128 de tokeni),
# așa că secțiunile lungi se taie pe elementele de listă de nivel 1 ("* ").
# Un element încă prea lung se taie pe sub-elemente ("  * "), iar fiecare bucată
# păstrează prima linie a elementului-părinte (ex. numele setului de date).
def split_long(section, limit=800):
    if len(section) < limit:
        return [section]
    out = []
    for item in re.split(r"\n(?=\* )", section):
        if len(item) < limit:
            out.append(item)
            continue
        subs = re.split(r"\n(?=\s{2,}\* )", item)
        if len(subs) == 1:  # fără sub-elemente: liste numerotate sau paragrafe
            subs = re.split(r"\n(?=\d+\. )|\n\s*\n", item)
        parent = subs[0].splitlines()[0]
        out.append(subs[0])
        out += [parent + "\n" + s for s in subs[1:]]
    return out


chunks = load_chunks()
print(f"Am încărcat {len(chunks)} secțiuni din {len(DOCS)} documente.")

# 2. Embeddings (model multilingv: întrebi în română, documentele sunt în engleză).
#    fastembed rulează același model ca sentence-transformers, dar în format ONNX
#    (~0,2 GB, fără PyTorch), ca să încapă în memoria mică a găzduirii gratuite.
embedder = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


def embed(texts):
    v = np.array(list(embedder.embed(texts)))
    return v / np.linalg.norm(v, axis=1, keepdims=True)


vectors = embed([t for _, t in chunks])


# 3. Caută secțiunile cele mai relevante (documentația + documentele încărcate în platformă)
_extra_cache = {}


def _extra_vectors(extra):
    key = tuple(t for _, t in extra)
    if key not in _extra_cache:
        _extra_cache.clear()
        _extra_cache[key] = embed([t for _, t in extra]) if extra else None
    return _extra_cache[key]


def retrieve(question, k=4, extra=None):
    q = embed([question])[0]
    pool = list(chunks)
    scores = list(vectors @ q)
    if extra:
        pool += list(extra)
        scores += list(_extra_vectors(extra) @ q)
    top = np.argsort(np.array(scores))[::-1][:k]
    return [pool[i] for i in top]


def split_text(src, text, size=700):
    """Împarte un document încărcat în fragmente pentru căutare (pe paragrafe)."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) > size and cur:
            out.append((src, cur.strip()))
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        out.append((src, cur.strip()))
    return out


# 4. Generează răspunsul cu sursele
_client = None  # creat la prima întrebare, ca harta să meargă și fără cheie API


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
    """'claude' (prin API), 'gemini' (Google, cheie gratuită din aistudio.google.com)
    sau 'ollama' (model local, datele nu părăsesc calculatorul)."""
    p = os.environ.get("URBIS_LLM", "").strip().lower()
    if p:
        return p
    if not api_key() and gemini_key():
        return "gemini"
    return "claude"


GEMINI_MODEL = os.environ.get("URBIS_GEMINI_MODEL", "gemini-flash-latest")


def _ask_gemini(prompt):
    import requests
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": gemini_key(), "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.1, "maxOutputTokens": 800}},
        timeout=60)
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


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


def answer(question, extra=None, db_context=None, provider=None, ollama_model=None):
    """Întoarce (răspuns, secțiunile găsite). extra: fragmente din documentele încărcate
    în platformă (fără cele cu date personale); db_context: rezumatul agregat al bazei de date
    URBIS (sursa [Baza de date URBIS]); provider: 'claude' sau 'ollama'."""
    provider = (provider or llm_provider()).lower()
    found = retrieve(question, extra=extra)
    if db_context:
        found = [("Baza de date URBIS", db_context)] + found
    if provider == "claude" and not api_key():
        return ("⚠️ Asistentul nu are cheie API. Pe Streamlit Cloud adaug-o la "
                "Settings → Secrets; local, pune-o în cheie_api.txt, sau alege un model local (Ollama). "
                "Mai jos vezi totuși secțiunile găsite în documente."), found
    context = "\n\n---\n\n".join(f"Sursa: {src}\n{txt}" for src, txt in found)
    prompt = (
        "Ești asistentul GIS-IA al platformei URBIS GIS (densificare urbană, Iași; voucher "
        "UEFISCDI TUIASI). Răspunde în română, concis, doar pe baza contextului. După fiecare "
        "afirmație indică sursa în paranteze drepte, de ex. [IasiStudy.md] sau "
        "[Baza de date URBIS]. Dacă informația nu e în context, spune clar că nu există "
        "în datele platformei; nu inventa cifre.\n\n"
        f"Context:\n{context}\n\nÎntrebare: {question}"
    )
    if provider == "gemini":
        if not gemini_key():
            return ("⚠️ Lipsește cheia GEMINI_API_KEY (gratuită, de pe aistudio.google.com). "
                    "Mai jos vezi totuși secțiunile găsite în documente."), found
        try:
            return _ask_gemini(prompt), found
        except Exception as e:
            return f"⚠️ Gemini nu a răspuns: {e}", found
    if provider == "ollama":
        model = ollama_model or os.environ.get("URBIS_OLLAMA_MODEL", "llama3.1")
        try:
            return _ask_ollama(prompt, model), found
        except Exception as e:
            return (f"⚠️ Modelul local ({model}) nu răspunde la {OLLAMA_URL}: {e}. Pornește Ollama "
                    "(ollama serve) și descarcă modelul (ollama pull " + model + ")."), found
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
