# estimare.py - estimarea numărului de niveluri pentru clădirile fără date în OSM.
#
# Un model de tip gradient boosting învață, din clădirile CU niveluri cunoscute din zona
# încărcată, legătura dintre niveluri și: tipul clădirii, amprentă, formă (alungire),
# densitatea vecinătății, nivelurile vecinilor cunoscuți și distanța față de centru.
# Precizia se măsoară prin validare încrucișată și se afișează utilizatorului.
# Dacă sunt prea puține clădiri cu niveluri cunoscute, se folosesc reguli simple pe tip.

import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from scipy.spatial import cKDTree
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import KFold

STEREO70 = 3844
# Piața Unirii, Iași, transformată în Stereo 70
CENTER_S70 = Transformer.from_crs(4326, STEREO70, always_xy=True).transform(27.5815, 47.1650)
MIN_TRAIN = 60
K_NEIGH = 8

LOW = {"garage", "garages", "shed", "roof", "carport", "service", "hut", "kiosk",
       "greenhouse", "toilets", "cabin", "barn", "farm_auxiliary", "transformer_tower"}
HOUSES = {"house", "detached", "semidetached_house", "terrace", "bungalow", "villa"}


def _features(gdf):
    g = gdf.to_crs(STEREO70)
    area = g.area.values
    # alungirea = latura lungă / latura scurtă a dreptunghiului minim rotit
    rect = shapely.minimum_rotated_rectangle(g.geometry.values)
    w, h = np.ones(len(g)), np.ones(len(g))
    for i, r in enumerate(rect):
        cc = np.asarray(r.exterior.coords) if r.geom_type == "Polygon" else None
        if cc is not None and len(cc) >= 4:
            w[i] = max(np.hypot(*(cc[1] - cc[0])), 0.1)
            h[i] = max(np.hypot(*(cc[2] - cc[1])), 0.1)
    c = g.geometry.representative_point()
    xy = np.c_[c.x.values, c.y.values]
    tree = cKDTree(xy)
    dens = tree.query_ball_point(xy, r=100, return_length=True)
    X = pd.DataFrame({
        "log_arie": np.log1p(area),
        "alungire": np.maximum(w, h) / np.minimum(w, h),
        "densitate_100m": dens,
        "dist_centru": np.hypot(xy[:, 0] - CENTER_S70[0], xy[:, 1] - CENTER_S70[1]),
        "tip": pd.Categorical(gdf["tip"].fillna("yes").values),
    })
    return X, xy


def _neighbour_levels(xy, levels):
    """Mediana nivelurilor celor mai apropiați K vecini CU niveluri cunoscute (fără clădirea
    însăși), pe o rază de 250 m."""
    known = ~np.isnan(levels)
    out = np.full(len(xy), np.nan)
    if known.sum() < 3:
        return out
    kxy, klev = xy[known], levels[known]
    tree = cKDTree(kxy)
    k = min(K_NEIGH + 1, len(kxy))
    d, idx = tree.query(xy, k=k, distance_upper_bound=250)
    for i in range(len(xy)):
        sel = idx[i][(d[i] > 0.01) & np.isfinite(d[i])][:K_NEIGH]
        if len(sel):
            out[i] = np.median(klev[sel])
    return out


def _rules(gdf):
    """Estimare de rezervă, pe tip și amprentă (tipologii uzuale în Iași)."""
    area = gdf.to_crs(STEREO70).area.values
    out = []
    for t, a in zip(gdf["tip"].fillna("yes"), area):
        if t in LOW or a < 25:
            out.append(1)
        elif t in HOUSES:
            out.append(1 if a < 120 else 2)
        elif t in ("apartments", "residential", "dormitory"):
            out.append(4 if a < 250 else (5 if a < 900 else 9))
        elif a > 1500:
            out.append(3)
        else:
            out.append(2 if a >= 150 else 1)
    return np.array(out, dtype=float)


def estimate(gdf, niv_col="niv"):
    """Adaugă coloanele 'niv_final' și 'niv_sursa' ('OSM' / 'estimat') și întoarce
    (gdf, info), unde info descrie metoda și precizia."""
    gdf = gdf.copy()
    levels = gdf[niv_col].astype(float).values
    known = ~np.isnan(levels)
    X, xy = _features(gdf)
    X["niv_vecini"] = _neighbour_levels(xy, levels)
    info = {"cunoscute": int(known.sum()), "estimate": int((~known).sum())}

    if known.sum() >= MIN_TRAIN:
        y = np.clip(levels[known], 1, 30)
        Xk = X[known]
        # precizia: validare încrucișată pe clădirile cu niveluri cunoscute
        errs, base = [], []
        for tr, te in KFold(5, shuffle=True, random_state=0).split(Xk):
            m = HistGradientBoostingRegressor(categorical_features="from_dtype",
                                              max_iter=200, learning_rate=0.08,
                                              random_state=0)
            m.fit(Xk.iloc[tr], y[tr])
            p = np.clip(np.round(m.predict(Xk.iloc[te])), 1, 30)
            errs.append(np.abs(p - y[te]))
            base.append(np.abs(np.median(y[tr]) - y[te]))
        errs = np.concatenate(errs)
        info.update({
            "metoda": "model învățat din clădirile cu niveluri cunoscute din zonă",
            "eroare_medie": float(errs.mean()),
            "exact_pct": float(100 * (errs == 0).mean()),
            "plus_minus_1_pct": float(100 * (errs <= 1).mean()),
            "eroare_fara_model": float(np.concatenate(base).mean()),
        })
        if info["eroare_medie"] >= info["eroare_fara_model"]:
            # modelul nu bate o estimare naivă: nu îl folosim
            info = {k: info[k] for k in ("cunoscute", "estimate")}
            info["metoda"] = ("reguli pe tip și amprentă (modelul învățat nu a fost mai precis "
                              "decât o estimare simplă în această zonă)")
            pred = _rules(gdf[~known])
        else:
            model = HistGradientBoostingRegressor(categorical_features="from_dtype",
                                                  max_iter=200, learning_rate=0.08,
                                                  random_state=0).fit(Xk, y)
            pred = np.clip(np.round(model.predict(X[~known])), 1, 30) if (~known).any() \
                else np.array([])
    else:
        info["metoda"] = ("reguli pe tip și amprentă (prea puține clădiri cu niveluri "
                          f"cunoscute pentru un model: {int(known.sum())} < {MIN_TRAIN})")
        pred = _rules(gdf[~known])

    final = levels.copy()
    final[~known] = pred
    gdf["niv_final"] = final
    gdf["niv_sursa"] = np.where(known, "OSM", "estimat")
    return gdf, info
