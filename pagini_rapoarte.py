# pagini_rapoarte.py - rapoartele pe construcție și pe zonă, harta straturilor proprii
# (preluate din DensIași și integrate în URBIS GIS)

from common import *  # noqa: F401,F403
import osm
import raport
import raport_cladire

def page_constructie_osm():
    left, right = st.columns([1, 1.5], gap="large")
    with left:
        st.markdown("#### 1 · Găsește clădirea")
        q = st.text_input("Adresă din Iași (opțional, ca să centrezi harta)",
                          placeholder="ex. Strada Lascăr Catargi 10", key="bld_q")
        if st.button("🔍 Caută adresa", key="bld_search") and q:
            try:
                res = osm.geocode(q, limit=1)
                if res:
                    st.session_state.bld_center = (res[0][1], res[0][2])
                else:
                    st.warning("Nu am găsit adresa în Iași.")
            except Exception as e:
                st.error(f"Căutarea nu a funcționat: {e}")
        st.caption("Apoi **dă clic pe clădire** pe hartă. Clădirea aleasă apare cu roșu.")

        sel = st.session_state.get("bld_sel")
        if sel:
            row = sel["buildings"].loc[sel["idx"]]
            st.success(f"Clădire selectată: **{raport.TIP_RO.get(row['tip'], row['tip'])}**"
                       + (f" – {row['adresa']}" if row.get("adresa") else "")
                       + f" ({row['osm_id']})")
        st.markdown("#### 2 · Straturi de evoluție 2011–2024 (opțional)")
        evo_b, evo_bf = evo_selector("bld")
        st.markdown("#### 3 · Generează raportul")
        ai_b = st.checkbox("Include interpretarea AI (necesită cheie API)",
                           value=bool(rag.api_key()), disabled=not rag.api_key(), key="ai_b")
        go_b = st.button("🏠 Generează raportul construcției", type="primary",
                         disabled=not sel, width="stretch")

    with right:
        center = st.session_state.get("bld_center")
        if sel:
            g = sel["buildings"].loc[[sel["idx"]]]
            c = g.geometry.iloc[0].centroid
            m = base_map((c.y, c.x), 18)
            folium.GeoJson(sel["buildings"], name="Clădiri OSM",
                           style_function=lambda f: {"color": "#4f84c4", "weight": 1,
                                                     "fillOpacity": 0.15}).add_to(m)
            folium.GeoJson(g, name="Clădirea selectată",
                           style_function=lambda f: {"color": "#d7263d", "weight": 3,
                                                     "fillOpacity": 0.45}).add_to(m)
        else:
            m = base_map(center or (47.165, 27.585), 18 if center else 14)
        out = st_folium(m, height=560, use_container_width=True, key="harta_cladire",
                        returned_objects=["last_clicked"]) or {}
        click = out.get("last_clicked")
        if click and click != st.session_state.get("bld_click"):
            st.session_state.bld_click = click
            lat, lon = click["lat"], click["lng"]
            try:
                with st.spinner("Caut clădirea în OpenStreetMap..."):
                    bs = fetch_osm(circle(lat, lon, 200).wkt)
                idx = raport_cladire.pick_building(bs, lat, lon)
                if idx is None:
                    st.warning("Nu există nicio clădire OSM în punctul ales. Mărește harta și "
                               "dă clic exact pe o clădire.")
                else:
                    st.session_state.bld_sel = {"buildings": bs, "idx": idx}
                    st.session_state.pop("bld_rep", None)
                    st.rerun()
            except Exception as e:
                st.error(f"Serviciul OpenStreetMap nu a răspuns: {e}")

    if go_b and sel:
        ind = raport_cladire.compute(sel["idx"], sel["buildings"], evo_b, evo_bf)
        row = sel["buildings"].loc[sel["idx"]]
        title = (row.get("adresa") or row.get("nume") or
                 f"{ind['tip'].capitalize()} {ind['osm_id']}")
        ai_text, ai_src = None, None
        if ai_b:
            prompt = ("Scrie o interpretare de 120–180 de cuvinte despre această construcție "
                      "și rolul ei în densificarea zonei, în contextul studiului de densificare Iași. "
                      "Folosește doar cifrele date; nu inventa. Date: "
                      + raport_cladire.summary_text(ind))
            with st.spinner("Asistentul AI interpretează datele..."):
                try:
                    ai_text, found = rag.answer(prompt)
                    ai_src = [s for s, _ in found]
                except Exception as e:
                    st.warning(f"Interpretarea AI nu a reușit: {e}")
        with st.spinner("Generez PDF-ul..."):
            st.session_state.bld_rep = {
                "pdf": raport_cladire.build_pdf(ind, title, ai_text, ai_src),
                "plan": raport_cladire.plan_figure(ind, dpi=110),
                "ctx": raport_cladire.context_figure(ind, dpi=110),
                "title": title, "ai": ai_text,
                "ind": {k: v for k, v in ind.items() if k not in
                        ("context", "context_zone", "geom_s70", "plan_s70")},
                "ctx_ind": {k: v for k, v in ind["context"].items()
                            if k not in ("cladiri", "evolutie_gdf")}}

    rep = st.session_state.get("bld_rep")
    if rep:
        ind = rep["ind"]
        st.divider()
        st.subheader(f"Raport: {rep['title']}")
        st.download_button("⬇️ Descarcă raportul PDF", rep["pdf"],
                           file_name="raport_constructie_iasi.pdf", mime="application/pdf",
                           type="primary", key="dl_bld")
        k = st.columns(5)
        k[0].metric("Amprentă", f"{ind['amprenta']:.0f} m²")
        k[1].metric("Dimensiuni", f"{ind['lungime']:.0f} × {ind['latime']:.0f} m")
        k[2].metric("Regim înălțime", ind["regim"])
        k[3].metric("Arie desfășurată", f"{ind['arie_desf']:.0f} m²" if ind["arie_desf"] else "–")
        k[4].metric("Vecini < 50 m", ind["vecini_nr"])
        c1, c2 = st.columns(2)
        c1.image(rep["plan"], caption="Plan cu laturi și colțuri numerotate (Stereo 70)")
        c2.image(rep["ctx"], caption="Vecinătatea pe o rază de 100 m")
        st.markdown("**Coordonatele colțurilor (Stereo 70)**")
        st.dataframe(pd.DataFrame(ind["varfuri"], columns=["X (m)", "Y (m)"],
                                  index=range(1, len(ind["varfuri"]) + 1)), width="stretch")
        if "evolutie" in ind:
            st.markdown("**Schimbări 2011–2024:** " +
                        (", ".join(ind["evolutie"]) or "niciuna pe amprenta clădirii"))
        if rep["ai"]:
            st.markdown("**Interpretare AI**")
            st.write(rep["ai"])



def page_zona():
    left, right = st.columns([1, 1.5], gap="large")
    zone, zone_name = None, None

    with left:
        st.markdown("#### 1 · Alege zona")
        mode = st.radio("Mod de selecție",
                        ["Zonă de studiu Iași", "Adresă + rază", "Desenez pe hartă",
                         "Încarc parcela (fișier)"], label_visibility="collapsed")

        if mode == "Zonă de studiu Iași":
            exact = study_zone_polygons()
            name = st.selectbox("Zona", list(exact) or list(ZONES))
            if name in exact:
                zone, zone_name = exact[name], f"Zona de studiu {name}"
            else:
                r = st.slider("Rază în jurul centrului zonei (m)", 200, 1500, 500, 50)
                lat, lon = ZONES[name]
                zone = circle(lat, lon, r)
                zone_name = f"{name} – cerc de {r} m"
                st.caption("Centrul zonei e aproximativ. Adaugă zone_iasi.geojson pentru "
                           "limitele exacte ale zonelor de studiu.")
            desc = zone_text(name)
            if desc:
                st.info(desc[:400])

        elif mode == "Adresă + rază":
            q = st.text_input("Adresă din Iași", placeholder="ex. Bulevardul Carol I 11")
            if st.button("🔍 Caută") and q:
                try:
                    st.session_state.geo = osm.geocode(q)
                    if not st.session_state.geo:
                        st.warning("Nu am găsit adresa în Iași. Încearcă altă formulare.")
                except Exception as e:
                    st.error(f"Căutarea nu a funcționat: {e}")
            results = st.session_state.get("geo") or []
            if results:
                pick = st.selectbox("Rezultat", range(len(results)),
                                    format_func=lambda i: results[i][0][:90])
                r = st.slider("Rază (m)", 100, 1000, 300, 50)
                label, lat, lon = results[pick]
                zone, zone_name = circle(lat, lon, r), f"{label.split(',')[0]} – rază {r} m"

        elif mode == "Desenez pe hartă":
            st.caption("Folosește instrumentul ⬟ (poligon) sau ▭ (dreptunghi) din stânga "
                       "hărții. Ultimul desen devine zona raportului.")
            drawn = st.session_state.get("drawn")
            if drawn is not None:
                zone, zone_name = drawn, "Zonă desenată pe hartă"

        else:
            st.caption("Conturul parcelei de la topograf sau din QGIS: GeoJSON, GeoPackage, "
                       "shapefile arhivat (.zip), DXF sau KML. Stereo 70 e recunoscut automat.")
            f = st.file_uploader("Fișierul parcelei",
                                 type=["geojson", "json", "gpkg", "zip", "dxf", "kml"])
            if f:
                try:
                    gdf, crs = read_layer(f.name, f.getvalue())
                    zone = zone_polygon_from_file(gdf)
                    zone_name = f"Parcela din {f.name}"
                    if zone is None:
                        st.error("Fișierul nu conține un contur închis (poligon).")
                except Exception as e:
                    st.error(f"Nu am putut citi fișierul: {e}")

        if zone is not None:
            ha = area_ha(zone)
            st.markdown(f"**{zone_name}** · {ha:.2f} ha")
            if ha > MAX_ZONE_HA:
                st.error(f"Zona are {ha:.0f} ha. Limita este {MAX_ZONE_HA} ha (5 km²); "
                         "alege o zonă mai mică.")

        st.markdown("#### 2 · Straturi de evoluție 2011–2024 (opțional)")
        evo_gdf, evo_field = evo_selector("zona")

        st.markdown("#### 3 · Generează raportul")
        use_ai = st.checkbox("Include interpretarea AI (necesită cheie API)",
                             value=bool(rag.api_key()), disabled=not rag.api_key())
        ok = zone is not None and area_ha(zone) <= MAX_ZONE_HA
        go = st.button("📄 Generează raportul", type="primary", disabled=not ok,
                       width="stretch")

    with right:
        if zone is not None:
            c = zone.centroid
            m = base_map((c.y, c.x), 15)
            folium.GeoJson(zone.__geo_interface__, name="Zona raportului",
                           style_function=lambda f: {"color": NAVY, "weight": 3,
                                                     "fillOpacity": 0.08,
                                                     "dashArray": "6 4"}).add_to(m)
            minx, miny, maxx, maxy = zone.bounds
            m.fit_bounds([[miny, minx], [maxy, maxx]])
        else:
            m = base_map()
        if mode == "Desenez pe hartă":
            plugins.Draw(position="topleft",
                         draw_options={"polyline": False, "circle": False, "marker": False,
                                       "circlemarker": False}).add_to(m)
        folium.LayerControl().add_to(m)
        out = st_folium(m, height=560, use_container_width=True, key=f"harta_raport_{mode}",
                        returned_objects=["last_active_drawing"]) or {}
        drawing = out.get("last_active_drawing")
        if mode == "Desenez pe hartă" and drawing and \
                drawing.get("geometry", {}).get("type") == "Polygon":
            new = shape(drawing["geometry"])
            old = st.session_state.get("drawn")
            if old is None or not new.equals(old):
                st.session_state.drawn = new
                st.rerun()

    # --- generare
    if go:
        try:
            with st.spinner("Descarc clădirile din OpenStreetMap..."):
                b = fetch_osm(zone.wkt)
        except Exception as e:
            st.error(f"Serviciul OpenStreetMap (Overpass) nu a răspuns: {e}. "
                     "Încearcă din nou peste un minut.")
            st.stop()
        ind = raport.compute(zone, b, evo_gdf, evo_field)
        ai_text, ai_src = None, None
        if use_ai:
            prompt = ("Scrie o interpretare de 150–220 de cuvinte a densificării urbane din "
                      "zona de mai jos, legând indicatorii de metodologia și zonele "
                      "studiului de densificare Iași. Nu inventa valori; folosește doar cifrele "
                      "date și contextul. Indicatori: " + raport.summary_text(ind, zone_name))
            with st.spinner("Asistentul AI interpretează indicatorii..."):
                try:
                    ai_text, found = rag.answer(prompt)
                    ai_src = [s for s, _ in found]
                except Exception as e:
                    st.warning(f"Interpretarea AI nu a reușit: {e}")
        with st.spinner("Generez PDF-ul..."):
            pdf = raport.build_pdf(zone, zone_name, ind, ai_text, ai_src)
            fig = raport.map_figure(zone, ind, dpi=110)
        st.session_state.rep = {"ind": {k: v for k, v in ind.items()
                                        if k not in ("cladiri", "evolutie_gdf")},
                                "pdf": pdf, "map": fig, "name": zone_name,
                                "ai": ai_text, "ai_src": ai_src}

    rep = st.session_state.get("rep")
    if rep:
        ind = rep["ind"]
        st.divider()
        st.subheader(f"Raport: {rep['name']}")
        st.download_button("⬇️ Descarcă raportul PDF", rep["pdf"],
                           file_name="raport_densificare_iasi.pdf", mime="application/pdf",
                           type="primary", key="dl_zona")
        if ind["nr_cladiri"]:
            k = st.columns(5)
            k[0].metric("Suprafață", f"{ind['zona_ha']:.1f} ha")
            k[1].metric("Clădiri (OSM)", f"{ind['nr_cladiri']}")
            k[2].metric("Densitate", f"{ind['densitate_ha']:.1f} /ha")
            k[3].metric("POT estimat", f"{ind['pot']:.1f} %")
            k[4].metric("CUT estimat", f"{ind['cut']:.2f}")
        else:
            st.warning("Nu există clădiri OSM în această zonă.")
        c1, c2 = st.columns([1.2, 1])
        c1.image(rep["map"], caption="Clădiri după regim de înălțime (OSM)")
        with c2:
            if ind["nr_cladiri"]:
                st.markdown("**Tipuri de clădiri**")
                st.dataframe(ind["tipuri"].rename(columns={"cladiri": "clădiri",
                                                           "amprenta_m2": "amprentă (m²)"})
                             .round(0), width="stretch")
            if "evolutie" in ind:
                st.markdown("**Schimbări 2011–2024**")
                st.dataframe(ind["evolutie"].round(0), width="stretch")
            else:
                st.caption("Secțiunea 2011–2024 se completează după încărcarea stratului "
                           "de evoluție.")
        if rep["ai"]:
            st.markdown("**Interpretare AI**")
            st.write(rep["ai"])


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _osm_layer(kind, zone_wkt):
    from shapely import wkt as _wkt
    import osm as _osm
    z = _wkt.loads(zone_wkt)
    return _osm.fetch_roads(z) if kind == "roads" else _osm.fetch_poi(z)


def osm_context_panel():
    """Straturi de context din OpenStreetMap: rețeaua stradală și punctele de interes."""
    import db as _db
    from shapely.geometry import box as _box
    zs = _db.zones()
    opts = {"Iași (întreg municipiul)": _box(27.50, 47.12, 27.67, 47.21)}
    if len(zs):
        z4 = zs.to_crs(4326)
        for r in z4.itertuples():
            opts[f"{r.nume} ({r.tip})"] = r.geometry
    zname = st.selectbox("Zona", list(opts), key="osm_ctx_zone")
    c1, c2 = st.columns(2)
    for col, kind, label in [(c1, "roads", "🛣️ Străzi"), (c2, "poi", "🏫 Puncte de interes (școli, spitale, servicii)")]:
        if col.button(label, key=f"osm_ctx_{kind}", width="stretch"):
            with st.spinner("Descarc din OpenStreetMap..."):
                try:
                    gdf = _osm_layer(kind, opts[zname].wkt)
                except Exception as e:
                    st.error(f"OpenStreetMap nu răspunde: {e}")
                    gdf = None
            if gdf is not None:
                key = f"OSM {'străzi' if kind == 'roads' else 'puncte de interes'} – {zname}"
                st.session_state.layers[key] = {"gdf": gdf, "crs": "EPSG:4326", "visible": True,
                                                "color_by": "tip" if kind == "roads" else "categorie"}
                st.success(f"{key}: {len(gdf)} obiecte. Date © contribuitorii OpenStreetMap (ODbL).")


def page_straturi():
    with st.expander("🗂️ Straturile mele (încărcare și stil)", expanded=not st.session_state.layers):
        layers_panel()
    with st.expander("🌐 Straturi de context din OpenStreetMap (străzi, puncte de interes)"):
        osm_context_panel()
    m = base_map()
    zones_fg = folium.FeatureGroup(name="Zone de studiu", show=True)
    if ZONES_FILE.exists():
        folium.GeoJson(
            json.loads(ZONES_FILE.read_text(encoding="utf-8")),
            style_function=lambda f: {"color": "#d7263d", "weight": 3, "fillOpacity": 0.08,
                                      "dashArray": "6 4"},
            tooltip=folium.GeoJsonTooltip(fields=["name"], labels=False),
        ).add_to(zones_fg)
    else:
        for name, (lat, lon) in ZONES.items():
            folium.CircleMarker(
                [lat, lon], radius=11, color="#d7263d", fill=True, fill_opacity=0.35,
                tooltip=name,
                popup=folium.Popup(f"<b>{name}</b><br>{zone_text(name)[:300]}", max_width=300),
            ).add_to(zones_fg)
    zones_fg.add_to(m)

    bounds = []
    for i, (key, lyr) in enumerate(st.session_state.layers.items()):
        if not lyr["visible"] or lyr["gdf"].empty:
            continue
        gdf = lyr["gdf"]
        if len(gdf) > MAX_FEATURES:
            st.warning(f"{key}: afișez primele {MAX_FEATURES:,} din {len(gdf):,} obiecte "
                       "(toate sunt în tabelul de atribute).".replace(",", "."))
            gdf = gdf.iloc[:MAX_FEATURES]
        fields = [c for c in gdf.columns if c != gdf.geometry.name][:12]
        base = PALETTE[i % len(PALETTE)]
        cmap = color_map(lyr["gdf"], lyr["color_by"]) if lyr["color_by"] else None
        field = lyr["color_by"]

        def style(f, base=base, cmap=cmap, field=field):
            c = cmap.get(f["properties"].get(field), "#999") if cmap else base
            return {"color": c, "fillColor": c, "weight": 1.2, "fillOpacity": 0.45,
                    "radius": 5}

        folium.GeoJson(
            gdf, name=key, style_function=style,
            highlight_function=lambda f: {"weight": 3, "color": "#ffea00", "fillOpacity": 0.7},
            marker=folium.CircleMarker(radius=5),
            tooltip=folium.GeoJsonTooltip(fields=fields[:4]) if fields else None,
            popup=folium.GeoJsonPopup(fields=fields) if fields else None,
        ).add_to(m)
        bounds.append(gdf.total_bounds)
        if cmap:
            items = "".join(f"<div><span style='background:{c};width:12px;height:12px;"
                            f"display:inline-block;margin-right:6px'></span>{v}</div>"
                            for v, c in cmap.items())
            st.markdown(f"**Legendă – {key} ({field})**")
            st.markdown(f"<div style='font-size:0.9em'>{items}</div>", unsafe_allow_html=True)

    if bounds:
        bb = pd.DataFrame(bounds).agg({0: "min", 1: "min", 2: "max", 3: "max"})
        m.fit_bounds([[bb[1], bb[0]], [bb[3], bb[2]]])

    plugins.Fullscreen(title="Ecran complet").add_to(m)
    plugins.MeasureControl(position="topleft", primary_length_unit="meters",
                           primary_area_unit="sqmeters").add_to(m)
    plugins.Draw(export=True, position="topleft",
                 draw_options={"circlemarker": False}).add_to(m)
    plugins.MousePosition(position="bottomright", separator=" | ", num_digits=5,
                          prefix="Lat | Lon:").add_to(m)
    plugins.MiniMap(toggle_display=True, position="bottomleft").add_to(m)
    plugins.Geocoder(collapsed=True, position="topright",
                     placeholder="Caută o adresă...").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    out = st_folium(m, height=620, use_container_width=True, key="harta",
                    returned_objects=["last_object_clicked_tooltip",
                                      "last_active_drawing", "all_drawings"]) or {}

    col_sel, col_draw = st.columns(2)
    with col_sel:
        st.subheader("🔎 Obiect selectat")
        feat = out.get("last_active_drawing")
        clicked = out.get("last_object_clicked_tooltip")
        props = (feat or {}).get("properties") or {}
        props = {k: v for k, v in props.items() if k not in ("style", "highlight")}
        if props:
            st.dataframe(pd.DataFrame(props.items(), columns=["Câmp", "Valoare"]),
                         hide_index=True, width="stretch")
            question = ("Explică, pe baza documentației studiului Iași, ce înseamnă acest "
                        f"obiect și atributele lui: {json.dumps(props, ensure_ascii=False)}")
        elif clicked in ZONES or (clicked and ZONES_FILE.exists()):
            st.markdown(f"**{clicked}**")
            question = f"Descrie zona {clicked} din studiul Iași."
        else:
            st.caption("Dă clic pe o clădire, un poligon sau o zonă de studiu.")
            question = None
        if question and st.button("🤖 Întreabă asistentul despre selecție"):
            text, found = run_question(question)
            st.markdown(text)
            if found:
                show_sources(found)

    with col_draw:
        st.subheader("✏️ Selecție spațială")
        drawings = [d for d in (out.get("all_drawings") or [])
                    if d.get("geometry", {}).get("type") in ("Polygon", "MultiPolygon")]
        if not drawings:
            st.caption("Desenează un poligon pe hartă ca să vezi ce obiecte conține.")
        else:
            area = gpd.GeoDataFrame.from_features(drawings, crs=4326)
            st.markdown(f"Suprafață desenată: **{area.to_crs(3844).area.sum():,.0f} m²**"
                        .replace(",", "."))
            rows = []
            for key, lyr in st.session_state.layers.items():
                inside = gpd.sjoin(lyr["gdf"], area[["geometry"]], predicate="intersects")
                inside = lyr["gdf"].loc[inside.index.unique()]
                row = {"Strat": key, "Obiecte": len(inside)}
                if inside.geom_type.isin(["Polygon", "MultiPolygon"]).any():
                    row["Arie totală (m²)"] = round(inside.to_crs(3844).area.sum())
                if lyr["color_by"]:
                    row["Pe categorii"] = ", ".join(
                        f"{k}: {v}" for k, v in inside[lyr["color_by"]].value_counts().items())
                rows.append(row)
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            else:
                st.caption("Încarcă un strat ca să faci selecție spațială pe el.")

    if st.session_state.layers:
        st.subheader("📋 Tabel de atribute")
        key = st.selectbox("Strat", list(st.session_state.layers))
        gdf = st.session_state.layers[key]["gdf"]
        table = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
        if gdf.geom_type.isin(["Polygon", "MultiPolygon"]).any():
            table["arie_m2"] = gdf.to_crs(3844).area.round(1).values
        filt = st.text_input("Filtrează (text căutat în orice coloană)")
        if filt:
            mask = table.astype(str).apply(lambda c: c.str.contains(filt, case=False)).any(axis=1)
            table = table[mask]
        st.dataframe(table, width="stretch", height=300)
        st.caption(f"{len(table):,} rânduri".replace(",", "."))
        c1, c2 = st.columns(2)
        c1.download_button("⬇️ Descarcă CSV", table.to_csv(index=False).encode("utf-8"),
                           f"{Path(key).stem}.csv", "text/csv")
        c2.download_button("⬇️ Descarcă GeoJSON", gdf.to_json().encode("utf-8"),
                           f"{Path(key).stem}.geojson", "application/geo+json")

