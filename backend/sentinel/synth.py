"""Reproducible synthetic data generator.

Produces 90 days of environmental readings (zone x day), pseudonymous clinic visit records
(one row per visit, with an observation day, an availability day and incomplete location), and
aggregated workplace HR / safety counts (site x cohort x day, split by reporting lag).

No real person, patient, employee, employer or place is represented. Identical inputs
(scenario, seed, degraded) always produce identical output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from . import config as C

CLINIC_LAG_P = [0.15, 0.50, 0.25, 0.10]  # days from visit to availability in the clinic feed
OCC_LAG_P = {"sick_leave": [0.35, 0.55, 0.10], "fatigue_reports": [0.50, 0.40, 0.10]}
CLINIC_DOW = np.array([1.15, 1.05, 1.0, 1.0, 0.95, 0.85, 0.70])  # Mon..Sun
WORK_DOW = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 0.75, 0.30])
CLINIC_BASE = {"heat_illness_visits": 10.0, "resp_visits": 22.0}
ABSENCE_RATE = np.array([0.030, 0.025, 0.020])  # outdoor, yard, indoor (per worker-day)
FATIGUE_RATE = np.array([0.020, 0.006, 0.001])
GPS_SHARE, SITE_SHARE, DISTRICT_SHARE = 0.68, 0.14, 0.14  # remainder: no location at all
DEGRADED_GPS_LOSS = 0.30
DEGRADED_CLINIC_DELAY = 2

LOC_GPS, LOC_SITE, LOC_DISTRICT, LOC_NONE, LOC_POSTAL = 0, 1, 2, 3, 4  # LOC_POSTAL is assigned in the pipeline, never in raw data
POSTAL_SEED_OFFSET = 104729  # postal codes use their own random stream so no other column changes


@dataclass
class Scenario:
    id: str
    label: str
    description: str
    events: list = field(default_factory=list)  # health-impacting events (ground truth)
    decoys: list = field(default_factory=list)  # exposure without health response
    artefacts: list = field(default_factory=list)  # reporting artefacts / outages
    default_as_of: int = 60
    health_lag: int | None = None  # stress tests only: days from exposure onset to health response (None = built-in timing)


SCENARIOS = {
    "heatwave": Scenario(
        id="heatwave",
        label="Heatwave: outdoor workers (North District)",
        description="A 5-day heatwave over Riverside Works (N1) and Ring Road Corridor (N2) followed by heat-illness clinic visits, sick leave and fatigue reports at outdoor sites.",
        events=[{"type": "heatwave", "zones": ["N1", "N2"], "start": 55, "length": 5, "magnitude": 7.0}],
        default_as_of=58,
    ),
    "air_quality": Scenario(
        id="air_quality",
        label="Air-quality respiratory cluster (Industrial Estate)",
        description="A 4-day PM2.5 episode in Industrial Estate (N3) with a rise in respiratory clinic visits and absence.",
        events=[{"type": "air_quality", "zones": ["N3"], "start": 60, "length": 4, "magnitude": 85.0}],
        default_as_of=62,
    ),
    "edge_case": Scenario(
        id="edge_case",
        label="Edge case: exposure without health response + reporting faults",
        description="A 2-day heat spike in Old Market (S1) with no health response, a clinic in Port Logistics (S2) that reports four days of visits late in one batch, and a PM2.5 monitor outage in New Town (S3).",
        decoys=[{"type": "heat_spike", "zones": ["S1"], "start": 57, "length": 2, "magnitude": 6.5}],
        artefacts=[
            {"type": "bulk_late_report", "zone": "S2", "from": 58, "to": 61, "report_day": 65},
            {"type": "station_outage", "signal": "pm25", "zone": "S3", "from": 59, "to": 63},
        ],
        default_as_of=61,
    ),
}


@dataclass
class World:
    scenario_id: str
    seed: int
    degraded: bool
    env: dict  # signal -> float array [zone, day] (NaN = no reading)
    clinic: dict  # column -> array (one entry per visit record)
    occ: dict  # signal -> int array [site, cohort, obs_day, lag]
    truth: list  # ground-truth health events
    decoys: list
    artefacts: list


def _ar1(rng, n, phi, sd):
    out = np.zeros(n)
    e = rng.normal(0, sd, n)
    for i in range(1, n):
        out[i] = phi * out[i - 1] + e[i]
    return out


def _profile(length):
    p = np.ones(length)
    p[0] = 0.6
    if length > 2:
        p[-1] = 0.7
    return p


def _lagged(a, k):
    out = np.zeros_like(a)
    if k == 0:
        return a.copy()
    out[:, k:] = a[:, :-k]
    return out


def generate(scenario: Scenario, seed: int = 42, degraded: bool = False) -> World:
    rng = np.random.default_rng(seed)
    Z, D, S = len(C.ZONES), C.N_DAYS, len(C.SITES)
    days = np.arange(D)
    dow = np.array([(C.START_DATE + timedelta(days=int(d))).weekday() for d in days])

    # --- environmental baseline -------------------------------------------------------
    season = 35.0 + 1.8 * np.sin(np.pi * days / D)
    regional_hi = _ar1(rng, D, 0.7, 1.0)
    regional_pm = _ar1(rng, D, 0.75, 0.16)
    hi = np.zeros((Z, D))
    pm = np.zeros((Z, D))
    for zi, z in enumerate(C.ZONES):
        hi[zi] = season + regional_hi + z["heat_offset"] + _ar1(rng, D, 0.6, 0.7)
        pm[zi] = 30.0 * np.exp(regional_pm + _ar1(rng, D, 0.6, 0.12)) + z["pm_offset"]

    hi_excess_health = np.zeros((Z, D))
    pm_excess_health = np.zeros((Z, D))
    truth = []
    for ev in scenario.events:
        prof = _profile(ev["length"]) * ev["magnitude"]
        span = slice(ev["start"], ev["start"] + ev["length"])
        for zid in ev["zones"]:
            zi = C.ZONE_INDEX[zid]
            if ev["type"] == "heatwave":
                hi[zi, span] += prof
                hi_excess_health[zi, span] += prof
                L = scenario.health_lag
                truth.append({"id": f"{ev['type']}-{zid}-{ev['start']}", "track": "heat", "zone": zid,
                              "exposure_start": ev["start"], "health_onset": ev["start"] + (1 if L is None else L),
                              "end": ev["start"] + ev["length"] + 1 + (0 if L is None else max(0, L - 1))})
            elif ev["type"] == "air_quality":
                pm[zi, span] += prof
                pm_excess_health[zi, span] += prof
                L = scenario.health_lag
                truth.append({"id": f"{ev['type']}-{zid}-{ev['start']}", "track": "respiratory", "zone": zid,
                              "exposure_start": ev["start"], "health_onset": ev["start"] + (0 if L is None else L),
                              "end": ev["start"] + ev["length"] + (0 if L is None else L)})
    for dc in scenario.decoys:
        prof = _profile(dc["length"]) * dc["magnitude"]
        for zid in dc["zones"]:
            hi[C.ZONE_INDEX[zid], dc["start"]: dc["start"] + dc["length"]] += prof

    # --- clinic visits (one record per visit) ------------------------------------------
    L = scenario.health_lag
    if L is None:  # built-in timing (all demo and validation scenarios)
        heat_mult = 1 + 0.08 * hi_excess_health + 0.17 * _lagged(hi_excess_health, 1)
        resp_mult = 1 + 0.006 * pm_excess_health + 0.004 * _lagged(pm_excess_health, 1)
    else:  # stress test: shift the health response by L days (L = 0 means simultaneous with exposure)
        heat_mult = 1 + (0.25 * hi_excess_health if L == 0 else 0.08 * _lagged(hi_excess_health, L - 1) + 0.17 * _lagged(hi_excess_health, L))
        resp_mult = 1 + 0.006 * _lagged(pm_excess_health, L) + 0.004 * _lagged(pm_excess_health, L + 1)
    lam = {
        "heat_illness_visits": CLINIC_BASE["heat_illness_visits"] * np.exp(0.035 * (hi - 35.0)) * heat_mult,
        "resp_visits": CLINIC_BASE["resp_visits"] * np.exp(0.004 * (pm - 35.0)) * resp_mult,
    }
    sig_col, obs_col, zone_col = [], [], []
    for si, sig in enumerate(C.CLINIC_SIGNALS):
        pop = np.array([z["pop_factor"] for z in C.ZONES])[:, None]
        l = lam[sig] * pop * CLINIC_DOW[dow][None, :]
        counts = rng.poisson(l * rng.gamma(30.0, 1 / 30.0, size=l.shape))
        zz, dd = np.nonzero(counts)
        reps = counts[zz, dd]
        sig_col.append(np.full(reps.sum(), si))
        zone_col.append(np.repeat(zz, reps))
        obs_col.append(np.repeat(dd, reps))
    sig_arr = np.concatenate(sig_col)
    true_zone = np.concatenate(zone_col)
    obs = np.concatenate(obs_col)
    n = len(obs)
    lag = rng.choice(len(CLINIC_LAG_P), size=n, p=CLINIC_LAG_P)
    avail = obs + lag

    u = rng.random(n)
    loc = np.full(n, LOC_NONE)
    loc[u < GPS_SHARE + SITE_SHARE + DISTRICT_SHARE] = LOC_DISTRICT
    loc[u < GPS_SHARE + SITE_SHARE] = LOC_SITE
    loc[u < GPS_SHARE] = LOC_GPS
    cols = np.array([z["col"] for z in C.ZONES])
    rows = np.array([z["row"] for z in C.ZONES])
    gx = cols[true_zone] + rng.uniform(0.02, 0.98, n)
    gy = rows[true_zone] + rng.uniform(0.02, 0.98, n)
    site = np.full(n, -1)
    zone_sites = {zi: [si for si, s in enumerate(C.SITES) if C.SITE_ZONE_IDX[si] == zi] for zi in range(Z)}
    pick = rng.random(n)
    for i in np.nonzero(loc == LOC_SITE)[0]:
        options = zone_sites[true_zone[i]]
        if options:
            site[i] = options[int(pick[i] * len(options))]
        else:
            loc[i] = LOC_DISTRICT
    district = np.array(C.ZONE_DISTRICT_IDX)[true_zone]

    # --- workplace HR / safety aggregates ----------------------------------------------
    shares = np.array([C.SITE_TYPES[s["type"]]["shares"] for s in C.SITES])  # [S, 3]
    wf = np.array([s["workforce"] for s in C.SITES], dtype=float)
    site_z = np.array(C.SITE_ZONE_IDX)
    hi_s = hi[site_z]  # [S, D]
    ex_s = hi_excess_health[site_z]
    pm_ex_s = pm_excess_health[site_z]
    if L is None:
        ex_lag = (_lagged(ex_s, 1) + _lagged(ex_s, 2)) / 2
        pm_term = _lagged(pm_ex_s, 1)
        fat_core = 1 + 0.14 * ex_s + 0.06 * _lagged(ex_s, 1)
    else:
        ex_lag = (_lagged(ex_s, L) + _lagged(ex_s, L + 1)) / 2
        pm_term = _lagged(pm_ex_s, L + 1)
        fat_core = 1 + (0.20 * ex_s if L == 0 else 0.14 * _lagged(ex_s, L - 1) + 0.06 * _lagged(ex_s, L))
    abs_mult = np.stack([1 + 0.07 * ex_lag, 1 + 0.035 * ex_lag, np.ones_like(ex_lag)], axis=1)  # [S,3,D]
    abs_mult = abs_mult + 0.0015 * pm_term[:, None, :]
    fat_mult = np.stack([fat_core] * 2 + [np.ones_like(ex_s)], axis=1)
    base = (wf[:, None] * shares)[:, :, None] * WORK_DOW[dow][None, None, :]
    occ = {}
    l_abs = base * ABSENCE_RATE[None, :, None] * abs_mult
    l_fat = base * FATIGUE_RATE[None, :, None] * np.exp(0.06 * (hi_s - 35.0))[:, None, :] * fat_mult
    for sig, l in (("sick_leave", l_abs), ("fatigue_reports", l_fat)):
        counts = rng.poisson(l)
        p = OCC_LAG_P[sig]
        split = np.zeros(counts.shape + (len(p),), dtype=int)
        remaining = counts.copy()
        for k in range(len(p) - 1):
            frac = p[k] / sum(p[k:])
            take = rng.binomial(remaining, frac)
            split[..., k] = take
            remaining = remaining - take
        split[..., -1] = remaining
        occ[sig] = split

    # --- environmental feed gaps ---------------------------------------------------------
    env = {"heat_index": np.round(hi, 1), "pm25": np.round(pm, 1)}
    for sig in env:
        gaps = rng.random((Z, D)) < 0.01
        env[sig] = np.where(gaps, np.nan, env[sig])

    # --- reporting artefacts -----------------------------------------------------------
    for art in scenario.artefacts:
        if art["type"] == "bulk_late_report":
            zi = C.ZONE_INDEX[art["zone"]]
            m = (true_zone == zi) & (obs >= art["from"]) & (obs <= art["to"])
            avail[m] = np.maximum(avail[m], art["report_day"])
        elif art["type"] == "station_outage":
            zi = C.ZONE_INDEX[art["zone"]]
            env[art["signal"]][zi, art["from"]: art["to"] + 1] = np.nan

    # --- degraded-input simulation (separate random stream: same underlying world) -------
    if degraded:
        drng = np.random.default_rng(seed + 7919)
        lose = (loc == LOC_GPS) & (drng.random(n) < DEGRADED_GPS_LOSS)
        # A record that loses GPS keeps its district code (from the clinic's registered address).
        loc[lose] = LOC_DISTRICT
        avail = avail + DEGRADED_CLINIC_DELAY

    # --- postal codes (own random stream). Only records that have a district but no GPS/site carry one. ----
    prng = np.random.default_rng(seed + POSTAL_SEED_OFFSET)
    u_kind, u_pick = prng.random(n), prng.random(n)
    own = {zid: [int(c) for c, v in C.POSTAL_CROSSWALK.items() if v.get("zone") == zid] for zid in C.ZONE_IDS}
    postal = np.zeros(n, dtype=int)  # 0 = no postal code recorded
    for i in np.nonzero(loc == LOC_DISTRICT)[0]:
        zid = C.ZONE_IDS[true_zone[i]]
        if u_kind[i] < 0.78:
            opts = own[zid]
        elif u_kind[i] < 0.90:
            opts = [int(c) for c in C.POSTAL_BOUNDARY_FOR_ZONE[zid]]
        else:
            opts = [int(c) for c in C.POSTAL_UNMAPPED]
        postal[i] = opts[int(u_pick[i] * len(opts))]

    gx = np.where(loc == LOC_GPS, gx, np.nan)
    gy = np.where(loc == LOC_GPS, gy, np.nan)
    district = np.where(loc == LOC_NONE, -1, district)
    site = np.where(loc == LOC_SITE, site, -1)

    clinic = {
        "record_id": np.arange(n),  # pseudonymous row number; never leaves the backend
        "signal": sig_arr, "obs": obs, "avail": avail, "loc_type": loc,
        "gps_x": gx, "gps_y": gy, "site": site, "district": district, "postal": postal,
        "true_zone": true_zone,  # ground truth for validation only; not used by detection
    }
    return World(scenario.id, seed, degraded, env, clinic, occ, truth, scenario.decoys, scenario.artefacts)


def random_scenario(rng: np.random.Generator, idx: int) -> Scenario:
    """Randomised scenario for validation runs: one heatwave, one air-quality event, one decoy."""
    zones = list(C.ZONE_IDS)
    hz = zones[int(rng.integers(0, 6))]
    hz_list = [hz]
    if rng.random() < 0.5:
        zi = C.ZONE_INDEX[hz]
        neighbour = zi + 1 if C.ZONES[zi]["col"] < 2 else zi - 1
        hz_list.append(zones[neighbour])
    az = zones[int(rng.integers(0, 6))]
    decoy_candidates = [z for z in zones if z not in hz_list]
    dz = decoy_candidates[int(rng.integers(0, len(decoy_candidates)))]
    return Scenario(
        id=f"random-{idx}", label=f"Random validation scenario {idx}", description="",
        events=[
            {"type": "heatwave", "zones": hz_list, "start": int(rng.integers(42, 77)), "length": int(rng.integers(3, 7)), "magnitude": float(rng.uniform(5.0, 9.0))},
            {"type": "air_quality", "zones": [az], "start": int(rng.integers(42, 79)), "length": int(rng.integers(3, 6)), "magnitude": float(rng.uniform(50.0, 110.0))},
        ],
        decoys=[{"type": "heat_spike", "zones": [dz], "start": int(rng.integers(42, 81)), "length": int(rng.integers(2, 4)), "magnitude": float(rng.uniform(5.0, 8.0))}],
    )
