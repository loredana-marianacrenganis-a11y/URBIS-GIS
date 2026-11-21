# swipe.py - hartă de comparație cu bară glisantă (swipe): în stânga și în dreapta barei se pot
# alege imagini satelitare din ani diferiți (Esri World Imagery Wayback, din 2014) sau harta OSM,
# iar peste ele clădirile din baza URBIS pentru anul ales pe fiecare parte.

import json

import streamlit as st
import streamlit.components.v1 as components
from shapely.geometry import box, mapping

import db
import harta_inaltimi

# ediții Wayback folosite dacă lista completă nu se poate încărca în browser
WAYBACK_REZERVA = [(10, "2014-02-20"), (577, "2017-01-11"), (119, "2020-10-14"), (12428, "2024-06-06")]


def _round(o, nd=6):
    if isinstance(o, (list, tuple)):
        return [_round(x, nd) for x in o]
    return round(o, nd) if isinstance(o, float) else o


@st.cache_data(show_spinner=False, ttl=3600)
def cladiri_geojson(an, bounds, _ver):
    """Clădirile valabile în anul `an` din dreptunghiul zonei (WGS84), ca GeoJSON compact."""
    g = db.buildings_at(an)
    if g.empty:
        return {"type": "FeatureCollection", "features": []}
    g = g.to_crs(4326)
    g = g[g.geometry.representative_point().within(box(*bounds))]
    g = g.assign(geometry=g.geometry.simplify(0.000002, preserve_topology=True))
    feats = []
    for r in g.itertuples():
        niv = getattr(r, "niveluri", None)
        try:
            niv = int(float(niv)) if niv is not None and niv == niv else None
        except (TypeError, ValueError):
            niv = None
        gm = mapping(r.geometry)
        feats.append({"type": "Feature", "geometry": {"type": gm["type"], "coordinates": _round(gm["coordinates"])},
                      "properties": {"t": getattr(r, "tip", "") or "", "n": niv}})
    return {"type": "FeatureCollection", "features": feats}


HTML = r"""
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  #wrap{position:relative;height:__H__px}
  #map{position:absolute;inset:0;border-radius:8px}
  #bar{position:absolute;top:0;bottom:0;width:4px;margin-left:-2px;background:#fff;z-index:1000;
       box-shadow:0 0 4px rgba(0,0,0,.6);cursor:ew-resize;touch-action:none}
  #knob{position:absolute;top:50%;left:50%;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;
        background:#1f2a4d;color:#fff;display:flex;align-items:center;justify-content:center;font-size:16px;
        box-shadow:0 1px 4px rgba(0,0,0,.5);user-select:none}
  .ctl{position:absolute;top:10px;z-index:1001;background:rgba(255,255,255,.95);border-radius:6px;padding:4px 6px;
       box-shadow:0 1px 4px rgba(0,0,0,.3);font-size:12px;max-width:45%}
  .ctl b{display:block;margin-bottom:2px}
  .ctl select{font-size:12px;max-width:100%}
  #cl{left:54px} #cr{right:10px}
  .tag{position:absolute;bottom:22px;z-index:1001;background:rgba(31,42,77,.85);color:#fff;font-size:12px;
       padding:2px 8px;border-radius:4px}
  #tl{left:10px} #tr{right:10px}
</style>
<div id="wrap">
  <div id="map"></div>
  <div id="bar"><div id="knob">&#x21C6;</div></div>
  <div class="ctl" id="cl"><b>Stânga</b><select id="selL"></select></div>
  <div class="ctl" id="cr"><b>Dreapta</b><select id="selR"></select></div>
  <div class="tag" id="tl"></div><div class="tag" id="tr"></div>
</div>
<script>
const CFG = __CFG__;
const map = L.map('map', {zoomControl: true, maxZoom: 19}).fitBounds([[CFG.bounds[1], CFG.bounds[0]], [CFG.bounds[3], CFG.bounds[2]]]);
for (const p of ['L', 'R']) {
  map.createPane('tile' + p).style.zIndex = 200;
  map.createPane('vec' + p).style.zIndex = 400;
}
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const WB = r => 'https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/' + r + '/{z}/{y}/{x}';
const ATTR_ESRI = 'Imagini &copy; Esri, Maxar, Earthstar Geographics';
let sources = {
  'esri': {label: 'Satelit actual (Esri)', url: ESRI, attr: ATTR_ESRI},
  'osm': {label: 'Hartă OpenStreetMap', url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', attr: '&copy; contribuitorii OpenStreetMap'}
};
function addWayback(list) {
  list.sort((a, b) => a[1] < b[1] ? -1 : 1);
  for (const [rel, d] of list) sources['wb' + rel] = {label: 'Satelit Esri Wayback ' + d, url: WB(rel),
    attr: ATTR_ESRI + ' (World Imagery Wayback, ediția ' + d + ')'};
}
const tiles = {L: null, R: null};
function setTile(p, key) {
  if (tiles[p]) map.removeLayer(tiles[p]);
  const s = sources[key];
  tiles[p] = L.tileLayer(s.url, {pane: 'tile' + p, maxZoom: 19, maxNativeZoom: 19, attribution: s.attr}).addTo(map);
  document.getElementById(p === 'L' ? 'tl' : 'tr').textContent =
    s.label + (CFG.years[p] ? ' · clădiri ' + CFG.years[p] : '');
  clip();
}
function fillSelects(defL, defR) {
  for (const [p, def] of [['L', defL], ['R', defR]]) {
    const sel = document.getElementById('sel' + p);
    sel.innerHTML = '';
    for (const [k, s] of Object.entries(sources)) {
      const o = document.createElement('option'); o.value = k; o.textContent = s.label; sel.appendChild(o);
    }
    sel.value = sources[def] ? def : 'esri';
    sel.onchange = () => setTile(p, sel.value);
    setTile(p, sel.value);
  }
}
// clădirile pe fiecare parte (fiecare în panoul ei, cu randare proprie, ca să poată fi tăiată de bară)
const esc = v => String(v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
function levelColor(n) {
  if (!n) return '#ffd400';
  const h = n <= 5 ? 60 * (n - 1) / 4 : 60 + 240 * (Math.min(n, 27) - 5) / 22;
  return 'hsl(' + h + ',90%,55%)';
}
for (const p of ['L', 'R']) {
  if (!CFG.data[p]) continue;
  L.geoJSON(CFG.data[p], {
    pane: 'vec' + p, renderer: L.canvas({pane: 'vec' + p}),
    style: f => ({color: levelColor(f.properties.n), weight: 1.2, fillOpacity: CFG.fill, opacity: .95}),
    onEachFeature: (f, l) => l.bindTooltip(esc(f.properties.t || 'construcție') +
      (f.properties.n ? ' · ' + (f.properties.n === 1 ? 'P' : 'P+' + (f.properties.n - 1)) : ''), {sticky: true})
  }).addTo(map);
}
// bara de swipe
let ratio = 0.5;
const wrap = document.getElementById('wrap'), bar = document.getElementById('bar');
function clip() {
  const size = map.getSize(), x = Math.round(size.x * ratio);
  bar.style.left = x + 'px';
  const nw = map.containerPointToLayerPoint([0, 0]), se = map.containerPointToLayerPoint(size);
  const cx = nw.x + x;
  const left = 'rect(' + [nw.y, cx, se.y, nw.x].join('px,') + 'px)';
  const right = 'rect(' + [nw.y, se.x, se.y, cx].join('px,') + 'px)';
  map.getPane('tileL').style.clip = left;  map.getPane('vecL').style.clip = left;
  map.getPane('tileR').style.clip = right; map.getPane('vecR').style.clip = right;
}
let drag = false;
function setFromEvent(e) {
  const r = wrap.getBoundingClientRect();
  const cx = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
  ratio = Math.min(0.98, Math.max(0.02, cx / r.width)); clip();
}
bar.addEventListener('pointerdown', e => { drag = true; map.dragging.disable(); bar.setPointerCapture(e.pointerId); e.preventDefault(); });
bar.addEventListener('pointermove', e => { if (drag) setFromEvent(e); });
bar.addEventListener('pointerup', () => { drag = false; map.dragging.enable(); });
map.on('move zoom resize', clip);
// lista completă a edițiilor Wayback (publică); dacă nu se încarcă, se folosesc câteva ediții fixe
fetch('https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json')
  .then(r => r.json())
  .then(j => {
    const list = Object.entries(j).map(([k, v]) => [+k, ((v.itemTitle || '').match(/(\d{4}-\d{2}-\d{2})/) || [])[1]])
      .filter(x => x[1]);
    addWayback(list);
    fillSelects('wb' + list.sort((a, b) => a[1] < b[1] ? -1 : 1)[0][0], 'esri');
  })
  .catch(() => { addWayback(CFG.fallback.slice()); fillSelects('wb' + CFG.fallback[0][0], 'esri'); });
</script>
"""


def render(header, db_version):
    header("Comparație cu swipe", "M3", "Trage bara albă ca să compari două momente: imagini satelitare din ani "
                                       "diferiți, cu clădirile din baza URBIS desenate peste")
    zone = [z for z in harta_inaltimi.AREAS if not z.startswith("Municipiul")]
    ys = db.years()
    c1, c2, c3, c4 = st.columns([1.4, 1, 1, 0.9])
    zona = c1.selectbox("Zona", zone, index=zone.index("Centru – Copou") if "Centru – Copou" in zone else 0,
                        key="sw_zona")
    fara = "fără clădiri"
    opt = [fara] + [str(y) for y in ys]
    yl = c2.selectbox("Clădiri în stânga (anul)", opt, index=1 if len(ys) else 0, key="sw_yl")
    yr = c3.selectbox("Clădiri în dreapta (anul)", opt, index=len(opt) - 1, key="sw_yr")
    fill = c4.slider("Umplere clădiri", 0.0, 0.8, 0.15, 0.05, key="sw_fill")
    bounds = harta_inaltimi.AREAS[zona]
    ver = db_version()
    data, years = {}, {}
    with st.spinner("Pregătesc clădirile..."):
        for p, y in (("L", yl), ("R", yr)):
            if y != fara:
                data[p] = cladiri_geojson(int(y), tuple(bounds), ver)
                years[p] = y
    cfg = {"bounds": list(bounds), "data": data, "years": years, "fill": fill, "fallback": WAYBACK_REZERVA}
    height = 620
    # datele OSM sunt editate public: „</” se neutralizează ca să nu poată închide blocul <script>
    page = HTML.replace("__CFG__", json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")).replace("__H__", str(height))
    if hasattr(st, "iframe"):
        st.iframe(page, height=height + 6)
    else:
        components.html(page, height=height + 6)
    n = {p: len(d["features"]) for p, d in data.items()}
    st.caption(
        "În fiecare parte alegi fundalul din lista de pe hartă: imagini satelitare Esri World Imagery Wayback "
        "(ediții arhivate din 2014 până azi), imaginea satelitară actuală sau harta OpenStreetMap. Data ediției "
        "Wayback este data publicării hărții, iar imaginea poate fi mai veche. Clădirile sunt colorate după numărul "
        "de niveluri (galben = necunoscut)"
        + (f"; stânga: {n['L']} clădiri" if "L" in n else "") + (f"; dreapta: {n['R']} clădiri" if "R" in n else "")
        + ". Dacă baza conține mai mulți ani (de ex. 2011 și 2024), alege anii diferiți ca să vezi construcțiile "
          "noi și demolate.")
