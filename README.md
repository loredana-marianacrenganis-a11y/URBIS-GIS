# URBIS GIS – platformă GIS pentru densificarea urbană (Iași)

Prototip funcțional realizat în cadrul proiectului „Dezvoltarea arhitecturii GIS pentru management
cadastral și analiză urbană sustenabilă”, contract 29CI/2026, cod PN-IV-P7-7.1-CI-2025-0106
(cec de inovare UEFISCDI; TUIASI × HURJUI & ASOCIAȚII S.R.L.). Acoperă toate cele 8 module din cererea de finanțare.

| Modul | Pagina din aplicație | Livrabil |
|---|---|---|
| M1 Bază de date spațio-temporală | Model de date (ERD, schema PostGIS, statistici) | WP1 |
| M2 Import și validare | Import și validare (construcții, parcele, zone, documente) | WP2 |
| M3 Hartă Web-GIS | Hartă și căutare (cursor de timp, 3 tematici, fișă, istoric) · Harta înălțimilor (OSM) · Comparație cu swipe (imagini satelitare 2014–prezent, clădiri pe ani) | WP4 |
| M4 Interogare spațială | Interogare spațială (filtre, zonă, export CSV/GeoJSON) | WP4 |
| M5 Asistent GIS | Asistent (baza de date + documente fără date personale + documentația FLAIR-HUB2BF, cu surse) + surse publice: registrul MDLPA al documentațiilor de urbanism (PUG/PUZ/PUD, județul Iași) și ghidurile urban-gis.ro · Documentare web (articole OpenAlex și presă locală, cu sinteză și surse) | WP3 |
| M6 Indicatori și rapoarte | Tablou de bord (grafice pe ani și cartiere) · Indicatori densificare (POT, CUT, noi/demolate, tipuri de schimbări: fuziune, divizare, recompunere) · Rapoarte PDF · Neconcordanțe cadastrale | WP4 |
| M7 Mapathon | Mapathon (validare pe telefon + aprobare) | WP4 |
| M8 Administrare și jurnal | Jurnal și statistici · Valori de referință pentru impact (2026 vs 2027) · roluri | WP0, Etapa 2 |
| WP0 Diseminare | Despre proiect | WP0 |

## Pornire
Windows: dublu-clic pe `porneste_platforma.bat` → http://localhost:8502.
Altfel: `pip install -r requirements.txt` și `streamlit run app.py`.

## Primii pași
1. **Hartă și căutare**: dacă baza e goală, importă inventarul OSM al zonei pilot (sau mergi la Import).
2. **Import și validare**: importă straturile de evoluție pe rând, cu anul fiecăruia (ex. 2011, apoi 2024,
   „acoperă complet perimetrul” bifat) – platforma detectează construcțiile noi, extinse și demolate.
3. Importă zonele (UTR, zone de studiu) și documentele (normative MDLPA, PUZ) pentru indicatori și asistent.

## Fișiere
- `app.py` – interfața și modulele · `db.py` – baza de date spațio-temporală (SQLite, `urbis.sqlite`)
- `schema_postgis.sql` – aceeași schemă pentru PostgreSQL + PostGIS (livrabil WP1)
- `estimare.py` – estimarea nivelurilor lipsă · `rag_iasi.py` – asistentul
- `raport.py`, `raport_cladire.py` – rapoarte PDF · `osm.py` – OpenStreetMap · `harta_inaltimi.py`
- `common.py`, `pagini_rapoarte.py` – funcții și pagini comune · `swipe.py` – harta de comparație cu swipe
- `cautare_web.py` – documentarea web · `surse_publice.py` – surse publice pentru asistent (registrul MDLPA, urban-gis.ro)
- `tablou.py` – tabloul de bord cu grafice · `docs/` – site-ul public Iași 2011 → 2024 (hartă, grafice, cartiere)

## Straturi de context
Straturi de lucru › „Straturi de context din OpenStreetMap”: rețeaua stradală și punctele de interes (școli,
spitale, servicii publice) pentru Iași sau pentru o zonă salvată. Hărțile de acoperire a terenului (FLAIR-HUB) se
încarcă tot aici, ca strat de lucru (.gpkg).

## Protecția datelor
La importul documentelor, bifează „Conține date personale” (implicit pentru extrasele CF): documentul rămâne în baza
de date, dar nu intră în indexul asistentului. Asistentul primește doar fragmentele relevante din documentele tehnice
și un rezumat agregat al bazei (fără date despre persoane).

## Configurare
- Cheia pentru asistent: în Secrets pe Streamlit Cloud (local: variabile de mediu sau `cheie_api.txt`);
  numele variabilelor sunt în `rag_iasi.py`. Asistentul poate rula și local, fără cheie.
- Parolă pentru rolurile Operator/Administrator (opțional): variabila `URBIS_PAROLA` sau în Secrets.
- Pe găzduirea gratuită Streamlit Cloud, fișierul `urbis.sqlite` nu e permanent: pentru producție,
  folosiți serverul TUIASI cu PostgreSQL + PostGIS (`schema_postgis.sql`).

Date © contribuitorii OpenStreetMap (ODbL). Rapoartele sunt informative și nu înlocuiesc documentele
oficiale. Acest proiect a fost finanțat printr-un grant al Ministerului Educației și Cercetării,
CCCDI – UEFISCDI, cod proiect PN-IV-P7-7.1-CI-2025-0106, în cadrul PNCDI IV.
