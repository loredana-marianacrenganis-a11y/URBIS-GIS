# Iași 2011 → 2024 (site public)

Site static care arată cum s-a schimbat fondul construit al Iașiului între 2011 și 2024:
construcții noi, demolări, reconstrucții, extinderi și supraetajări, pe hartă (cu bară de
comparație) și pe cartiere. Proiect PN-IV-P7-7.1-CI-2025-0106, contract 29CI/2026.

## Fișiere

- `index.html` – site-ul (MapLibre GL JS; tot calculul se face în browser)
- `cartiere.geojson` – limitele celor 17 cartiere ale municipiului Iași
- `urbis_2011_2024.json` – rezultatul comparației (se adaugă după încărcarea straturilor)

## Cum se publică datele

1. Deschide site-ul, secțiunea **Încarcă date**.
2. Alege stratul de clădiri 2011 și stratul 2024 (shapefile arhivat .zip cu .shp, .shx, .dbf, .prj,
   sau GeoJSON; Stereo 70 sau WGS84).
3. Apasă **Compară 2011 cu 2024**, apoi **Descarcă fișierul pentru publicare**.
4. Pe GitHub, în acest folder `docs/`: Add file → Upload files → `urbis_2011_2024.json` → Commit.

După un minut, site-ul îl încarcă automat pentru toți vizitatorii. Fișierul conține doar
contururile clădirilor, numărul de niveluri și categoria de schimbare (fără proprietari sau
numere cadastrale).

## Găzduire

GitHub Pages: Settings → Pages → Deploy from a branch → `main` / `docs`.
