"""Baseline anomaly detection and transparent signal fusion.

Baseline for day d: the 28 days ending GUARD_DAYS before d (past-only), with multiplicative
day-of-week factors for count signals. Deviation is expressed as a z-score and a percentage.
Fusion is a published weighted sum of clipped z-scores; contributions are shares of that sum.
Contribution shares describe the scoring rule; they are not causal attributions or probabilities.
"""
from __future__ import annotations

import numpy as np

from . import config as C
from .pipeline import Aligned, completeness_at, zone_series

ENV_SD_FLOOR = {"heat_index": 0.6, "pm25": 3.0}
# Exposure-only Watch requires both a large deviation and a published guidance exceedance.
GUIDANCE_THRESHOLDS = {"heat_index": C.HEAT_INDEX_DANGER_C, "pm25": C.PM25_UNHEALTHY}
MIN_BASELINE_POINTS = 14
EXPOSURE_WINDOW = 3  # days (72 h) over which environmental exposure peak is taken


def baseline_at(values: np.ndarray, d: int, dow: np.ndarray, sig: str):
    """Expected value and spread for day d using only values strictly before d - GUARD_DAYS."""
    end = d - C.GUARD_DAYS + 1  # exclusive index: window ends GUARD_DAYS before d
    start = end - C.BASELINE_WINDOW
    if start < 0:
        return np.nan, np.nan
    win = values[start:end]
    ok = ~np.isnan(win)
    if ok.sum() < MIN_BASELINE_POINTS:
        return np.nan, np.nan
    if C.SIGNALS[sig]["kind"] == "count":
        s2 = max(0, end - C.DOW_WINDOW)
        long = values[s2:end]
        ldow = dow[s2:end]
        lok = ~np.isnan(long)
        overall = long[lok].mean() if lok.any() else np.nan
        factors = np.ones(7)
        if overall and overall > 0:
            for w in range(7):
                sel = lok & (ldow == w)
                n = sel.sum()
                if n:
                    f = long[sel].mean() / overall
                    factors[w] = (n * f + 4 * 1.0) / (n + 4)  # shrink towards 1
        wdow = dow[start:end][ok]
        deseason = win[ok] / factors[wdow]
        level = deseason.mean()
        expected = level * factors[dow[d]]
        resid = win[ok] - level * factors[wdow]
        sd = max(resid.std(ddof=1), np.sqrt(max(expected, 1.0)))
    else:
        expected = win[ok].mean()
        sd = max(win[ok].std(ddof=1), ENV_SD_FLOOR[sig])
    return float(expected), float(sd)


def signal_state(al: Aligned, sig: str, t: int, zi: int, params: dict) -> dict:
    est, raw, comp = zone_series(al, sig, t, zi, params["min_completeness"])
    # Privacy (release rule R8): the baseline of a CLINICAL count ignores days whose count is withheld (1-4 visits). Every
    # published score, share, z and band then depends only on released data (plus that day's own withheld value), so a hidden
    # count cannot be recovered from the next 28 days of published baselines. Cost: a slightly higher baseline.
    est_b = est
    if C.SIGNALS[sig]["family"] == "clinical":
        r = np.rint(np.asarray(raw, dtype=float))
        est_b = np.where((r > 0) & (r < C.SMALL_CELL_THRESHOLD), np.nan, est)
    usable = np.nonzero(~np.isnan(est))[0]
    state = {"signal": sig, "family": C.SIGNALS[sig]["family"], "as_of": t}
    if len(usable) == 0 or usable[-1] < t - 7:
        state.update(status="missing", day=None, value=None, expected=None, z=None, pct=None, reporting_gap=False,
                     completeness=None, raw=None)
        return state
    d = int(usable[-1])
    expected, sd = baseline_at(est_b, d, al.dow, sig)
    value = float(est[d])
    if np.isnan(expected):
        state.update(status="baseline_insufficient", day=d, value=value, expected=None, z=None, pct=None,
                     reporting_gap=False, completeness=float(comp[d]), raw=float(raw[d]))
        return state
    z = (value - expected) / sd
    if C.SIGNALS[sig]["kind"] == "value":
        # Lag-aware exposure: health effects follow exposure, so fusion uses the peak deviation
        # over the most recent EXPOSURE_WINDOW days (each day against its own past-only baseline).
        best = (z, d, value, expected, sd)
        for dd in range(max(0, d - EXPOSURE_WINDOW + 1), d):
            if np.isnan(est[dd]):
                continue
            e2, s2 = baseline_at(est, dd, al.dow, sig)
            if np.isnan(e2):
                continue
            z2 = (est[dd] - e2) / s2
            if z2 > best[0]:
                best = (z2, dd, float(est[dd]), e2, s2)
        state["exposure_peak"] = {"z": float(best[0]), "day": int(best[1]), "value": best[2], "expected": best[3]}
        state["latest"] = {"z": float(z), "day": d, "value": value, "expected": expected}
        z, value, expected, sd = float(best[0]), best[2], best[3], best[4]
        d_used = int(best[1])
    else:
        d_used = d
    status = "current" if d >= t - params["stale_days"] else "delayed"
    gap = bool(C.SIGNALS[sig]["kind"] == "count" and z <= -3.0)
    state.update(status=status, day=d_used, newest_day=d, value=value, expected=expected, sd=sd, z=float(z),
                 pct=float(value / expected - 1) if expected else None, reporting_gap=gap,
                 completeness=float(comp[d]), raw=float(raw[d_used]))
    return state


def fuse(track: str, states: dict, params: dict) -> dict:
    """Combine per-signal states into a composite score, contributions and a raw tier."""
    weights = C.TRACKS[track]["weights"]
    zc = params["z_clip"]
    parts, avail_w, max_z, families = {}, 0.0, 0.0, set()
    data_issues = []
    for sig, w in weights.items():
        st = states[sig]
        if st["status"] in ("current", "delayed") and st["z"] is not None:
            avail_w += w * (1.0 if st["status"] == "current" else 0.5)
            zpos = min(max(st["z"], 0.0), zc)
            parts[sig] = w * zpos
            max_z = max(max_z, st["z"])
            if st["z"] >= params["corroboration_z"]:
                families.add(st["family"])
            if st["status"] == "delayed":
                data_issues.append({"signal": sig, "issue": "delayed", "detail": f"newest usable day is {st['as_of'] - st['day']} days old"})
            if st["reporting_gap"]:
                data_issues.append({"signal": sig, "issue": "reporting_gap", "detail": "count far below expected; possible reporting gap, not treated as low risk"})
        else:
            parts[sig] = None
            data_issues.append({"signal": sig, "issue": st["status"], "detail": "no usable data" if st["status"] == "missing" else "baseline not yet available"})
    score = float(sum(v for v in parts.values() if v is not None))
    contributions = []
    for sig, w in weights.items():
        st = states[sig]
        part = parts[sig]
        contributions.append({
            "signal": sig, "family": C.SIGNALS[sig]["family"], "weight": w,
            "z": st.get("z"), "pct_deviation": st.get("pct"), "value": st.get("value"), "expected": st.get("expected"),
            "day": st.get("day"), "status": st["status"], "points": part,
            "share": (part / score) if (part is not None and score > 0) else (0.0 if part is not None else None),
        })
    total_w = sum(weights.values())
    weight_share = avail_w / total_w
    candidate_action = score >= params["action_score"] and len(families) >= params["min_families_action"]
    score_watch = score >= params["watch_score"]
    guidance_watch = False
    for sig in weights:
        st = states[sig]
        thr = GUIDANCE_THRESHOLDS.get(sig)
        latest = st.get("latest")
        if thr and st["status"] == "current" and latest and latest["z"] >= params["watch_single_z"] and latest["value"] >= thr:
            guidance_watch = True
    if candidate_action:
        raw_tier = "action"
    elif score_watch or guidance_watch:
        raw_tier = "watch"
    else:
        raw_tier = "normal"
    clinical = C.TRACKS[track]["clinical_signal"]
    insufficient = weight_share < params["min_weight_available"] or states[clinical]["status"] in ("missing", "baseline_insufficient") \
        or any(i["issue"] == "reporting_gap" and C.SIGNALS[i["signal"]]["family"] == "clinical" for i in data_issues)
    exposure_unavailable = any(
        states[sig]["status"] != "current" or states[sig].get("newest_day") != states[sig]["as_of"]
        for sig in weights if sig in C.ENV_SIGNALS
    )
    return {
        "track": track, "score": score, "contributions": contributions, "families": sorted(families),
        "candidate_action": bool(candidate_action), "score_watch": bool(score_watch), "guidance_watch": bool(guidance_watch),
        "weight_available": weight_share, "raw_tier": raw_tier, "insufficient": bool(insufficient or exposure_unavailable or states[clinical]["status"] == "delayed"),
        "data_issues": data_issues, "max_z": max_z,
    }


def data_confidence(weight_available: float, zone_resolved_share: float | None) -> dict:
    """Descriptive confidence in the *data* behind a status (not a probability of an event)."""
    loc = zone_resolved_share if zone_resolved_share is not None else 0.0
    value = 0.6 * weight_available + 0.4 * min(loc / 0.8, 1.0)
    label = "High" if value >= 0.85 else "Medium" if value >= 0.6 else "Low"
    return {"value": round(value, 2), "label": label,
            "basis": "Share of signal weight with current data (60%) and share of clinic records resolved to a zone (40%)."}


def comparator_flag(al: Aligned, sig: str, t: int, zi: int, z_threshold: float = 3.0, complete: float = 0.95) -> bool:
    """Conventional single-signal surveillance: clinic count z >= 3 on the two newest *complete* days."""
    est, _, comp = zone_series(al, sig, t, zi, complete)
    usable = np.nonzero(~np.isnan(est))[0]
    if len(usable) < 2:
        return False
    zs = []
    for d in usable[-2:]:
        e, sd = baseline_at(est, int(d), al.dow, sig)
        if np.isnan(e):
            return False
        zs.append((est[d] - e) / sd)
    return all(z >= z_threshold for z in zs)


__all__ = ["baseline_at", "signal_state", "fuse", "data_confidence", "comparator_flag", "completeness_at"]
