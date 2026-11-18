"""Back-testing harness.

* Tuning seeds (1-20) are used only to choose detection thresholds from a small grid.
* Evaluation seeds (101-130) are held out and produce every reported metric.
* Each seed is a randomised synthetic world with one heatwave (1-2 zones), one air-quality
  episode, and one heat spike with no health response (a decoy that should not reach Action).
* Every metric is computed from actual pipeline runs; nothing here is hard-coded.

Run:  python -m sentinel.validation   (writes data/validation_results.json, data/tuned_params.json)
"""
from __future__ import annotations

import itertools
import json
import time
import warnings
from datetime import datetime, timezone

import numpy as np

from . import config as C
from .engine import build_incidents, compute_comparator, compute_location, compute_states
from .pipeline import align
from . import synth as SYNTH
from .synth import SCENARIOS, generate, random_scenario

warnings.filterwarnings("ignore", category=RuntimeWarning)

TUNING_SEEDS = list(range(1, 21))
EVAL_SEEDS = list(range(101, 131))
MATCH_BEFORE, MATCH_AFTER = 1, 3  # Action day must fall in [exposure_start - 1, end + 3]
COMPARATOR_AFTER = 7
STRESS_SEEDS = list(range(301, 313))  # never used for tuning or for the main held-out evaluation
STRESS_LAGS = [0, 1, 2, 3, 4]
GRID = {"action_score": [1.5, 1.8, 2.1, 2.4], "corroboration_z": [1.5, 2.0, 2.5]}
DAYS = range(C.WARMUP_DAYS, C.N_DAYS)
ZONE_MONTHS = len(C.ZONES) * len(DAYS) / 30.0
RESULTS_PATH = C.DATA_DIR / "validation_results.json"


def _prepare(scenario, seed, degraded, params):
    world = generate(scenario, seed=seed, degraded=degraded)
    al = align(world)
    states = compute_states(al, params, DAYS)
    loc = compute_location(al, DAYS)
    return al, states, loc


def _weekly_report_day(day: int) -> int:
    """Conventional weekly report: Mon-Sun week containing `day`, published the following Wednesday."""
    dow = C.day_to_date(day).weekday()
    sunday = day + (6 - dow)
    return sunday + 3


def score_run(truth, incidents, comparator, al=None):
    events = []
    action_incs = [i for i in incidents if i.action_day is not None]
    matched_incs = set()
    events_matched = 0
    for ev in truth:
        lo, hi = ev["exposure_start"] - MATCH_BEFORE, ev["end"] + MATCH_AFTER
        acts = [i for i in action_incs if i.zone == ev["zone"] and i.track == ev["track"] and lo <= i.action_day <= hi]
        # first day in the event window on which the zone/track was at Watch or higher
        active_days = [h["t"] for i in incidents if i.zone == ev["zone"] and i.track == ev["track"]
                       for h in i.history if h["tier"] != "normal" and lo <= h["t"] <= hi]
        for i in acts:
            matched_incs.add(i.id)
        events_matched += 1 if acts else 0
        first_action = min((i.action_day for i in acts), default=None)
        first_watch = min(active_days, default=None)
        zi = C.ZONE_INDEX[ev["zone"]]
        cmp_days = [t for t in comparator.get((zi, ev["track"]), []) if lo <= t <= ev["end"] + COMPARATOR_AFTER]
        first_cmp = min(cmp_days, default=None)
        events.append({
            "zone": ev["zone"], "track": ev["track"], "exposure_start": ev["exposure_start"], "health_onset": ev["health_onset"],
            "detected_action": first_action is not None, "detected_watch": first_watch is not None,
            "action_day": first_action, "watch_day": first_watch, "comparator_day": first_cmp,
            "weekly_report_day": _weekly_report_day(ev["health_onset"]),
        })
    unmatched_action = [i for i in action_incs if i.id not in matched_incs]
    watch_only_unmatched = []
    for i in incidents:
        if i.action_day is not None:
            continue
        linked = any(i.zone == ev["zone"] and i.track == ev["track"] and ev["exposure_start"] - MATCH_BEFORE <= i.opened <= ev["end"] + MATCH_AFTER for ev in truth)
        if not linked:
            watch_only_unmatched.append(i)
    # comparator false alarms: runs of flagged days not linked to an injected event
    cmp_false = 0
    for (zi, track), ts in comparator.items():
        runs = []
        for t in ts:
            if runs and t - runs[-1][-1] <= 1:
                runs[-1].append(t)
            else:
                runs.append([t])
        for r in runs:
            if not any(ev["zone"] == C.ZONE_IDS[zi] and ev["track"] == track and ev["exposure_start"] - MATCH_BEFORE <= r[0] <= ev["end"] + COMPARATOR_AFTER for ev in truth):
                cmp_false += 1
    cmp_true = sum(1 for e in events if e["comparator_day"] is not None)
    # One true positive per detected event: further Action incidents for the same event are duplicates
    # (counted as alerts, not as extra true positives), so repeated alerts cannot inflate PPV.
    return {"events": events, "n_action": len(action_incs), "n_action_true": events_matched,
            "n_action_duplicate": len(matched_incs) - events_matched,
            "n_action_false": len(unmatched_action), "n_watch_only_unlinked": len(watch_only_unmatched),
            "n_incidents": len(incidents), "comparator_false": cmp_false, "comparator_true": cmp_true}


def _summarise(scored: list, label: str) -> dict:
    events = [e for s in scored for e in s["events"]]
    n_ev = len(events)
    n_act = sum(s["n_action"] for s in scored)
    n_act_true = sum(s["n_action_true"] for s in scored)
    n_false = sum(s["n_action_false"] for s in scored)
    det = [e for e in events if e["detected_action"]]
    leads_cmp = [e["comparator_day"] - e["action_day"] for e in det if e["comparator_day"] is not None]
    leads_week = [e["weekly_report_day"] - e["action_day"] for e in det]
    watch_leads_cmp = [e["comparator_day"] - e["watch_day"] for e in events if e["detected_watch"] and e["comparator_day"] is not None]
    rel_onset = [e["action_day"] - e["health_onset"] for e in det]
    cmp_detected = sum(1 for e in events if e["comparator_day"] is not None)
    cmp_alarms = cmp_detected + sum(s["comparator_false"] for s in scored)
    runs = len(scored)

    def stats(v):
        if not v:
            return None
        a = np.array(v, dtype=float)
        return {"n": len(v), "median": float(np.median(a)), "mean": float(a.mean()), "p25": float(np.percentile(a, 25)),
                "p75": float(np.percentile(a, 75)), "min": float(a.min()), "max": float(a.max()), "values": [float(x) for x in v]}

    by_track = {}
    for track in C.TRACKS:
        ev_t = [e for e in events if e["track"] == track]
        by_track[track] = {
            "events": len(ev_t),
            "sensitivity_action": (sum(e["detected_action"] for e in ev_t) / len(ev_t)) if ev_t else None,
            "sensitivity_watch": (sum(e["detected_watch"] for e in ev_t) / len(ev_t)) if ev_t else None,
            "comparator_sensitivity": (sum(e["comparator_day"] is not None for e in ev_t) / len(ev_t)) if ev_t else None,
            "median_lead_vs_comparator_days": (float(np.median([e["comparator_day"] - e["action_day"] for e in ev_t if e["detected_action"] and e["comparator_day"] is not None])) if any(e["detected_action"] and e["comparator_day"] is not None for e in ev_t) else None),
        }
    return {
        "label": label, "runs": runs, "events": n_ev,
        "sensitivity_action": (len(det) / n_ev) if n_ev else None,
        "sensitivity_watch": (sum(e["detected_watch"] for e in events) / n_ev) if n_ev else None,
        "ppv_action": (n_act_true / n_act) if n_act else None,
        "action_alerts": n_act, "action_true": n_act_true, "false_action_alerts": n_false,
        "duplicate_action_alerts": sum(s["n_action_duplicate"] for s in scored),
        "events_detected_action": len(det), "events_detected_with_comparator": len(leads_cmp),
        "false_action_per_zone_month": n_false / (ZONE_MONTHS * runs),
        "watch_only_unlinked_per_zone_month": sum(s["n_watch_only_unlinked"] for s in scored) / (ZONE_MONTHS * runs),
        "lead_vs_comparator_days": stats(leads_cmp),
        "lead_vs_weekly_report_days": stats(leads_week),
        "watch_lead_vs_comparator_days": stats(watch_leads_cmp),
        "action_day_relative_to_health_onset": stats(rel_onset),
        "events_detected_but_missed_by_comparator": sum(1 for e in det if e["comparator_day"] is None),
        "comparator": {
            "definition": "Single-signal clinic surveillance: syndrome count z >= 3 on the two newest complete days (same data availability).",
            "sensitivity": cmp_detected / n_ev if n_ev else None,
            "ppv": (cmp_detected / cmp_alarms) if cmp_alarms else None,
            "false_alarms_per_zone_month": sum(s["comparator_false"] for s in scored) / (ZONE_MONTHS * runs),
        },
        "by_track": by_track,
    }


def _latency(world) -> dict:
    out = {}
    cl = world.clinic
    for si, sig in enumerate(C.CLINIC_SIGNALS):
        lag = (cl["avail"] - cl["obs"])[cl["signal"] == si]
        out[sig] = {"median_days": float(np.median(lag)), "p90_days": float(np.percentile(lag, 90)), "max_days": float(lag.max())}
    for sig in C.OCC_SIGNALS:
        split = world.occ[sig].sum(axis=(0, 1, 2))
        lags = np.repeat(np.arange(len(split)), split)
        out[sig] = {"median_days": float(np.median(lags)), "p90_days": float(np.percentile(lags, 90)), "max_days": float(lags.max())}
    for sig in C.ENV_SIGNALS:
        out[sig] = {"median_days": 0.0, "p90_days": 0.0, "max_days": 0.0,
                    "missing_share": float(np.isnan(world.env[sig]).mean())}
    return out


def tune(verbose=True) -> dict:
    base = dict(C.DEFAULT_DETECTION)
    prepared = []
    for seed in TUNING_SEEDS:
        sc = random_scenario(np.random.default_rng(seed), seed)
        al, states, loc = _prepare(sc, seed, False, base)
        prepared.append((al, states, loc, compute_comparator(al, DAYS)))
    results = []
    for combo in itertools.product(*GRID.values()):
        params = {**base, **dict(zip(GRID.keys(), combo))}
        scored = []
        for al, states, loc, cmp in prepared:
            _, _, incidents, _, _ = build_incidents(states, loc, params, DAYS)
            scored.append(score_run(al.world.truth, incidents, cmp))
        s = _summarise(scored, "tuning")
        sens, ppv = s["sensitivity_action"] or 0, s["ppv_action"] or 0
        f1 = 2 * sens * ppv / (sens + ppv) if sens + ppv else 0
        results.append({"params": dict(zip(GRID.keys(), combo)), "sensitivity": sens, "ppv": ppv, "f1": f1,
                        "false_per_zone_month": s["false_action_per_zone_month"]})
        if verbose:
            print("tune", results[-1])
    # Select highest F1; ties broken by fewer false alerts. Budget: <= 0.25 false Action alerts per zone-month.
    eligible = [r for r in results if r["false_per_zone_month"] <= 0.25] or results
    best = max(eligible, key=lambda r: (round(r["f1"], 3), -r["false_per_zone_month"]))
    return {"selected": best, "grid": results, "seeds": TUNING_SEEDS,
            "criterion": "Maximise F1 of Action-tier detection subject to <= 0.25 false Action alerts per zone-month."}


def evaluate_holdout(params: dict, verbose=True) -> dict:
    out = {}
    latency = {}
    location = {}
    for degraded in (False, True):
        scored = []
        for seed in EVAL_SEEDS:
            sc = random_scenario(np.random.default_rng(seed), seed)
            al, states, loc = _prepare(sc, seed, degraded, params)
            _, _, incidents, _, _ = build_incidents(states, loc, params, DAYS)
            scored.append(score_run(al.world.truth, incidents, compute_comparator(al, DAYS)))
            if seed == EVAL_SEEDS[0]:
                latency["degraded" if degraded else "standard"] = _latency(al.world)
                n_in = len(al.world.clinic["obs"])
                levels = {int(k): int((al.rec_level == k).sum()) for k in (0, 1, 4, 2, 3)}
                location["degraded" if degraded else "standard"] = {
                    "records_in": n_in, "records_retained": int(sum(levels.values())), "records_dropped": n_in - int(sum(levels.values())),
                    "by_level": {lvl: cnt for lvl, cnt in zip(["zone_gps", "zone_site", "zone_postal", "district", "region"], levels.values())},
                }
        key = "degraded" if degraded else "standard"
        out[key] = _summarise(scored, key)
        if verbose:
            print(key, {k: out[key][k] for k in ("sensitivity_action", "ppv_action", "false_action_per_zone_month")})
    return {"conditions": out, "latency": latency, "location": location}


def holdout_tradeoff(params: dict, verbose=True) -> dict:
    """Every grid setting evaluated on the held-out seeds (standard inputs). Display only: thresholds were
    chosen on the tuning seeds, and nothing here feeds back into that choice."""
    prepared = []
    for seed in EVAL_SEEDS:
        sc = random_scenario(np.random.default_rng(seed), seed)
        al, states, loc = _prepare(sc, seed, False, params)
        prepared.append((al, states, loc, compute_comparator(al, DAYS)))
    rows = []
    cmp_point = None
    for combo in itertools.product(*GRID.values()):
        p = {**params, **dict(zip(GRID.keys(), combo))}
        scored = []
        for al, states, loc, cmp in prepared:
            _, _, incidents, _, _ = build_incidents(states, loc, p, DAYS)
            scored.append(score_run(al.world.truth, incidents, cmp))
        sm = _summarise(scored, "holdout-grid")
        rows.append({"params": dict(zip(GRID.keys(), combo)), "sensitivity": sm["sensitivity_action"], "ppv": sm["ppv_action"],
                     "false_per_zone_month": sm["false_action_per_zone_month"], "action_alerts": sm["action_alerts"],
                     "false_action_alerts": sm["false_action_alerts"], "events": sm["events"]})
        cmp_point = sm["comparator"]
        if verbose:
            print("holdout grid", rows[-1]["params"], round(rows[-1]["sensitivity"], 3), round(rows[-1]["ppv"] or 0, 3))
    return {"grid": rows, "comparator": cmp_point, "used_for_selection": False}


def stress_scenario(seed: int, lag: int, compound: bool = False):
    sc = random_scenario(np.random.default_rng(seed), seed)
    sc.health_lag = lag
    if compound:  # heat and PM2.5 hit the same zone at the same time: two risk tracks fire together
        heat, aq = sc.events[0], sc.events[1]
        aq["zones"] = [heat["zones"][0]]
        aq["start"] = heat["start"] + 1
    return sc


def stress_test(params: dict, verbose=True) -> dict:
    """Held-out robustness check. Thresholds stay fixed (params); only the world changes. Health signals lag the exposure by 0-4
    days (0 = simultaneous: environment, clinic and workplace signals all move on the same day), plus a compound case."""
    rows = []
    conditions = [(f"lag {L} d" + (" (simultaneous)" if L == 0 else ""), L, False) for L in STRESS_LAGS] + [("compound heat + PM2.5, same zone, lag 1 d", 1, True)]
    for label, lag, compound in conditions:
        scored = []
        for seed in STRESS_SEEDS:
            sc = stress_scenario(seed, lag, compound)
            al, states, loc = _prepare(sc, seed, False, params)
            _, _, incidents, _, _ = build_incidents(states, loc, params, DAYS)
            scored.append(score_run(al.world.truth, incidents, compute_comparator(al, DAYS)))
        sm = _summarise(scored, label)
        rows.append({"label": label, "health_lag_days": lag, "compound": compound, "runs": sm["runs"], "events": sm["events"],
                     "sensitivity_action": sm["sensitivity_action"], "sensitivity_watch": sm["sensitivity_watch"], "ppv_action": sm["ppv_action"],
                     "action_alerts": sm["action_alerts"], "action_true": sm["action_true"], "false_action_alerts": sm["false_action_alerts"],
                     "false_action_per_zone_month": sm["false_action_per_zone_month"],
                     "comparator_sensitivity": sm["comparator"]["sensitivity"],
                     "lead_vs_comparator_median": (sm["lead_vs_comparator_days"] or {}).get("median"),
                     "lead_vs_comparator_n": (sm["lead_vs_comparator_days"] or {}).get("n", 0),
                     "action_day_vs_health_onset_median": (sm["action_day_relative_to_health_onset"] or {}).get("median"),
                     "events_missed": sm["events"] - sm["events_detected_action"]})
        if verbose:
            print("stress", label, {k: rows[-1][k] for k in ("sensitivity_action", "ppv_action", "lead_vs_comparator_median")})
    return {"seeds": STRESS_SEEDS, "thresholds_fixed": True, "params": {k: params[k] for k in ("action_score", "corroboration_z")},
            "definition": "Held-out worlds (seeds not used for tuning or the main evaluation). Thresholds are NOT re-tuned. The health response (clinic visits, sick leave, fatigue) starts `lag` days after exposure onset; 0 means every signal moves on the same day. Truth windows are extended by the lag so a response that has not begun yet is not scored as a miss by construction.",
            "rows": rows}


def demo_scenarios(params: dict) -> list:
    rows = []
    for sid, sc in SCENARIOS.items():
        for degraded in (False, True):
            al, states, loc = _prepare(sc, 42, degraded, params)
            _, _, incidents, events, watch_evals = build_incidents(states, loc, params, DAYS)
            s = score_run(al.world.truth, incidents, compute_comparator(al, DAYS))
            rows.append({
                "scenario": sid, "label": sc.label, "degraded": degraded, "true_events": len(al.world.truth),
                "action_alerts": s["n_action"], "false_action_alerts": s["n_action_false"],
                "incidents": s["n_incidents"], "alert_events": len(events), "zone_days_at_or_above_watch": watch_evals,
                "events": s["events"],
            })
    return rows


def run_all(verbose=True) -> dict:
    t0 = time.time()
    tuning = tune(verbose)
    params = {**C.DEFAULT_DETECTION, **tuning["selected"]["params"]}
    C.TUNED_PATH.write_text(json.dumps({"params": tuning["selected"]["params"], "selected_on": "tuning seeds 1-20",
                                        "created_at": datetime.now(timezone.utc).isoformat()}, indent=2))
    holdout = evaluate_holdout(params, verbose)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": None,
        "params": params,
        "tuning": tuning,
        "evaluation_seeds": EVAL_SEEDS,
        "matching_rule": f"An Action incident is a true alert if it is in the event's zone and risk track and escalates between {MATCH_BEFORE} day before exposure onset and {MATCH_AFTER} days after the event ends.",
        "holdout": holdout,
        "holdout_tradeoff": holdout_tradeoff(params, verbose),
        "degraded_definition": {"gps_removed_share": SYNTH.DEGRADED_GPS_LOSS, "clinic_extra_delay_days": SYNTH.DEGRADED_CLINIC_DELAY},
        "baseline_definition": {"window_days": C.BASELINE_WINDOW, "guard_days": C.GUARD_DAYS},
        "scenario_mix": "One heatwave (1-2 zones), one PM2.5 episode and one heat spike with no health response per world; size, timing and place are random.",
        "demo_scenarios": demo_scenarios(params),
        "stress": stress_test(params, verbose),
        "targets": {
            "note": "Design targets from the earlier proposal. They are goals, not results.",
            "sensitivity": ">= 0.85", "ppv": ">= 0.60", "lead_time": ">= 2 days earlier than conventional reporting for gradual events",
        },
    }
    result["runtime_seconds"] = round(time.time() - t0, 1)
    RESULTS_PATH.write_text(json.dumps(result, indent=2, default=float))
    if verbose:
        print(f"wrote {RESULTS_PATH} in {result['runtime_seconds']} s")
    return result


if __name__ == "__main__":
    run_all()
