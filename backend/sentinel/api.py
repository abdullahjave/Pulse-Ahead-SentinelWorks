"""FastAPI application: every response is scoped by the caller's role (from the server-side
token) and passed through small-cell suppression before it leaves the server."""
from __future__ import annotations

import csv
import io
import json
import math
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config as C
from . import release, store
from .auth import USERS, PERMISSIONS, CurrentUser, issue_token, require_site, require_zone, sites_in_scope, zones_in_scope
from .detection import baseline_at
from .engine import TIER_RANK, demo_run, evaluate, zone_status
from .pipeline import GEO_LEVELS, location_quality, zone_series
from .playbooks import ROLES, evidence_lines, playbook
from .privacy import count_cell, is_small, suppress_row
from .synth import SCENARIOS

app = FastAPI(title="SentinelWorks API", version="1.0")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_validation_lock = threading.Lock()


# ---------------------------------------------------------------- helpers
def num(x, nd=2):
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(xf) or math.isinf(xf):
        return None
    return round(xf, nd)


def get_run(scenario: str, degraded: bool):
    if scenario not in SCENARIOS:
        raise HTTPException(404, "Unknown scenario")
    return demo_run(scenario, degraded)


def as_of_day(as_of: str | None, scenario: str) -> int:
    if not as_of:
        return SCENARIOS[scenario].default_as_of
    try:
        d = C.date_to_day(as_of)
    except ValueError:
        raise HTTPException(422, "as_of must be an ISO date")
    return int(min(max(d, C.WARMUP_DAYS), C.N_DAYS - 1))


def iso(d):
    return None if d is None else C.day_to_date(d).isoformat()


def zone_meta(zid):
    z = C.ZONES[C.ZONE_INDEX[zid]]
    return {"id": z["id"], "name": z["name"], "district": z["district"]}


def can_see_counts(user) -> bool:
    return user["role"] == "public_health_officer"


def public_clinical_numbers(run, zi, sig, t_row, day):
    """value, expected, sd of a clinical count computed ONLY from released days (withheld days treated as missing)."""
    est, raw, comp = zone_series(run.al, sig, t_row, zi, 0.0)
    est_b = np.where(comp >= run.params["min_completeness"], est, np.nan)
    est_b = np.where(_small(raw), np.nan, est_b)
    v = est_b[day]
    e, sd = baseline_at(est_b, day, run.al.dow, sig)
    if np.isnan(v) or np.isnan(e) or not sd:
        return None
    return float(v), float(e), float(sd)


def contribution_out(c: dict, raw, user, ctx=None) -> dict:
    sig = C.SIGNALS[c["signal"]]
    out = {
        "signal": c["signal"], "label": sig["label"], "short": sig["short"], "unit": sig["unit"], "family": c["family"],
        "family_label": C.FAMILY_LABELS[c["family"]], "weight": c["weight"], "status": c["status"], "date": iso(c["day"]),
        "z": num(c["z"]), "pct_deviation": num(c["pct_deviation"], 3), "value": num(c["value"], 1), "expected": num(c["expected"], 1),
        "points": num(c["points"], 2), "share": num(c["share"], 2), "suppressed": False, "values_hidden_for_role": False,
    }
    if sig["kind"] == "count":
        if is_small(raw):
            out.update(value=None, expected=None, z=None, pct_deviation=None, points=None, share=None, suppressed=True)
        elif not can_see_counts(user):
            out.update(value=None, expected=None, values_hidden_for_role=True)
    if ctx is not None and sig["family"] == "clinical" and sig["kind"] == "count" and not out["suppressed"] and c["day"] is not None:
        run_, zi_, t_row = ctx
        nums = public_clinical_numbers(run_, zi_, c["signal"], t_row, c["day"])
        if nums is None:
            out.update(z=None, pct_deviation=None, value=None, expected=None)
        else:  # R8: from released days only, rounded (whole visits, z to 0.1, deviation to 1%)
            v, e, sd = nums
            out["z"] = num((v - e) / sd, 1)
            out["pct_deviation"] = num(v / e - 1, 2) if e else None
            if out["value"] is not None:
                out["value"], out["expected"] = num(v, 0), num(e, 0)
    if sig["family"] == "occupational":
        # Release policy R1: no daily workplace number leaves the server. Only the share and weight remain.
        out.update(z=None, pct_deviation=None, value=None, expected=None, weekly_only=True)
    return out


def mask_row(contribs: list) -> list:
    """If any count component is suppressed, also withhold what would reveal it by subtraction:
    the per-signal points, and the share split (renormalised over the visible signals only)."""
    if not any(c["suppressed"] for c in contribs):
        return contribs
    vis_total = sum(c["share"] for c in contribs if not c["suppressed"] and c["share"] is not None)
    for c in contribs:
        c["points"] = None
        c["share_basis"] = "visible_signals"
        if not c["suppressed"] and c["share"] is not None and vis_total > 0:
            c["share"] = num(c["share"] / vis_total, 2)
    return contribs


def row_contributions(run, zi, row, user):
    st = run.states[(row["t"], zi)]
    return mask_row([contribution_out(c, st[c["signal"]].get("raw"), user, ctx=(run, zi, row["t"])) for c in row["contributions"]])


def safe_score(run, zi, t, contributions, score):
    """The composite score is the sum of the components, so it is withheld whenever a count component
    is suppressed (otherwise hidden = score - visible components)."""
    st = run.states[(t, zi)]
    for c in contributions:
        if C.SIGNALS[c["signal"]]["kind"] == "count" and is_small(st[c["signal"]].get("raw")):
            return None
    return num(score, 1)


def _small(a):
    """Boolean mask: aggregated counts in 1..SMALL_CELL_THRESHOLD-1 (zero is not sensitive)."""
    r = np.rint(np.asarray(a, dtype=float))
    return (r > 0) & (r < C.SMALL_CELL_THRESHOLD)


def derived_cell() -> dict:
    return {"value": None, "display": "masked", "suppressed": True, "reason": "derived"}


def child_small_mask(run, user, sig, t, level, ident):
    """Per-day mask for CLINIC series: a district day is withheld when a zone day inside it is suppressed and the same
    role can request that zone (district minus visible zones would otherwise reveal it). Workplace counts are not
    published daily at all (release.py), so they need no daily mask."""
    mask = np.zeros(C.N_DAYS, dtype=bool)
    if level == "district" and sig in C.CLINIC_SIGNALS and sig in user["permissions"]["zone_signals"]:
        for zi, z in enumerate(C.ZONES):
            if z["district"] == ident:
                mask |= _small(run.al.zone_raw[sig][t, zi])
    return mask


def apply_derived_mask(points: list, mask) -> list:
    for p in points:
        if mask[C.date_to_day(p["date"])] and not p["suppressed"]:
            p.update(observed=None, z=None, suppressed=True, derived=True)
            if "reported_so_far" in p:
                p["reported_so_far"] = derived_cell()
    return points


def pct_text(x):
    return "an undisclosed share" if x is None else f"{round(x * 100)}%"


def track_meta():
    return {k: {"id": k, "label": v["label"], "weights": v["weights"]} for k, v in C.TRACKS.items()}


def decision_summary(run, inc_id):
    recs = store.decisions(run.key, inc_id)
    return recs[-1] if recs else None


def incident_summary(run, inc, t, user):
    st = inc.state_at(t)
    zi = C.ZONE_INDEX[inc.zone]
    last = st["latest"]
    dec = decision_summary(run, inc.id)
    return {
        "id": inc.id, "zone": zone_meta(inc.zone), "track": inc.track, "track_label": C.TRACKS[inc.track]["label"],
        "status": st["status"], "current_tier": last["tier"], "max_tier": st["max_tier"],
        "opened": iso(inc.opened), "action_date": iso(inc.action_day) if inc.action_day is not None and inc.action_day <= t else None,
        "last_active": iso(st["last_active"]), "peak_score": safe_score(run, zi, st["peak"]["t"], st["peak"]["contributions"], st["peak"]["score"]),
        "current_score": safe_score(run, zi, last["t"], last["contributions"], last["score"]),
        "confidence": last["confidence"], "families": last["families"],
        "top_signals": sorted([c for c in row_contributions(run, zi, st["peak"], user) if c["share"]], key=lambda c: -c["share"])[:2],
        "decision": {"status": dec["status"], "recorded_at": dec["recorded_at"], "role": dec["role"]} if dec else None,
        "reopened": [iso(x) for x in inc.reopened if x <= t],
    }


def visible_incidents(run, t, user):
    scope = set(zones_in_scope(user))
    return [i for i in run.incidents if i.opened <= t and i.zone in scope]


def scenario_key_dates(sc) -> list:
    """Replay jump points derived from the scenario's own definition (event / decoy / artefact timing)."""
    lo, hi = C.WARMUP_DAYS, C.N_DAYS - 1
    clamp = lambda d: int(min(max(d, lo), hi))
    pts = [{"id": "start", "label": "Start of replay (normal baseline)", "day": lo}]
    for ev in sc.events:
        first, last = ev["start"], ev["start"] + ev["length"] - 1
        what = "Heatwave" if ev["type"] == "heatwave" else "PM2.5 episode"
        pts += [{"id": f"pre-{ev['type']}", "label": f"Normal baseline, 7 days before the {what.lower()}", "day": clamp(first - 7)},
                {"id": f"start-{ev['type']}", "label": f"{what} begins ({', '.join(ev['zones'])})", "day": clamp(first)},
                {"id": f"end-{ev['type']}", "label": f"{what} ends", "day": clamp(last)},
                {"id": f"rec-{ev['type']}", "label": f"Recovery, 7 days after the {what.lower()}", "day": clamp(last + 7)}]
    for dc in sc.decoys:
        first, last = dc["start"], dc["start"] + dc["length"] - 1
        pts += [{"id": "start-decoy", "label": f"Heat spike begins ({', '.join(dc['zones'])}; no health response)", "day": clamp(first)},
                {"id": "end-decoy", "label": "Heat spike ends", "day": clamp(last)}]
    for a in sc.artefacts:
        if a["type"] == "bulk_late_report":
            pts += [{"id": "late-start", "label": f"Clinic reporting gap begins ({a['zone']})", "day": clamp(a["from"])},
                    {"id": "late-batch", "label": f"Late batch of visits arrives ({a['zone']})", "day": clamp(a["report_day"])}]
        elif a["type"] == "station_outage":
            pts += [{"id": "outage-start", "label": f"PM2.5 monitor outage begins ({a['zone']})", "day": clamp(a["from"])},
                    {"id": "outage-end", "label": "PM2.5 monitor outage ends", "day": clamp(a["to"])}]
    pts.append({"id": "end", "label": "End of replay", "day": hi})
    seen, out = set(), []
    for p in sorted(pts, key=lambda p: p["day"]):
        if (p["day"], p["label"]) not in seen:
            seen.add((p["day"], p["label"]))
            out.append({"id": p["id"], "label": p["label"], "date": iso(p["day"])})
    return out


# ---------------------------------------------------------------- auth & meta
class LoginBody(BaseModel):
    user_id: str


@app.post("/api/auth/login")
def login(body: LoginBody):
    token = issue_token(body.user_id)
    u = USERS[body.user_id]
    return {"token": token, "user": {**u, "role_label": ROLES[u["role"]]["label"], "permissions": PERMISSIONS[u["role"]]}}


@app.get("/api/auth/me")
def me(user=CurrentUser):
    return {**user, "role_label": ROLES[user["role"]]["label"], "zones_in_scope": zones_in_scope(user), "sites_in_scope": sites_in_scope(user)}


@app.exception_handler(RuntimeError)
async def configuration_error(request, exc):
    """A missing or invalid tuned configuration must be visible to the client, not an opaque 500."""
    if "tuned configuration" in str(exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.get("/api/health")
def health():
    try:
        C.detection_params()
        config_error = None
    except RuntimeError as exc:
        config_error = str(exc)
    return {"ok": config_error is None, "config_error": config_error,
            "validation_available": (C.DATA_DIR / "validation_results.json").exists()}


@app.get("/api/meta")
def meta(user=CurrentUser):
    scope = zones_in_scope(user)
    return {
        "data_mode": {"synthetic": True, "replay": True, "label": "Synthetic data · Replay mode"},
        "dates": {"start": iso(0), "end": iso(C.N_DAYS - 1), "detection_start": iso(C.WARMUP_DAYS)},
        "scenarios": [{"id": s.id, "label": s.label, "description": s.description, "default_as_of": iso(s.default_as_of), "key_dates": scenario_key_dates(s)} for s in SCENARIOS.values()],
        "districts": C.DISTRICTS,
        "zones": [{**zone_meta(z["id"]), "col": z["col"], "row": z["row"], "in_scope": z["id"] in scope} for z in C.ZONES],
        "signals": [{"id": k, **v, "family_label": C.FAMILY_LABELS[v["family"]], "zone_visible": k in user["permissions"]["zone_signals"]} for k, v in C.SIGNALS.items()],
        "tracks": track_meta(),
        "detection": {k: v for k, v in C.detection_params().items()},
        "baseline": {"window_days": C.BASELINE_WINDOW, "guard_days": C.GUARD_DAYS, "day_of_week_adjusted": True},
        "users": [{"id": u["id"], "name": u["name"], "role": u["role"], "role_label": ROLES[u["role"]]["label"]} for u in USERS.values()],
        "permissions": {r: {**p, "label": ROLES[r]["label"]} for r, p in PERMISSIONS.items()},
        "guidance": C.GUIDANCE_SOURCES,
        "privacy": {"small_cell_threshold": C.SMALL_CELL_THRESHOLD, "geo_levels": list(GEO_LEVELS.values()),
                    "workplace_grain": "week", "release_rule": RULE_TEXT},
        "postal_crosswalk": [{"code": k, **v} for k, v in C.POSTAL_CROSSWALK.items()],
    }


# ---------------------------------------------------------------- overview
@app.get("/api/overview")
def overview(scenario: str = "heatwave", degraded: bool = False, as_of: str | None = None, user=CurrentUser):
    run = get_run(scenario, degraded)
    t = as_of_day(as_of, scenario)
    scope = zones_in_scope(user)
    zones = []
    counts = {"action": 0, "watch": 0, "insufficient_data": 0, "normal": 0}
    for zi, z in enumerate(C.ZONES):
        if z["id"] not in scope:
            zones.append({**zone_meta(z["id"]), "col": z["col"], "row": z["row"], "in_scope": False})
            continue
        zs = zone_status(run, t, zi)
        counts[zs["status"]] += 1
        tracks = {}
        for tr in C.TRACKS:
            f = run.fused[(t, zi, tr)]
            tracks[tr] = {"tier": run.tiers[(t, zi, tr)], "score": safe_score(run, zi, t, f["contributions"], f["score"]), "insufficient": f["insufficient"],
                          "weight_available": num(f["weight_available"]), "data_issues": f["data_issues"]}
        active = [i for i in run.incidents if i.zone == z["id"] and i.opened <= t and i.state_at(t)["status"] != "closed"]
        loc = location_quality(run.al, t=t, district=C.ZONE_DISTRICT_IDX[zi])
        zones.append({**zone_meta(z["id"]), "col": z["col"], "row": z["row"], "in_scope": True,
                      "status": zs["status"], "track": zs["track"], "score": safe_score(run, zi, t, zs["fused"]["contributions"], zs["score"]), "tracks": tracks,
                      "zone_resolved_share": num(loc["zone_resolved_share"], 3),
                      "active_incidents": [i.id for i in active]})

    incs = visible_incidents(run, t, user)
    active = [incident_summary(run, i, t, user) for i in incs if i.state_at(t)["status"] != "closed"]
    active.sort(key=lambda r: (-TIER_RANK[r["max_tier"]], -(r["current_score"] or 0)))
    events = [e for e in run.alert_events if e["t"] <= t and e["zone"] in scope]
    recent = [e for e in events if e["t"] > t - 14]
    zone_days_watch = sum(1 for (tt, zi, tr), tier in run.tiers.items() if t - 14 < tt <= t and tier != "normal" and C.ZONE_IDS[zi] in scope)
    feed = []
    for e in sorted(events, key=lambda e: (-e["t"], e["zone"]))[:40]:
        verb = {"opened": "raised", "escalated": "escalated to Action Required", "closed": "closed after 2 normal days", "reopened": "re-opened within cooldown"}[e["type"]]
        feed.append({"date": iso(e["t"]), "incident": e["incident"], "zone": zone_meta(e["zone"]), "track": e["track"],
                     "track_label": C.TRACKS[e["track"]]["label"], "type": e["type"], "tier": e["tier"],
                     "message": f"{C.TRACKS[e['track']]['label']} {'Watch ' if e['type'] == 'opened' and e['tier'] == 'watch' else ''}{verb}"})

    feeds = []
    for sig, meta_ in C.SIGNALS.items():
        newest = [run.states[(t, C.ZONE_INDEX[z])][sig].get("newest_day", run.states[(t, C.ZONE_INDEX[z])][sig].get("day")) for z in scope]
        newest = [d for d in newest if d is not None]
        statuses = [run.states[(t, C.ZONE_INDEX[z])][sig]["status"] for z in scope]
        nd = max(newest) if newest else None
        n_missing = sum(s in ("missing",) for s in statuses)
        n_delayed = sum(s == "delayed" for s in statuses)
        status = "missing" if nd is None else ("delayed" if n_delayed else "current")
        feeds.append({"signal": sig, "label": meta_["label"], "family": meta_["family"], "source": meta_["source"],
                      "newest_date": iso(nd), "lag_days": None if nd is None else t - nd, "status": status,
                      "zones_missing": n_missing, "zones_delayed": n_delayed, "zones_total": len(scope),
                      "typical_reporting_lag_days": int(np.argmax(run.al.completeness[sig] >= run.params["min_completeness"]))})
    loc_all = location_quality(run.al, t=t)
    return {
        "as_of": iso(t), "scenario": scenario, "degraded": degraded, "data_mode": "synthetic_replay",
        "zones": zones, "counts": counts,
        "kpis": {
            "action_incidents": sum(1 for a in active if a["max_tier"] == "action" and a["status"] == "active"),
            "watch_incidents": sum(1 for a in active if a["max_tier"] == "watch" and a["status"] == "active"),
            "monitoring_incidents": sum(1 for a in active if a["status"] == "monitoring"),
            "zones_insufficient": counts["insufficient_data"], "zones_in_scope": len(scope),
            "alerts_14d": len(recent), "zone_days_above_watch_14d": zone_days_watch,
            "zone_resolved_share": num(loc_all["zone_resolved_share"], 3),
            "records_total": loc_all["total_records"],
        },
        "active_incidents": active, "alert_feed": feed, "feeds": feeds,
        "location_quality": {**loc_all, "levels": [{**l, "share": num(l["share"], 3), "records": None if is_small(l["records"]) else l["records"],
                                                   "records_display": "<5" if is_small(l["records"]) else str(l["records"])} for l in loc_all["levels"]]}
        if can_see_counts(user) else {"zone_resolved_share": num(loc_all["zone_resolved_share"], 3),
                                      "levels": [{k: v for k, v in l.items() if k != "records"} | {"share": num(l["share"], 3)} for l in loc_all["levels"]]},
    }


# ---------------------------------------------------------------- time series
def _series_points(est_used, raw, comp, t, window, sig, dow, kind, show_raw, extra_hide=None):
    """Chart points. R8: the baseline that is DISPLAYED (expected, band, z) is computed only from released values: a withheld
    day counts as missing. Otherwise the published expected values of the next 28 days depend on the hidden count and an
    attacker who knows the method can recover it (measured: 62% of hidden clinic days pinned exactly)."""
    pts = []
    mc = C.detection_params()["min_completeness"]
    hide = np.zeros(len(est_used), dtype=bool)
    if kind == "count":
        hide = _small(np.asarray(raw)[: len(est_used)])
        if extra_hide is not None:
            hide = hide | np.asarray(extra_hide)[: len(est_used)]
    est_b = np.where(hide, np.nan, est_used)
    for d in range(max(0, t - window + 1), t + 1):
        e, sd = baseline_at(est_b, d, dow, sig)
        val = est_used[d] if not np.isnan(est_used[d]) else None
        if kind == "value":
            status = "missing" if val is None else "complete"
        else:
            status = "pending" if comp[d] < mc else ("provisional" if comp[d] < 0.95 else "complete")
        suppressed = kind == "count" and is_small(raw[d])
        z = None if (val is None or np.isnan(e)) else (val - e) / sd
        # Count baselines are rolling means of past days, so successive values differ by (new - old) / window.
        # Rounding them to whole numbers stops a masked 1-4 being recovered by differencing the baseline.
        nd = 0 if kind == "count" else 1
        p = {"date": iso(d), "observed": None if suppressed else num(val, 1), "expected": num(e, nd),
             "lower": num(max(e - 2 * sd, 0), nd) if (kind == "count" and not np.isnan(e)) else (num(e - 2 * sd, 1) if not np.isnan(e) else None),
             "upper": num(e + 2 * sd, nd) if not np.isnan(e) else None,
             "z": None if suppressed else num(z), "status": status, "suppressed": bool(suppressed),
             "completeness": num(comp[d], 2)}
        if kind == "count" and show_raw:
            p["reported_so_far"] = count_cell(raw[d])
        pts.append(p)
    return pts


@app.get("/api/zones/{zone}/timeseries")
def zone_timeseries(zone: str, signal: str, scenario: str = "heatwave", degraded: bool = False, as_of: str | None = None,
                    window: int = Query(42, ge=7, le=90), user=CurrentUser):
    require_zone(user, zone)
    if signal not in C.SIGNALS:
        raise HTTPException(404, "Unknown signal")
    if signal not in user["permissions"]["zone_signals"]:
        raise HTTPException(403, "Your role cannot view this signal at zone level")
    run = get_run(scenario, degraded)
    t = as_of_day(as_of, scenario)
    if signal in C.OCC_SIGNALS:
        return weekly_series(run, user, "zone", zone, signal, t, window)
    zi = C.ZONE_INDEX[zone]
    mc = run.params["min_completeness"]
    est, raw, comp = zone_series(run.al, signal, t, zi, 0.0)
    est_used = np.where(comp >= mc, est, np.nan)
    kind = C.SIGNALS[signal]["kind"]
    pts = _series_points(est_used, raw, comp, t, window, signal, run.al.dow, kind, can_see_counts(user))
    tiers = {tr: [{"date": iso(d), "tier": run.tiers.get((d, zi, tr), "normal")} for d in range(max(C.WARMUP_DAYS, t - window + 1), t + 1)]
             for tr, m in C.TRACKS.items() if signal in m["weights"]}
    return {"zone": zone_meta(zone), "signal": {"id": signal, **C.SIGNALS[signal]}, "as_of": iso(t), "points": pts, "tiers": tiers,
            "note": "Recent count days are completeness-adjusted estimates (provisional) until reporting is complete."}


@app.get("/api/districts/{district}/timeseries")
def district_timeseries(district: str, signal: str, scenario: str = "heatwave", degraded: bool = False, as_of: str | None = None,
                        window: int = Query(42, ge=7, le=90), user=CurrentUser):
    if not user["permissions"]["district_series"]:
        raise HTTPException(403, "Your role cannot view district series")
    if district not in C.DISTRICT_INDEX or signal not in C.SIGNALS:
        raise HTTPException(404, "Unknown district or signal")
    run = get_run(scenario, degraded)
    t = as_of_day(as_of, scenario)
    if signal in C.OCC_SIGNALS:
        return weekly_series(run, user, "district", district, signal, t, window)
    di = C.DISTRICT_INDEX[district]
    al = run.al
    d_idx = np.arange(t + 1)
    comp = al.completeness[signal][np.clip(t - d_idx, 0, len(al.completeness[signal]) - 1)]
    zis = [i for i, z in enumerate(C.ZONES) if z["district"] == district]
    if signal in C.ENV_SIGNALS:
        raw = np.nanmean(np.stack([al.env[signal][zi, : t + 1] for zi in zis]), axis=0)
        est = raw
    elif signal in C.CLINIC_SIGNALS:
        raw = al.dist_raw[signal][t, di, : t + 1]
        est = raw / comp
    else:
        raw = sum(al.zone_occ_raw[signal][t, zi, : t + 1] for zi in zis)
        est = raw / comp
    mc = run.params["min_completeness"]
    est_used = np.where(comp >= mc, est, np.nan)
    dmask = child_small_mask(run, user, signal, t, "district", district) if C.SIGNALS[signal]["kind"] == "count" else None
    pts = _series_points(est_used, raw, comp, t, window, signal, al.dow, C.SIGNALS[signal]["kind"], can_see_counts(user), extra_hide=dmask)
    if dmask is not None:
        apply_derived_mask(pts, dmask)
    return {"district": C.DISTRICTS[di], "signal": {"id": signal, **C.SIGNALS[signal]}, "as_of": iso(t), "points": pts,
            "note": "District totals include records that could only be placed at district level."}


# ---------------------------------------------------------------- incidents
@app.get("/api/incidents")
def incidents(scenario: str = "heatwave", degraded: bool = False, as_of: str | None = None, user=CurrentUser):
    run = get_run(scenario, degraded)
    t = as_of_day(as_of, scenario)
    rows = [incident_summary(run, i, t, user) for i in visible_incidents(run, t, user)]
    rows.sort(key=lambda r: ({"active": 0, "monitoring": 1, "closed": 2}[r["status"]], -TIER_RANK[r["max_tier"]], r["opened"]), reverse=False)
    n_open = sum(1 for r in rows if r["status"] != "closed")
    return {"as_of": iso(t), "incidents": rows, "counts": {"open": n_open, "past": len(rows) - n_open, "all": len(rows)}}


def _find_incident(run, inc_id, t, user):
    inc = next((i for i in run.incidents if i.id == inc_id), None)
    if inc is None:
        raise HTTPException(404, "Incident not found in this scenario")
    require_zone(user, inc.zone)
    if inc.opened > t:
        raise HTTPException(404, "This incident has not been raised by the selected replay date")
    return inc


@app.get("/api/incidents/{inc_id}")
def incident_detail(inc_id: str, scenario: str = "heatwave", degraded: bool = False, as_of: str | None = None, user=CurrentUser):
    run = get_run(scenario, degraded)
    t = as_of_day(as_of, scenario)
    inc = _find_incident(run, inc_id, t, user)
    zi = C.ZONE_INDEX[inc.zone]
    st = inc.state_at(t)
    rows = [h for h in inc.history if h["t"] <= t]
    active_rows = [h for h in rows if h["tier"] != "normal"]
    focus = active_rows[-1] if active_rows else rows[-1]
    contributions = row_contributions(run, zi, focus, user)
    peak_contrib = row_contributions(run, zi, st["peak"], user)
    cz = run.params["corroboration_z"]
    onsets = {}
    for h in rows:
        for c in h["contributions"]:
            if c["z"] is not None and c["z"] >= cz and c["signal"] not in onsets:
                onsets[c["signal"]] = c["day"]
    tier_for_playbook = st["max_tier"]
    pb = playbook(inc.track, tier_for_playbook, user["role"])
    ev_src = []
    for rc, pc in zip(focus["contributions"], contributions):
        rc = dict(rc)
        if C.SIGNALS[rc["signal"]]["family"] == "clinical" and C.SIGNALS[rc["signal"]]["kind"] == "count" and not pc["suppressed"]:
            if pc["pct_deviation"] is None or pc["z"] is None:
                rc.update(value=None, expected=None, z=None)
            else:  # ratio and z from released data only; no absolute count
                rc.update(value=1 + pc["pct_deviation"], expected=1.0, z=pc["z"])
        ev_src.append(rc)
    ev = evidence_lines(inc.track, ev_src, focus["t"])
    for line, c in zip(ev, contributions):
        if c.get("weekly_only"):
            line["text"] = f"{c['label']}: contributes {pct_text(c['share'])} of the score. Daily workplace counts are not released; see the weekly view under Workplaces & privacy."
            line["kind"] = "weekly_only"
        elif c["suppressed"]:
            line["text"] = f"{c['label']}: value suppressed (fewer than {C.SMALL_CELL_THRESHOLD} records)."
    loc = location_quality(run.al, t=t, district=C.ZONE_DISTRICT_IDX[zi])
    related = []
    for other in visible_incidents(run, t, user):
        if other.id == inc.id or other.track != inc.track:
            continue
        ost = other.state_at(t)
        if ost["first_active"] is not None and st["first_active"] is not None and abs(ost["first_active"] - st["first_active"]) <= 3:
            related.append({"id": other.id, "zone": zone_meta(other.zone), "max_tier": ost["max_tier"], "status": ost["status"]})
    return {
        **incident_summary(run, inc, t, user),
        "as_of": iso(t),
        "focus_date": iso(focus["t"]),
        "where": {"zone": zone_meta(inc.zone), "district": next(d for d in C.DISTRICTS if d["id"] == C.ZONES[zi]["district"]),
                  "spatial_level": "zone", "location_quality_scope": "clinic records of the zone's district", "location_quality": {"zone_resolved_share": num(loc["zone_resolved_share"], 3),
                                                                "levels": [{"id": l["id"], "label": l["label"], "confidence": l["confidence"], "share": num(l["share"], 3)} for l in loc["levels"]]}},
        "when": {"opened": iso(inc.opened), "action": iso(inc.action_day) if inc.action_day is not None and inc.action_day <= t else None,
                 "peak": iso(st["peak"]["t"]), "last_active": iso(st["last_active"]),
                 "closed": iso(inc.closed) if inc.closed is not None and inc.closed <= t else None,
                 "signal_onsets": [{"signal": s, "label": C.SIGNALS[s]["label"], "date": iso(d)} for s, d in sorted(onsets.items(), key=lambda x: x[1])]},
        "contributions": contributions, "peak_contributions": peak_contrib,
        "score": safe_score(run, zi, focus["t"], focus["contributions"], focus["score"]),
        "score_masked": safe_score(run, zi, focus["t"], focus["contributions"], focus["score"]) is None,
        "thresholds": {"watch": run.params["watch_score"], "action": run.params["action_score"], "corroboration_z": cz,
                       "min_families": run.params["min_families_action"]},
        "data_issues": focus["data_issues"], "weight_available": num(focus["weight_available"]),
        "confidence": focus["confidence"],
        "timeline": [{"date": iso(h["t"]), "tier": h["tier"], "score": safe_score(run, zi, h["t"], h["contributions"], h["score"]), "confidence": h["confidence"]["label"],
                      "weight_available": num(h["weight_available"])} for h in rows],
        "evidence": ev, "playbook": pb,
        "decisions": store.decisions(run.key, inc.id),
        "allowed_decisions": user["permissions"]["decision_statuses"],
        "related": related,
        "explanation_note": "Contribution shares show how the published scoring rule combined each signal. They are not causal attributions or calibrated probabilities.",
    }


class DecisionBody(BaseModel):
    scenario: str
    degraded: bool = False
    as_of: str | None = None
    status: str
    note: str | None = None


@app.post("/api/incidents/{inc_id}/decision")
def record_decision(inc_id: str, body: DecisionBody, user=CurrentUser):
    run = get_run(body.scenario, body.degraded)
    t = as_of_day(body.as_of, body.scenario)
    inc = _find_incident(run, inc_id, t, user)
    if body.status not in user["permissions"]["decision_statuses"]:
        raise HTTPException(403, "Your role cannot record this decision")
    rec = store.add_decision(run.key, inc.id, body.status, body.note, user["role"], user["id"], iso(t))
    return {"recorded": rec, "notification_sent": False}


@app.post("/api/demo/reset")
def reset_demo(user=CurrentUser):
    if user["role"] != "public_health_officer":
        raise HTTPException(403, "Only the Public Health Officer demo role can reset demo decisions")
    return {"decisions_removed": store.reset()}


# ---------------------------------------------------------------- workplaces (weekly release policy, see release.py)
RULE_TEXT = (f"Workplace counts are released only as complete Monday-Sunday weeks. Counts from 1 to {C.SMALL_CELL_THRESHOLD - 1} are shown as "
             f"<{C.SMALL_CELL_THRESHOLD}. When a small cell is hidden, a larger sibling is hidden too (or the total is withheld) so that the "
             "total minus the visible cells cannot reveal it. No daily workplace counts are released.")


def _wk(run, user, sig):
    return release.weekly(run, sig, release.role_flags(user["permissions"], sig))


def _week_window(k):
    a, b = release.week_days(k)
    return {"start": iso(a), "end": iso(b)}


def _weekly_unit(sig_id):
    sig = dict(C.SIGNALS[sig_id])
    sig["unit"] = sig["unit"].replace("/day", "/week")
    return {"id": sig_id, **sig}


def weekly_series(run, user, level, ident, sig, t, window):
    """Weekly series for a zone, district or site; used by every chart that shows a workplace count."""
    w = _wk(run, user, sig)
    weeks_all = release.complete_weeks(t)
    weeks = weeks_all[-max(1, -(-window // 7)):]
    if level == "zone":
        vals, hid, meta_ = w.zone[C.ZONE_INDEX[ident]], w.zone_h[C.ZONE_INDEX[ident]], {"zone": zone_meta(ident)}
    elif level == "district":
        di = C.DISTRICT_INDEX[ident]
        vals, hid, meta_ = w.dist[di], w.dist_h[di], {"district": C.DISTRICTS[di]}
    else:
        si = C.SITE_INDEX[ident]
        vals, hid, meta_ = w.site[si], w.site_h[si], {"site": ident}
    pts = release.series_points(vals, hid, weeks, iso)
    return {**meta_, "signal": _weekly_unit(sig), "as_of": iso(t), "points": pts, "tiers": {}, "grain": "week",
            "note": "Workplace counts are released as complete calendar weeks only (no daily values). Expected = mean of the four preceding weeks, shown only when all four are released."}


@app.get("/api/sites")
def sites(scenario: str = "heatwave", degraded: bool = False, as_of: str | None = None, user=CurrentUser):
    allowed = sites_in_scope(user)
    if not allowed:
        raise HTTPException(403, "Your role has no site-level access")
    run = get_run(scenario, degraded)
    t = as_of_day(as_of, scenario)
    weeks = release.complete_weeks(t)
    if not weeks:
        return {"as_of": iso(t), "week": None, "sites": [], "rule": RULE_TEXT, "window_note": "No complete week of workplace data is available yet on this replay date."}
    k, kp = weeks[-1], (weeks[-2] if len(weeks) > 1 else None)
    w_sl, w_fa = _wk(run, user, "sick_leave"), _wk(run, user, "fatigue_reports")
    rows = []
    for sid in allowed:
        si = C.SITE_INDEX[sid]
        s = C.SITES[si]
        sl, fa = release.cell(w_sl.site[si, k], w_sl.site_h[si, k]), release.cell(w_fa.site[si, k], w_fa.site_h[si, k])
        prev = release.cell(w_sl.site[si, kp], w_sl.site_h[si, kp]) if kp is not None else None
        rate = None if sl["suppressed"] else num(w_sl.site[si, k] / (s["workforce"] * 7) * 100, 2)
        zs = zone_status(run, t, C.ZONE_INDEX[s["zone"]])
        rows.append({
            "id": sid, "name": s["name"], "employer": C.EMPLOYERS[s["employer"]], "employer_id": s["employer"],
            "type": C.SITE_TYPES[s["type"]]["label"], "zone": zone_meta(s["zone"]), "workforce": s["workforce"],
            "outdoor_share": C.SITE_TYPES[s["type"]]["shares"][0],
            "sick_leave_week": sl, "fatigue_week": fa, "sick_leave_prev_week": prev,
            "absence_rate_per_100_worker_days": rate, "zone_status": zs["status"], "week": _week_window(k),
        })
    return {"as_of": iso(t), "week": _week_window(k), "sites": rows, "rule": RULE_TEXT,
            "window_note": f"Latest complete week, {_week_window(k)['start']} to {_week_window(k)['end']} (weeks are released only after reporting is final)."}


@app.get("/api/sites/{site}/cohorts")
def site_cohorts(site: str, scenario: str = "heatwave", degraded: bool = False, as_of: str | None = None, user=CurrentUser):
    require_site(user, site)
    if not user["permissions"]["cohort_drilldown"]:
        raise HTTPException(403, "Your role cannot open cohort breakdowns")
    run = get_run(scenario, degraded)
    t = as_of_day(as_of, scenario)
    si = C.SITE_INDEX[site]
    s = C.SITES[si]
    weeks = release.complete_weeks(t)
    site_meta = {"id": site, "name": s["name"], "employer": C.EMPLOYERS[s["employer"]], "zone": zone_meta(s["zone"])}
    shares = C.SITE_TYPES[s["type"]]["shares"]
    headcount = [round(s["workforce"] * sh) for sh in shares]
    hc_cells = {c["id"]: float(v) for c, v in zip(C.COHORTS, headcount)}
    hc_cells["total"] = float(sum(headcount))
    hc = suppress_row(hc_cells, total_key="total")
    if not weeks:
        return {"site": site_meta, "as_of": iso(t), "week": None, "weeks": [], "rows": [], "total": None, "rule": RULE_TEXT}
    k = weeks[-1]
    hist = weeks[-6:]
    w = {sig: _wk(run, user, sig) for sig in C.OCC_SIGNALS}

    def by_week(sig, ci):
        arr, hid = (w[sig].coh[si, ci], w[sig].coh_h[si, ci]) if ci is not None else (w[sig].site[si], w[sig].site_h[si])
        return [{**release.cell(arr[j], hid[j]), "start": iso(release.week_days(j)[0])} for j in hist]

    rows = []
    for ci, c in enumerate(C.COHORTS):
        rows.append({"cohort": c["id"], "label": c["label"], "headcount": hc[c["id"]],
                     "sick_leave_week": release.cell(w["sick_leave"].coh[si, ci, k], w["sick_leave"].coh_h[si, ci, k]),
                     "fatigue_week": release.cell(w["fatigue_reports"].coh[si, ci, k], w["fatigue_reports"].coh_h[si, ci, k]),
                     "sick_leave_by_week": by_week("sick_leave", ci)})
    total = {"cohort": "total", "label": "All cohorts", "headcount": hc["total"],
             "sick_leave_week": release.cell(w["sick_leave"].site[si, k], w["sick_leave"].site_h[si, k]),
             "fatigue_week": release.cell(w["fatigue_reports"].site[si, k], w["fatigue_reports"].site_h[si, k]),
             "sick_leave_by_week": by_week("sick_leave", None)}
    return {"site": site_meta, "as_of": iso(t), "week": _week_window(k), "weeks": [_week_window(j) for j in hist],
            "rows": rows, "total": total, "rule": RULE_TEXT}


@app.get("/api/sites/{site}/cohorts/{cohort}/daily")
def cohort_daily(site: str, cohort: str, scenario: str = "heatwave", degraded: bool = False, as_of: str | None = None, user=CurrentUser):
    require_site(user, site)
    if not user["permissions"]["cohort_drilldown"]:
        raise HTTPException(403, "Your role cannot open cohort breakdowns")
    raise HTTPException(410, "Daily workplace counts are not released. Use the weekly cohort table (release policy R1).")


@app.get("/api/sites/{site}/timeseries")
def site_timeseries(site: str, signal: str = "sick_leave", scenario: str = "heatwave", degraded: bool = False, as_of: str | None = None,
                    window: int = Query(42, ge=7, le=90), user=CurrentUser):
    require_site(user, site)
    if signal not in C.OCC_SIGNALS:
        raise HTTPException(404, "Site series exist for workplace signals only")
    run = get_run(scenario, degraded)
    t = as_of_day(as_of, scenario)
    return weekly_series(run, user, "site", site, signal, t, window)


# ---------------------------------------------------------------- export
@app.get("/api/export")
def export(kind: str = "zone_daily", scenario: str = "heatwave", degraded: bool = False, as_of: str | None = None, user=CurrentUser):
    if kind not in user["permissions"]["exports"]:
        raise HTTPException(403, "Your role cannot export this table")
    run = get_run(scenario, degraded)
    t = as_of_day(as_of, scenario)
    weeks = release.complete_weeks(t)
    buf = io.StringIO()
    w = csv.writer(buf)
    buf.write(f"# SentinelWorks export: SYNTHETIC DATA, replay as of {iso(t)}, scenario={scenario}, degraded={degraded}, role={user['role']}\n")
    buf.write(f"# Counts from 1 to {C.SMALL_CELL_THRESHOLD - 1} are suppressed as <{C.SMALL_CELL_THRESHOLD}. No person-level data.\n")
    buf.write("# Workplace counts (sick leave, fatigue) are released as complete Monday-Sunday weeks only; withheld cells read <5 or masked. Clinic and environmental rows are daily.\n")
    if kind == "zone_daily":
        w.writerow(["date", "zone", "signal", "observed", "expected", "z", "status", "tier_heat", "tier_respiratory", "grain"])
        for zid in zones_in_scope(user):
            zi = C.ZONE_INDEX[zid]
            for sig in user["permissions"]["zone_signals"]:
                if sig in C.OCC_SIGNALS:
                    wk = _wk(run, user, sig)
                    for p in release.series_points(wk.zone[zi], wk.zone_h[zi], weeks, iso):
                        w.writerow([p["date"], zid, sig, p["reported_so_far"]["display"], p["expected"] if p["expected"] is not None else "", "", p["status"], "", "", "week"])
                    continue
                est, raw, comp = zone_series(run.al, sig, t, zi, 0.0)
                est_used = np.where(comp >= run.params["min_completeness"], est, np.nan)
                pts = _series_points(est_used, raw, comp, t, t + 1 - C.WARMUP_DAYS, sig, run.al.dow, C.SIGNALS[sig]["kind"], False)
                for p in pts:
                    d = C.date_to_day(p["date"])
                    w.writerow([p["date"], zid, sig, "<5" if p["suppressed"] else p["observed"], p["expected"], p["z"], p["status"],
                                run.tiers.get((d, zi, "heat"), ""), run.tiers.get((d, zi, "respiratory"), ""), "day"])
    else:
        w.writerow(["week_start", "week_end", "site", "employer", "zone", "sick_leave", "fatigue_reports"])
        w_sl, w_fa = _wk(run, user, "sick_leave"), _wk(run, user, "fatigue_reports")
        for sid in sites_in_scope(user):
            si = C.SITE_INDEX[sid]
            s = C.SITES[si]
            for k in weeks:
                a, b = release.week_days(k)
                w.writerow([iso(a), iso(b), sid, s["employer"], s["zone"], release.cell(w_sl.site[si, k], w_sl.site_h[si, k])["display"],
                            release.cell(w_fa.site[si, k], w_fa.site_h[si, k])["display"]])
    buf.seek(0)
    fname = f"sentinelworks_{kind}_{scenario}{'_degraded' if degraded else ''}_{iso(t)}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ---------------------------------------------------------------- validation & impact
@app.get("/api/validation")
def validation(user=CurrentUser):
    path = C.DATA_DIR / "validation_results.json"
    if not path.exists():
        raise HTTPException(503, "Validation results have not been computed yet. Run: python -m sentinel.validation")
    data = json.loads(path.read_text())
    if data.get("params") != C.detection_params():
        raise HTTPException(503, "Validation settings have changed. Re-run the back-test.")
    return data


@app.post("/api/validation/rerun")
def validation_rerun(user=CurrentUser):
    if user["role"] != "public_health_officer":
        raise HTTPException(403, "Only the Public Health Officer demo role can re-run the back-test")
    if not _validation_lock.acquire(blocking=False):
        raise HTTPException(409, "A validation run is already in progress")
    try:
        from .validation import run_all
        result = run_all(verbose=False)
        demo_run.cache_clear()
        return {"ok": True, "generated_at": result["generated_at"], "runtime_seconds": result["runtime_seconds"]}
    finally:
        _validation_lock.release()


@lru_cache(maxsize=8)
def impact_example(scenario, params):
    return evaluate(SCENARIOS[scenario], seed=901, params=dict(params))


@app.get("/api/impact")
def impact(scenario: str = "heatwave", user=CurrentUser):
    """Measured inputs for the benefit estimator. The estimator's assumptions are set in the UI."""
    if scenario not in SCENARIOS:
        raise HTTPException(404, "Unknown scenario")
    # Dedicated synthetic population: these published estimates cannot reveal replay counts.
    run = impact_example(scenario, tuple(sorted(C.detection_params().items())))
    truth = run.al.world.truth
    path = C.DATA_DIR / "validation_results.json"
    lead = None
    if path.exists():
        v = json.loads(path.read_text())
        lc = v["holdout"]["conditions"]["standard"]["lead_vs_comparator_days"] if v.get("params") == run.params else None
        lead = {"median": lc["median"], "p25": lc["p25"], "p75": lc["p75"], "n": lc["n"]} if lc else None
    zones = sorted({e["zone"] for e in truth}, key=C.ZONE_IDS.index)
    workforce_outdoor = sum(s["workforce"] * C.SITE_TYPES[s["type"]]["shares"][0] for s in C.SITES if s["zone"] in zones)
    excess_visits = 0.0
    excess_absence = 0.0
    after_sw = 0.0
    after_cmp = 0.0
    abs_after_sw = 0.0
    abs_after_cmp = 0.0
    t_end = C.N_DAYS - 1
    per_event = []
    for e in truth:
        zi = C.ZONE_INDEX[e["zone"]]
        sig = C.TRACKS[e["track"]]["clinical_signal"]
        est, raw, comp = zone_series(run.al, sig, t_end, zi, 0.0)
        occ, _, _ = zone_series(run.al, "sick_leave", t_end, zi, 0.0)
        acts = [i.action_day for i in run.incidents if i.zone == e["zone"] and i.track == e["track"] and i.action_day is not None
                and e["exposure_start"] - 1 <= i.action_day <= e["end"] + 3]
        sw_day = min(acts) if acts else None
        cmp_days = [d for d in run.comparator.get((zi, e["track"]), []) if e["exposure_start"] - 1 <= d <= e["end"] + 7]
        cmp_day = min(cmp_days) if cmp_days else None
        ev_excess = 0.0
        ev_sw = ev_cmp = 0.0
        for d in range(e["exposure_start"], e["end"] + 3):
            ex, _ = baseline_at(est, d, run.al.dow, sig)
            eo, _ = baseline_at(occ, d, run.al.dow, "sick_leave")
            xv = max(est[d] - ex, 0)
            ev_excess += xv
            xa = max(occ[d] - eo, 0)
            excess_absence += xa
            # an action needs about a day to take effect: count excess from the day after the alert
            if sw_day is not None and d > sw_day:
                ev_sw += xv
                abs_after_sw += xa
            if cmp_day is not None and d > cmp_day:
                ev_cmp += xv
                abs_after_cmp += xa
        excess_visits += ev_excess
        after_sw += ev_sw
        after_cmp += ev_cmp
        per_event.append({"zone": e["zone"], "track": e["track"], "sentinel_action": iso(sw_day), "comparator": iso(cmp_day),
                          "excess_visits": num(ev_excess, 1)})
    return {
        "scenario": scenario, "event_zones": [zone_meta(z) for z in zones],
        "measured": {
            "excess_clinic_visits": num(excess_visits, 0), "excess_sick_leave_days": num(excess_absence, 0),
            "outdoor_workforce_in_event_zones": round(workforce_outdoor),
            "lead_time_vs_comparator_days": lead,
            "event_days": sum(e["end"] - e["exposure_start"] + 1 for e in truth) if truth else 0,
            "share_of_excess_after_sentinel_alert": num(after_sw / excess_visits, 3) if excess_visits else None,
            "share_of_excess_after_comparator_alert": num(after_cmp / excess_visits, 3) if excess_visits else None,
            "share_of_excess_absence_after_sentinel_alert": num(abs_after_sw / excess_absence, 3) if excess_absence else None,
            "share_of_excess_absence_after_comparator_alert": num(abs_after_cmp / excess_absence, 3) if excess_absence else None,
            "per_event": per_event,
        },
        "units": {"excess_clinic_visits": "clinic visits (population in the zone's clinics; one person can make several visits)",
                  "excess_sick_leave_days": "sick-leave days recorded by the sites in the event zones (the only workday measure in the data)"},
        "note": "Measured inputs come from a separate synthetic example (seed 901) and held-out validation runs. Any benefit computed from them is an estimate that depends on the assumptions entered.",
    }


# ---------------------------------------------------------------- static frontend
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        f = FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
