"""Ingestion and alignment: location fallback, time buckets, availability-aware cubes.

Key rule: every record is kept. A record is placed at the most precise *reliable* geographic
level available (GPS -> zone, workplace site ID -> zone, district code -> district, nothing ->
region) and tagged with that level. Counts are built "as of" each replay day t using only
records whose availability day is <= t, so the past is never seen with future knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import config as C
from .synth import LOC_DISTRICT, LOC_GPS, LOC_NONE, LOC_POSTAL, LOC_SITE, World

GEO_LEVELS = {
    LOC_GPS: {"id": "zone_gps", "label": "Zone (GPS)", "confidence": "high"},
    LOC_SITE: {"id": "zone_site", "label": "Zone (workplace site ID)", "confidence": "medium"},
    LOC_POSTAL: {"id": "zone_postal", "label": "Zone (postal-code crosswalk)", "confidence": "medium"},
    LOC_DISTRICT: {"id": "district", "label": "District only (no usable postal code)", "confidence": "low"},
    LOC_NONE: {"id": "region", "label": "Unlocated (region only)", "confidence": "none"},
}
MAX_LAG = 12


@dataclass
class Aligned:
    world: World
    rec_zone: np.ndarray  # resolved zone index or -1
    rec_level: np.ndarray  # LOC_* level actually used
    zone_raw: dict  # clinic signal -> [T, Z, D] raw zone-resolved counts available by t
    dist_raw: dict  # clinic signal -> [T, Ds, D] all records with a district (zone-resolved included)
    region_raw: dict  # clinic signal -> [T, D] all records
    coverage: dict  # clinic signal -> [T, Ds, D] 7-day share of district records resolved to a zone
    site_raw: dict  # occupational signal -> [T, S, cohort, D]
    zone_occ_raw: dict  # occupational signal -> [T, Z, D]
    completeness: dict  # signal -> array over lag k: estimated share reported within k days
    env: dict  # environmental signal -> [Z, D]
    dow: np.ndarray


def resolve_location(world: World):
    cl = world.clinic
    n = len(cl["obs"])
    zone = np.full(n, -1)
    level = cl["loc_type"].copy()
    gps = level == LOC_GPS
    col = np.floor(cl["gps_x"][gps]).astype(int)
    row = np.floor(cl["gps_y"][gps]).astype(int)
    lookup = {(z["col"], z["row"]): i for i, z in enumerate(C.ZONES)}
    zone[gps] = [lookup.get((c, r), -1) for c, r in zip(col, row)]
    site = level == LOC_SITE
    zone[site] = np.array(C.SITE_ZONE_IDX)[cl["site"][site]]
    # Defensive fallback: a GPS point outside every zone keeps its district instead of being dropped.
    bad = (level == LOC_GPS) & (zone < 0)
    level[bad] = LOC_DISTRICT
    # Postal fallback: only for records that otherwise sit at district level, and only for codes that the
    # documented crosswalk maps to exactly one zone. Boundary, unknown or missing codes are never guessed.
    code_zone = {int(c): C.ZONE_INDEX[v["zone"]] for c, v in C.POSTAL_CROSSWALK.items() if "zone" in v}
    postal = cl["postal"]
    for i in np.nonzero((level == LOC_DISTRICT) & (postal > 0))[0]:
        zi = code_zone.get(int(postal[i]))
        if zi is not None and C.ZONE_DISTRICT_IDX[zi] == cl["district"][i]:  # the code must agree with the record's district
            zone[i] = zi
            level[i] = LOC_POSTAL
    return zone, level


def _completeness_profile(lags: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    k = np.arange(MAX_LAG + 1)
    if len(lags) == 0:
        return np.ones(MAX_LAG + 1)
    w = np.ones(len(lags)) if weights is None else weights
    tot = w.sum()
    return np.array([w[lags <= kk].sum() / tot for kk in k])


def align(world: World) -> Aligned:
    T = D = C.N_DAYS
    Z, Ds, S = len(C.ZONES), len(C.DISTRICTS), len(C.SITES)
    cl = world.clinic
    rec_zone, rec_level = resolve_location(world)
    avail_b = np.minimum(cl["avail"], T)  # bucket T = not yet available inside the replay window

    zone_raw, dist_raw, region_raw, coverage, completeness = {}, {}, {}, {}, {}
    for si, sig in enumerate(C.CLINIC_SIGNALS):
        m = cl["signal"] == si
        zr = np.zeros((Z, T + 1, D))
        mz = m & (rec_zone >= 0)
        np.add.at(zr, (rec_zone[mz], avail_b[mz], cl["obs"][mz]), 1)
        zone_raw[sig] = np.cumsum(zr, axis=1)[:, :T, :].transpose(1, 0, 2)  # [T, Z, D]

        dr = np.zeros((Ds, T + 1, D))
        md = m & (cl["district"] >= 0)
        np.add.at(dr, (cl["district"][md], avail_b[md], cl["obs"][md]), 1)
        dist_raw[sig] = np.cumsum(dr, axis=1)[:, :T, :].transpose(1, 0, 2)

        rr = np.zeros((T + 1, D))
        np.add.at(rr, (avail_b[m], cl["obs"][m]), 1)
        region_raw[sig] = np.cumsum(rr, axis=0)[:T]

        # share of district records that could be resolved to a zone (rolling 7 days)
        zone_in_dist = np.zeros((T, Ds, D))
        for zi in range(Z):
            zone_in_dist[:, C.ZONE_DISTRICT_IDX[zi], :] += zone_raw[sig][:, zi, :]
        kern = np.ones(7)
        num = np.apply_along_axis(lambda v: np.convolve(v, kern)[:D], 2, zone_in_dist)
        den = np.apply_along_axis(lambda v: np.convolve(v, kern)[:D], 2, dist_raw[sig])
        with np.errstate(invalid="ignore", divide="ignore"):
            coverage[sig] = np.where(den > 0, num / den, np.nan)

        early = m & (cl["obs"] < C.COMPLETENESS_ESTIMATION_DAYS)
        completeness[sig] = _completeness_profile(cl["avail"][early] - cl["obs"][early])

    site_raw, zone_occ_raw = {}, {}
    for sig in C.OCC_SIGNALS:
        split = world.occ[sig]  # [S, cohort, D, L]
        L = split.shape[-1]
        cum = np.cumsum(split, axis=-1)
        out = np.zeros((T, S, split.shape[1], D))
        d_idx = np.arange(D)
        for t in range(T):
            k = t - d_idx
            ok = k >= 0
            kk = np.clip(k, 0, L - 1)
            vals = np.take_along_axis(cum, kk[None, None, :, None].repeat(S, 0).repeat(split.shape[1], 1), axis=-1)[..., 0]
            out[t] = np.where(ok[None, None, :], vals, 0)
        site_raw[sig] = out
        zo = np.zeros((T, Z, D))
        for si in range(S):
            zo[:, C.SITE_ZONE_IDX[si], :] += out[:, si].sum(axis=1)
        zone_occ_raw[sig] = zo
        early = split[:, :, : C.COMPLETENESS_ESTIMATION_DAYS, :].sum(axis=(0, 1, 2))
        prof = np.cumsum(early) / max(early.sum(), 1)
        completeness[sig] = np.concatenate([prof, np.ones(MAX_LAG + 1 - len(prof))])

    for sig in C.ENV_SIGNALS:
        completeness[sig] = np.ones(MAX_LAG + 1)

    dow = np.array([C.day_to_date(d).weekday() for d in range(D)])
    return Aligned(world, rec_zone, rec_level, zone_raw, dist_raw, region_raw, coverage,
                   site_raw, zone_occ_raw, completeness, dict(world.env), dow)


def completeness_at(al: Aligned, sig: str, k: int) -> float:
    prof = al.completeness[sig]
    return float(prof[min(max(k, 0), len(prof) - 1)])


def zone_series(al: Aligned, sig: str, t: int, zi: int, min_completeness: float):
    """Return (estimate, raw, completeness) arrays for days 0..t as known at replay day t.

    Counts are adjusted for (a) reporting completeness at lag t-d and (b) the share of the
    district's records that could be placed in a zone. Days whose expected completeness is
    below the minimum are returned as NaN ("pending"), never as zero.
    """
    d = np.arange(t + 1)
    if sig in C.ENV_SIGNALS:
        v = al.env[sig][zi, : t + 1].astype(float)
        return v, v, np.ones(t + 1)
    comp = al.completeness[sig][np.clip(t - d, 0, MAX_LAG)]
    if sig in C.CLINIC_SIGNALS:
        raw = al.zone_raw[sig][t, zi, : t + 1]
        cov = al.coverage[sig][t, C.ZONE_DISTRICT_IDX[zi], : t + 1]
        cov = np.where(np.isnan(cov) | (cov < 0.2), np.nan, cov)
        with np.errstate(invalid="ignore", divide="ignore"):
            est = raw / (comp * cov)
    else:
        raw = al.zone_occ_raw[sig][t, zi, : t + 1]
        est = raw / comp
    est = np.where(comp >= min_completeness, est, np.nan)
    return est, raw, comp


def location_quality(al: Aligned, t: int | None = None, zi: int | None = None, district: int | None = None) -> dict:
    """Share of clinic records placed at each geographic level (records available by t)."""
    cl = al.world.clinic
    m = np.ones(len(cl["obs"]), dtype=bool)
    if t is not None:
        m &= cl["avail"] <= t
    if zi is not None:
        # zone-level view: records resolved to this zone plus district-only records of its district
        m &= (al.rec_zone == zi) | ((al.rec_zone < 0) & (cl["district"] == C.ZONE_DISTRICT_IDX[zi]))
    if district is not None:
        m &= cl["district"] == district
    lv = al.rec_level[m]
    total = int(m.sum())
    out = {"total_records": total, "levels": []}
    for code, meta in GEO_LEVELS.items():
        cnt = int((lv == code).sum())
        out["levels"].append({**meta, "records": cnt, "share": (cnt / total) if total else None})
    precise = sum(l["records"] for l in out["levels"] if l["id"] in ("zone_gps", "zone_site", "zone_postal"))
    out["zone_resolved_share"] = (precise / total) if total else None
    return out
