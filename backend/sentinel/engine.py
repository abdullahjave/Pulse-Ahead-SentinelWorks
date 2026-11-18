"""Replay engine: evaluates every zone and risk track on every replay day, then builds
incidents with persistence, cooldown and de-duplication rules.

Each replay day t only uses data available by t, so slicing the result at any day gives the
same answer the system would have given on that day.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from . import config as C
from .detection import comparator_flag, data_confidence, fuse, signal_state
from .pipeline import Aligned, align, location_quality
from .synth import SCENARIOS, Scenario, generate

TIER_RANK = {"normal": 0, "watch": 1, "action": 2}


@dataclass
class Incident:
    id: str
    zone: str
    track: str
    opened: int
    history: list = field(default_factory=list)  # [{t, tier, score, ...}]
    action_day: int | None = None
    closed: int | None = None
    reopened: list = field(default_factory=list)

    def state_at(self, t: int) -> dict | None:
        rows = [h for h in self.history if h["t"] <= t]
        if not rows:
            return None
        active_rows = [h for h in rows if h["tier"] != "normal"]
        peak = max(active_rows, key=lambda h: h["score"]) if active_rows else rows[-1]
        closed = self.closed is not None and self.closed <= t
        max_tier = "action" if (self.action_day is not None and self.action_day <= t) else "watch"
        current = rows[-1]
        return {"latest": current, "peak": peak, "closed": closed, "max_tier": max_tier,
                "status": "closed" if closed else ("active" if current["tier"] != "normal" else "monitoring"),
                "first_active": active_rows[0]["t"] if active_rows else None,
                "last_active": active_rows[-1]["t"] if active_rows else None}


@dataclass
class Run:
    scenario: Scenario
    seed: int
    degraded: bool
    params: dict
    al: Aligned
    states: dict  # (t, zone_idx) -> {signal: state}
    fused: dict  # (t, zone_idx, track) -> fused
    tiers: dict  # (t, zone_idx, track) -> final tier
    incidents: list
    alert_events: list
    comparator: dict  # (zone_idx, track) -> first t flagged by conventional single-signal rule
    evaluations_at_or_above_watch: int = 0

    @property
    def key(self) -> str:
        return f"{self.scenario.id}{'-degraded' if self.degraded else ''}"


def compute_states(al: Aligned, params: dict, days) -> dict:
    states = {}
    for t in days:
        for zi in range(len(C.ZONES)):
            states[(t, zi)] = {sig: signal_state(al, sig, t, zi, params) for sig in C.SIGNAL_IDS}
    return states


def compute_location(al: Aligned, days) -> dict:
    return {(t, zi): location_quality(al, t=t, district=C.ZONE_DISTRICT_IDX[zi])["zone_resolved_share"] for t in days for zi in range(len(C.ZONES))}


def compute_comparator(al: Aligned, days) -> dict:
    comparator = {}
    for zi in range(len(C.ZONES)):
        for track, tr in C.TRACKS.items():
            for t in days:
                if comparator_flag(al, tr["clinical_signal"], t, zi):
                    comparator.setdefault((zi, track), []).append(t)
    return comparator


def build_incidents(states: dict, loc_share: dict, params: dict, days):
    fused, tiers = {}, {}
    for (t, zi), st in states.items():
        for track in C.TRACKS:
            fused[(t, zi, track)] = fuse(track, st, params)
    incidents, events = [], []
    watch_evals = 0
    for zi, z in enumerate(C.ZONES):
        for track in C.TRACKS:
            open_inc: Incident | None = None
            last_closed: Incident | None = None
            normal_run = 0
            prev_candidate = prev_score_watch = False
            for t in days:
                f = fused[(t, zi, track)]
                candidate = f["candidate_action"]
                # Persistence rules: Action needs two consecutive qualifying evaluations unless the
                # composite is extreme; a score-based Watch also needs two consecutive evaluations.
                # An exposure reading above published guidance raises Watch immediately.
                if candidate and (prev_candidate or f["score"] >= params["extreme_score"]):
                    tier = "action"
                elif candidate or f["guidance_watch"] or (f["score_watch"] and prev_score_watch):
                    tier = "watch"
                else:
                    tier = "normal"
                prev_candidate, prev_score_watch = candidate, f["score_watch"]
                tiers[(t, zi, track)] = tier
                share = loc_share[(t, zi)]
                row = {"t": t, "tier": tier, "score": f["score"],
                       "contributions": f["contributions"], "families": f["families"],
                       "weight_available": f["weight_available"], "data_issues": f["data_issues"],
                       "confidence": data_confidence(f["weight_available"], share),
                       "zone_resolved_share": share}
                if tier != "normal":
                    watch_evals += 1
                    normal_run = 0
                    if open_inc is None:
                        if last_closed is not None and t - last_closed.closed <= params["cooldown_days"]:
                            open_inc = last_closed
                            open_inc.closed = None
                            open_inc.reopened.append(t)
                            events.append({"t": t, "incident": open_inc.id, "zone": z["id"], "track": track, "type": "reopened", "tier": tier})
                        else:
                            open_inc = Incident(id=f"{z['id']}-{track}-{C.day_to_date(t).isoformat()}", zone=z["id"], track=track, opened=t)
                            incidents.append(open_inc)
                            events.append({"t": t, "incident": open_inc.id, "zone": z["id"], "track": track, "type": "opened", "tier": tier})
                        last_closed = None
                    if tier == "action" and open_inc.action_day is None:
                        open_inc.action_day = t
                        events.append({"t": t, "incident": open_inc.id, "zone": z["id"], "track": track, "type": "escalated", "tier": "action"})
                    open_inc.history.append(row)
                elif open_inc is not None:
                    if f["insufficient"]:
                        normal_run = 0
                        continue
                    normal_run += 1
                    open_inc.history.append(row)
                    if normal_run >= params["close_after_normal_days"]:
                        open_inc.closed = t
                        events.append({"t": t, "incident": open_inc.id, "zone": z["id"], "track": track, "type": "closed", "tier": "normal"})
                        last_closed, open_inc = open_inc, None
    events.sort(key=lambda e: (e["t"], e["zone"]))
    return fused, tiers, incidents, events, watch_evals


def evaluate(scenario: Scenario, seed: int = 42, degraded: bool = False, params: dict | None = None,
             days: range | None = None, with_comparator: bool = True) -> Run:
    params = params or C.detection_params()
    world = generate(scenario, seed=seed, degraded=degraded)
    al = align(world)
    days = days or range(C.WARMUP_DAYS, C.N_DAYS)
    states = compute_states(al, params, days)
    loc_share = compute_location(al, days)
    fused, tiers, incidents, events, watch_evals = build_incidents(states, loc_share, params, days)
    comparator = compute_comparator(al, days) if with_comparator else {}
    return Run(scenario, seed, degraded, params, al, states, fused, tiers, incidents, events, comparator, watch_evals)


@lru_cache(maxsize=16)
def demo_run(scenario_id: str, degraded: bool) -> Run:
    return evaluate(SCENARIOS[scenario_id], seed=42, degraded=degraded)


def zone_status(run: Run, t: int, zi: int) -> dict:
    """Headline status for a zone on day t: highest tier over tracks, or insufficient data."""
    best_track, best = None, None
    for track in C.TRACKS:
        tier = run.tiers.get((t, zi, track), "normal")
        f = run.fused[(t, zi, track)]
        key = (TIER_RANK[tier], f["score"])
        if best is None or key > best[0]:
            best, best_track = (key, tier, f), track
    (_, tier, f) = best
    insufficient = all(run.fused[(t, zi, tr)]["insufficient"] for tr in C.TRACKS) or \
        (tier == "normal" and any(run.fused[(t, zi, tr)]["insufficient"] for tr in C.TRACKS))
    status = tier if tier != "normal" else ("insufficient_data" if insufficient else "normal")
    return {"status": status, "tier": tier, "track": best_track, "score": f["score"], "fused": f}
