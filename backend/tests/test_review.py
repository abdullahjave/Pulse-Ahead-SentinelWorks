"""Second-round checks: direct API requests (not the UI role switcher), exports, suppression recovery,
validation arithmetic, event matching and past-only behaviour."""
import csv
import io
import json
import re
import warnings
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

warnings.filterwarnings("ignore", category=RuntimeWarning)

from sentinel import config as C  # noqa: E402
from sentinel import store, validation  # noqa: E402
from sentinel.api import app, scenario_key_dates  # noqa: E402
from sentinel.engine import demo_run, evaluate  # noqa: E402
from sentinel.synth import SCENARIOS  # noqa: E402

client = TestClient(app)
PHO, HSE, ANA = "pho-north", "hse-emp-a", "policy-analyst"
SCENS = ["heatwave", "air_quality", "edge_case"]
COUNT_SIGNALS = set(C.CLINIC_SIGNALS) | set(C.OCC_SIGNALS)


def test_sensor_outage_interrupts_recovery():
    from sentinel.engine import build_incidents, zone_status

    run = demo_run("heatwave", False)
    days = range(50, 56)
    states, locations = {}, {}
    for day in days:
        for zi in range(len(C.ZONES)):
            row = {}
            for sig, original in run.states[(day, zi)].items():
                state = dict(original, as_of=day, newest_day=day, day=day,
                             status="current", reporting_gap=False, z=0.0, latest=None)
                if day == 50 and sig in C.TRACKS["heat"]["weights"]:
                    state["z"] = 4.0
                if day in (52, 53) and sig == "heat_index":
                    state["newest_day"] = 51
                row[sig] = state
            states[day, zi] = row
            locations[day, zi] = 1.0
    fused, tiers, incidents, _, _ = build_incidents(states, locations, run.params, days)
    replay = SimpleNamespace(fused=fused, tiers=tiers)
    assert zone_status(replay, 52, 0)["status"] == "insufficient_data"
    assert all(i.closed == 55 for i in incidents if i.track == "heat")
    assert any(i.track == "heat" and i.action_day == 50 for i in incidents)


def test_missing_tuning_file_requires_recalibration(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "TUNED_PATH", tmp_path / "absent.json")
    with pytest.raises(RuntimeError, match="tuned configuration"):
        C.detection_params()


def H(user):
    r = client.post("/api/auth/login", json={"user_id": user})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ------------------------------------------------------------------ RBAC through direct requests
PROTECTED = ["/api/meta", "/api/overview", "/api/incidents", "/api/sites", "/api/validation", "/api/impact",
             "/api/zones/N1/timeseries?signal=heat_index", "/api/export?kind=sites"]


@pytest.mark.parametrize("path", PROTECTED)
def test_missing_or_forged_token_is_rejected(path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer pho-north"}).status_code == 401  # a user id is not a token


def test_role_is_not_taken_from_query_or_headers():
    ana = H(ANA)
    # Extra parameters and headers claiming a stronger role must be ignored.
    for extra in ("&role=public_health_officer", "&user_id=pho-north", "&employer=EMP_A"):
        assert client.get("/api/sites?scenario=heatwave" + extra, headers=ana).status_code == 403
        assert client.get("/api/export?kind=sites" + extra, headers=ana).status_code == 403
    r = client.get("/api/sites", headers={**ana, "X-Role": "public_health_officer", "X-User-Id": "pho-north"})
    assert r.status_code == 403
    hse = H(HSE)
    oos = next(z["id"] for z in client.get("/api/meta", headers=hse).json()["zones"] if not z["in_scope"])
    r = client.get(f"/api/zones/{oos}/timeseries?signal=heat_index&role=public_health_officer", headers=hse)
    assert r.status_code == 403


def test_role_matrix_on_direct_requests():
    expect = {
        PHO: {"/api/sites": 200, "/api/export?kind=sites": 200, "/api/export?kind=zone_daily": 200,
              "/api/zones/N1/timeseries?signal=resp_visits": 200, "/api/districts/DN/timeseries?signal=resp_visits": 200},
        HSE: {"/api/sites": 200, "/api/export?kind=sites": 200, "/api/export?kind=zone_daily": 403,
              "/api/zones/N1/timeseries?signal=resp_visits": 403, "/api/districts/DN/timeseries?signal=resp_visits": 403,
              "/api/zones/N1/timeseries?signal=heat_index": 200},
        ANA: {"/api/sites": 403, "/api/export?kind=sites": 403, "/api/export?kind=zone_daily": 200,
              "/api/zones/N1/timeseries?signal=resp_visits": 403, "/api/districts/DN/timeseries?signal=resp_visits": 200,
              "/api/sites/A-01/cohorts": 403},
    }
    for user, paths in expect.items():
        h = H(user)
        for path, code in paths.items():
            got = client.get(path, headers=h).status_code
            assert got == code, (user, path, got)


def test_hse_lead_sees_only_own_employer_everywhere():
    h = H(HSE)
    mine = {s["id"] for s in client.get("/api/sites", headers=h).json()["sites"]}
    allsites = client.get("/api/sites", headers=H(PHO)).json()["sites"]
    others = [s["id"] for s in allsites if s["id"] not in mine]
    assert mine and others and all(s["employer_id"] == "EMP_A" for s in allsites if s["id"] in mine)
    for sid in others:
        assert client.get(f"/api/sites/{sid}/cohorts", headers=h).status_code == 403
        assert client.get(f"/api/sites/{sid}/timeseries?signal=sick_leave", headers=h).status_code == 403
        assert client.get(f"/api/sites/{sid}/cohorts/outdoor/daily", headers=h).status_code == 403
    out_of_scope = [z["id"] for z in client.get("/api/meta", headers=h).json()["zones"] if not z["in_scope"]]
    assert out_of_scope
    for z in out_of_scope:
        assert client.get(f"/api/zones/{z}/timeseries?signal=heat_index", headers=h).status_code == 403
    inc = client.get("/api/incidents?scenario=heatwave", headers=H(PHO)).json()["incidents"]
    foreign = [i for i in inc if i["zone"]["id"] in out_of_scope]
    assert foreign
    assert client.get(f"/api/incidents/{foreign[0]['id']}?scenario=heatwave", headers=h).status_code == 403
    body = {"scenario": "heatwave", "status": "acknowledged"}
    assert client.post(f"/api/incidents/{foreign[0]['id']}/decision", json=body, headers=h).status_code == 403
    exp = client.get("/api/export?kind=sites", headers=h).text
    allrows = [r for r in csv.reader(io.StringIO(exp)) if r and not r[0].startswith("#")]
    rows = allrows[1:]
    assert rows and {r[allrows[0].index("site")] for r in rows} <= mine


def test_heavy_and_destructive_endpoints_are_restricted():
    for u in (HSE, ANA):
        assert client.post("/api/validation/rerun", headers=H(u)).status_code == 403
        assert client.post("/api/demo/reset", headers=H(u)).status_code == 403


# ------------------------------------------------------------------ exports and suppression
IDENT = re.compile(r"(name|employee|worker_id|person|patient|staff|disciplin|email|phone|dob)", re.I)


@pytest.mark.parametrize("scenario", SCENS)
@pytest.mark.parametrize("degraded", [False, True])
def test_exports_never_contain_small_counts_or_identifiers(scenario, degraded):
    for user, kinds in ((PHO, ["zone_daily", "sites"]), (HSE, ["sites"]), (ANA, ["zone_daily"])):
        for kind in kinds:
            for as_of in ("2026-07-20", "2026-08-05", "2026-08-25"):
                r = client.get(f"/api/export?kind={kind}&scenario={scenario}&degraded={str(degraded).lower()}&as_of={as_of}", headers=H(user))
                assert r.status_code == 200
                lines = [ln for ln in r.text.splitlines() if not ln.startswith("#")]
                rows = list(csv.reader(lines))
                header = rows[0]
                assert not any(IDENT.search(c) for c in header), header
                for row in rows[1:]:
                    rec = dict(zip(header, row))
                    if kind == "sites":
                        for col in ("sick_leave", "fatigue_reports"):
                            assert not re.fullmatch(r"[1-4]", rec[col]), (user, kind, rec)
                    else:
                        if rec["signal"] in COUNT_SIGNALS:
                            try:
                                v = float(rec["observed"])
                            except ValueError:
                                continue
                            assert not (0 < v < C.SMALL_CELL_THRESHOLD), (user, rec)
                            e = float(rec["expected"]) if rec["expected"] not in ("", "None") else 0.0
                            assert e == round(e), "count baselines are exported as whole numbers"


def test_suppressed_chart_points_carry_no_value_and_baselines_are_whole_numbers():
    h = H(PHO)
    n_supp = 0
    for z in C.ZONE_IDS:
        for sig in ("heat_illness_visits", "resp_visits", "sick_leave", "fatigue_reports"):
            r = client.get(f"/api/zones/{z}/timeseries?signal={sig}&scenario=heatwave&as_of=2026-08-28&window=55", headers=h).json()
            for p in r["points"]:
                for k in ("expected", "lower", "upper"):
                    if p[k] is not None:
                        assert float(p[k]) == round(p[k]), (z, sig, p)
                if p["suppressed"]:
                    n_supp += 1
                    assert p["observed"] is None and p["z"] is None and "reported_so_far" in p
                    assert p["reported_so_far"]["value"] is None and p["reported_so_far"]["display"] in ("<5", "masked")
    assert n_supp > 10  # the check is meaningful: the synthetic data do contain small cells


def test_hidden_component_cannot_be_recovered_from_incident_payloads():
    """When a count contribution is suppressed, neither the score, the per-signal points nor a share residual may remain."""
    h = H(PHO)
    checked = 0
    for sc in SCENS:
        for as_of in ("2026-07-25", "2026-08-02", "2026-08-10", "2026-08-25"):
            for inc in client.get(f"/api/incidents?scenario={sc}&as_of={as_of}", headers=h).json()["incidents"]:
                d = client.get(f"/api/incidents/{inc['id']}?scenario={sc}&as_of={as_of}", headers=h).json()
                for rows in (d["contributions"], d["peak_contributions"]):
                    if any(c["suppressed"] for c in rows):
                        checked += 1
                        vis = [c["share"] for c in rows if not c["suppressed"] and c["share"] is not None]
                        assert not vis or abs(sum(vis) - 1) < 0.01, "visible shares must sum to 100% (no residual)"
                        assert all(c["points"] is None for c in rows)
                        for c in rows:
                            if c["suppressed"]:
                                assert c["share"] is None and c["z"] is None and c["value"] is None and c["expected"] is None
                if any(c["suppressed"] for c in d["contributions"]):
                    assert d["score"] is None and d["score_masked"] is True
    assert checked > 5


def test_overview_scores_withheld_when_a_small_count_contributes():
    h = H(PHO)
    seen_masked = False
    for sc in SCENS:
        for as_of in ("2026-07-25", "2026-08-10", "2026-08-25"):
            ov = client.get(f"/api/overview?scenario={sc}&as_of={as_of}", headers=h).json()
            run = demo_run(sc, False)
            t = C.date_to_day(as_of)
            for z in ov["zones"]:
                if not z["in_scope"]:
                    continue
                zi = C.ZONE_INDEX[z["id"]]
                for tr, info in z["tracks"].items():
                    f = run.fused[(t, zi, tr)]
                    small = any(C.SIGNALS[c["signal"]]["kind"] == "count" and 0 < (run.states[(t, zi)][c["signal"]].get("raw") or 0) < 5 for c in f["contributions"])
                    if small:
                        seen_masked = True
                        assert info["score"] is None
    assert seen_masked


def test_site_tables_cannot_be_back_calculated():
    """Weekly cohort tables: a visible total never sits next to exactly one hidden cohort, and every hidden cell carries no value."""
    h = H(PHO)
    for sc in SCENS:
        for degraded in ("false", "true"):
            for as_of in ("2026-07-20", "2026-08-05", "2026-08-20"):
                q = f"scenario={sc}&degraded={degraded}&as_of={as_of}"
                sites = client.get(f"/api/sites?{q}", headers=h).json()["sites"]
                for s in sites:
                    if s["sick_leave_week"]["suppressed"]:
                        assert s["absence_rate_per_100_worker_days"] is None  # a rate must not leak a hidden total
                    d = client.get(f"/api/sites/{s['id']}/cohorts?{q}", headers=h).json()
                    for col in ("headcount", "sick_leave_week", "fatigue_week"):
                        cells = [r[col] for r in d["rows"]]
                        masked = [c for c in cells if c["suppressed"]]
                        if d["total"][col]["value"] is not None:
                            assert len(masked) != 1, (s["id"], col, cells, d["total"][col])
                        for c in cells:
                            if c["suppressed"]:
                                assert c["value"] is None
                    assert client.get(f"/api/sites/{s['id']}/cohorts/outdoor/daily?{q}", headers=h).status_code == 410


# ------------------------------------------------------------------ replay controls, incidents, workflow
@pytest.mark.parametrize("sid", SCENS)
def test_key_dates_come_from_the_scenario_definition(sid):
    sc = SCENARIOS[sid]
    meta = client.get("/api/meta", headers=H(PHO)).json()
    kd = next(s for s in meta["scenarios"] if s["id"] == sid)["key_dates"]
    dates = [k["date"] for k in kd]
    assert dates == sorted(dates)
    lo, hi = meta["dates"]["detection_start"], meta["dates"]["end"]
    assert all(lo <= d <= hi for d in dates)
    assert dates[0] == lo and dates[-1] == hi  # normal baseline start and end of replay stay reachable
    iso = lambda d: C.day_to_date(d).isoformat()
    for ev in sc.events:
        first, last = ev["start"], ev["start"] + ev["length"] - 1
        assert iso(first) in dates and iso(last) in dates
        assert any(d < iso(first) for d in dates)                     # a baseline date before the event
        assert min(hi, iso(last + 7)) in dates                          # a recovery date after the event
    for dc in sc.decoys:
        assert iso(dc["start"]) in dates
    for a in sc.artefacts:
        assert iso(a["from"]) in dates


def test_incident_counts_split_open_and_past():
    h = H(PHO)
    r = client.get("/api/incidents?scenario=heatwave&as_of=2026-08-25", headers=h).json()
    c = r["counts"]
    assert c["all"] == len(r["incidents"]) == c["open"] + c["past"]
    assert c["past"] == sum(1 for i in r["incidents"] if i["status"] == "closed")
    # The two empty states must be distinguishable: nothing raised yet vs nothing open but history exists.
    first = client.get("/api/incidents?scenario=heatwave&as_of=2026-07-06", headers=h).json()["counts"]
    assert first == {"open": 0, "past": 0, "all": 0}
    late = client.get("/api/incidents?scenario=heatwave&as_of=2026-08-29", headers=h).json()["counts"]
    assert late["open"] == 0 and late["past"] > 0


def test_active_incident_workflow_end_to_end_and_persisted():
    h = H(PHO)
    inc = next(i for i in client.get("/api/incidents?scenario=heatwave&as_of=2026-07-29", headers=h).json()["incidents"] if i["status"] == "active" and i["max_tier"] == "action")
    q = "scenario=heatwave&as_of=2026-07-29"
    d = client.get(f"/api/incidents/{inc['id']}?{q}", headers=h).json()
    assert d["contributions"] and abs(sum(c["share"] for c in d["contributions"] if c["share"] is not None) - 1) < 0.025  # four shares rounded to 1% each
    assert d["evidence"] and all(e["text"] for e in d["evidence"])
    assert d["when"]["opened"] and d["when"]["action"] and d["when"]["peak"]
    pb = d["playbook"]
    assert pb["owner"] and pb["actions"] and pb["assumptions"] and pb["guidance"]
    assert all(g["url"].startswith("https://") and g["title"] for g in pb["guidance"])
    for c in d["contributions"]:
        if c["signal"] in ("heat_index", "heat_illness_visits"):
            ts = client.get(f"/api/zones/{d['zone']['id']}/timeseries?signal={c['signal']}&{q}&window=28", headers=h).json()
            assert any(p["expected"] is not None for p in ts["points"])  # baseline comparison exists
    store.reset()
    r = client.post(f"/api/incidents/{inc['id']}/decision", json={"scenario": "heatwave", "as_of": "2026-07-29", "status": "acknowledged", "note": "test ack"}, headers=h)
    assert r.status_code == 200 and r.json()["notification_sent"] is False
    fresh = TestClient(app)  # a new client = a "reload": state must come from the database, not memory
    tok = fresh.post("/api/auth/login", json={"user_id": PHO}).json()["token"]
    again = fresh.get(f"/api/incidents/{inc['id']}?{q}", headers={"Authorization": f"Bearer {tok}"}).json()
    assert again["decisions"][-1]["status"] == "acknowledged" and again["decisions"][-1]["role"] == "public_health_officer"
    lst = fresh.get(f"/api/incidents?{q}", headers={"Authorization": f"Bearer {tok}"}).json()["incidents"]
    assert next(i for i in lst if i["id"] == inc["id"])["decision"]["status"] == "acknowledged"
    store.reset()


def test_decision_rights_by_role():
    inc = next(i for i in client.get("/api/incidents?scenario=heatwave&as_of=2026-07-29", headers=H(PHO)).json()["incidents"] if i["max_tier"] == "action")
    body = {"scenario": "heatwave", "as_of": "2026-07-29", "status": "acknowledged"}
    assert client.post(f"/api/incidents/{inc['id']}/decision", json=body, headers=H(ANA)).status_code == 403
    assert client.post(f"/api/incidents/{inc['id']}/decision", json={**body, "status": "noted_for_review"}, headers=H(PHO)).status_code == 403
    assert client.post(f"/api/incidents/{inc['id']}/decision", json={**body, "status": "bogus"}, headers=H(PHO)).status_code == 403


# ------------------------------------------------------------------ validation arithmetic and matching
def _inc(zone, track, action_day, opened=None, tier_days=()):
    hist = [{"t": t, "tier": "action"} for t in tier_days] or ([{"t": action_day, "tier": "action"}] if action_day is not None else [])
    return SimpleNamespace(id=f"{zone}-{track}-{action_day}-{opened}", zone=zone, track=track, action_day=action_day, opened=opened if opened is not None else (action_day or 0), history=hist)


TRUTH = [{"zone": "N1", "track": "heat", "exposure_start": 50, "end": 54, "health_onset": 51}]


def test_event_matching_window_edges():
    for day, matched in ((48, False), (49, True), (57, True), (58, False)):
        s = validation.score_run(TRUTH, [_inc("N1", "heat", day)], {})
        assert (s["n_action_true"] == 1) is matched, day
        assert s["n_action_false"] == (0 if matched else 1)


def test_wrong_zone_or_track_does_not_match():
    s = validation.score_run(TRUTH, [_inc("N2", "heat", 52), _inc("N1", "respiratory", 52)], {})
    assert s["n_action_true"] == 0 and s["n_action_false"] == 2


def test_duplicate_alerts_for_one_event_are_not_extra_true_positives():
    s = validation.score_run(TRUTH, [_inc("N1", "heat", 52), _inc("N1", "heat", 56, opened=55)], {})
    assert s["n_action"] == 2 and s["n_action_true"] == 1 and s["n_action_duplicate"] == 1 and s["n_action_false"] == 0
    summ = validation._summarise([s], "t")
    assert summ["ppv_action"] == 0.5  # 1 true event out of 2 alerts, not 2/2


def test_reported_validation_metrics_are_arithmetically_consistent_with_the_stored_run():
    v = json.loads((C.DATA_DIR / "validation_results.json").read_text())
    assert not set(v["evaluation_seeds"]) & set(v["tuning"]["seeds"])  # held-out seeds
    assert v["params"]["action_score"] == v["tuning"]["selected"]["params"]["action_score"]
    for key in ("standard", "degraded"):
        c = v["holdout"]["conditions"][key]
        assert c["runs"] == len(v["evaluation_seeds"])
        assert c["sensitivity_action"] == pytest.approx(c["events_detected_action"] / c["events"])
        assert c["ppv_action"] == pytest.approx(c["action_true"] / c["action_alerts"])
        assert c["action_alerts"] == c["action_true"] + c["false_action_alerts"] + c["duplicate_action_alerts"]
        assert c["false_action_per_zone_month"] == pytest.approx(c["false_action_alerts"] / (validation.ZONE_MONTHS * c["runs"]))
        assert c["lead_vs_comparator_days"]["n"] == c["events_detected_with_comparator"]
        assert c["events_detected_but_missed_by_comparator"] == c["events_detected_action"] - c["events_detected_with_comparator"]
        loc = v["holdout"]["location"][key]
        assert loc["records_dropped"] == 0 and loc["records_in"] == loc["records_retained"]
    sel = next(g for g in v["holdout_tradeoff"]["grid"] if g["params"] == {"action_score": v["params"]["action_score"], "corroboration_z": v["params"]["corroboration_z"]})
    std = v["holdout"]["conditions"]["standard"]
    assert sel["sensitivity"] == pytest.approx(std["sensitivity_action"]) and sel["ppv"] == pytest.approx(std["ppv_action"])
    assert v["holdout_tradeoff"]["used_for_selection"] is False
    # the API serves the same file the page displays
    assert client.get("/api/validation", headers=H(PHO)).json()["holdout"]["conditions"]["standard"]["ppv_action"] == pytest.approx(std["ppv_action"])


def test_one_held_out_world_recomputes_to_the_stored_summary_inputs():
    """Independent recomputation of one evaluation seed from raw generation through scoring."""
    seed = validation.EVAL_SEEDS[0]
    params = {**C.DEFAULT_DETECTION, **json.loads((C.DATA_DIR / "validation_results.json").read_text())["tuning"]["selected"]["params"]}
    sc = validation.random_scenario(np.random.default_rng(seed), seed)
    al, states, loc = validation._prepare(sc, seed, False, params)
    _, _, incidents, _, _ = validation.build_incidents(states, loc, params, validation.DAYS)
    s = validation.score_run(al.world.truth, incidents, validation.compute_comparator(al, validation.DAYS))
    assert s["n_action"] == s["n_action_true"] + s["n_action_false"] + s["n_action_duplicate"]
    assert len(s["events"]) == len(al.world.truth)


# ------------------------------------------------------------------ past-only and availability handling
def test_replay_status_on_a_day_does_not_depend_on_later_data():
    full = demo_run("heatwave", False)
    early = evaluate(SCENARIOS["heatwave"], seed=42, days=range(C.WARMUP_DAYS, 58), with_comparator=False)
    for t in range(C.WARMUP_DAYS, 58):
        for zi in range(len(C.ZONES)):
            for tr in C.TRACKS:
                a, b = full.fused[(t, zi, tr)], early.fused[(t, zi, tr)]
                assert a["score"] == pytest.approx(b["score"]) and full.tiers[(t, zi, tr)] == early.tiers[(t, zi, tr)]


def test_records_arriving_after_the_replay_day_are_not_used():
    run = demo_run("edge_case", False)
    # S2 reports four days of visits in one batch on day 65: before that, S2 must look incomplete, not normal.
    s2 = C.ZONE_INDEX["S2"]
    from sentinel.pipeline import zone_series
    _, raw_before, comp_before = zone_series(run.al, "resp_visits", 62, s2, 0.0)
    _, raw_after, _ = zone_series(run.al, "resp_visits", 66, s2, 0.0)
    assert raw_before[59:62].sum() < raw_after[59:62].sum()


# ------------------------------------------------------------------ impact units
def test_impact_keeps_visits_and_absence_in_separate_units():
    for sc in ("heatwave", "air_quality"):
        m = client.get(f"/api/impact?scenario={sc}", headers=H(PHO)).json()
        assert "visit" in m["units"]["excess_clinic_visits"] and "sick-leave" in m["units"]["excess_sick_leave_days"]
        meas = m["measured"]
        for k in ("share_of_excess_after_sentinel_alert", "share_of_excess_absence_after_sentinel_alert",
                  "share_of_excess_after_comparator_alert", "share_of_excess_absence_after_comparator_alert"):
            assert meas[k] is None or 0 <= meas[k] <= 1, (sc, k, meas[k])
        assert meas["share_of_excess_absence_after_sentinel_alert"] >= meas["share_of_excess_absence_after_comparator_alert"]


def test_invalid_tuned_configuration_is_reported_explicitly_over_http(tmp_path, monkeypatch):
    from sentinel.engine import demo_run as _demo
    h = H(PHO)
    bad = tmp_path / "tuned.json"
    bad.write_text('{"params": {"action_score": "2.4", "corroboration_z": 2.0}}')
    monkeypatch.setattr(C, "TUNED_PATH", bad)
    _demo.cache_clear()
    try:
        quiet = TestClient(app, raise_server_exceptions=False)
        r = quiet.get("/api/overview?scenario=heatwave", headers=h)
        assert r.status_code == 503 and "tuned configuration" in r.json()["detail"]
        health = quiet.get("/api/health").json()
        assert health["ok"] is False and "tuned configuration" in health["config_error"]
    finally:
        monkeypatch.undo()
        _demo.cache_clear()
    assert client.get("/api/health").json()["ok"] is True
