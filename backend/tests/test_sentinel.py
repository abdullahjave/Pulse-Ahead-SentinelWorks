import csv
import io
import warnings

import numpy as np
import pytest
from fastapi.testclient import TestClient

warnings.filterwarnings("ignore", category=RuntimeWarning)

from sentinel import config as C  # noqa: E402
from sentinel import store  # noqa: E402
from sentinel.api import app  # noqa: E402
from sentinel.detection import baseline_at  # noqa: E402
from sentinel.engine import demo_run, zone_status  # noqa: E402
from sentinel.pipeline import align  # noqa: E402
from sentinel.privacy import assert_no_small_counts, suppress_row  # noqa: E402
from sentinel.synth import SCENARIOS, generate  # noqa: E402

client = TestClient(app)


def login(user):
    r = client.post("/api/auth/login", json={"user_id": user})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


PHO, HSE, ANA = "pho-north", "hse-emp-a", "policy-analyst"


# ---------------------------------------------------------------- data & pipeline
def test_generator_is_reproducible():
    a = generate(SCENARIOS["heatwave"], 42)
    b = generate(SCENARIOS["heatwave"], 42)
    for k in a.clinic:
        assert np.array_equal(a.clinic[k], b.clinic[k], equal_nan=True)
    assert np.array_equal(a.env["heat_index"], b.env["heat_index"], equal_nan=True)


def test_ninety_days_and_two_families_minimum():
    w = generate(SCENARIOS["heatwave"], 42)
    assert w.env["heat_index"].shape[1] == 90
    fams = {C.SIGNALS[s]["family"] for s in C.SIGNALS}
    assert len(fams) >= 2


@pytest.mark.parametrize("degraded", [False, True])
def test_no_record_is_dropped_by_location_fallback(degraded):
    al = align(generate(SCENARIOS["heatwave"], 42, degraded))
    n = len(al.world.clinic["obs"])
    assert len(al.rec_level) == n
    assert set(np.unique(al.rec_level)) <= {0, 1, 2, 3, 4}
    # region totals (all records available by the last day) equal all records with avail < 90
    total_region = sum(al.region_raw[s][-1].sum() for s in C.CLINIC_SIGNALS)
    assert total_region == (al.world.clinic["avail"] < C.N_DAYS).sum()


def test_degraded_mode_reduces_zone_resolution_but_keeps_records():
    a = align(generate(SCENARIOS["heatwave"], 42, False))
    b = align(generate(SCENARIOS["heatwave"], 42, True))
    assert len(a.rec_level) == len(b.rec_level)
    assert (b.rec_level == 0).mean() < (a.rec_level == 0).mean() - 0.15  # fewer GPS-placed records
    assert (b.rec_zone >= 0).mean() <= (a.rec_zone >= 0).mean()  # zone placement never improves when inputs degrade
    assert (b.rec_level == 4).mean() > (a.rec_level == 4).mean()  # the postal fallback absorbs part of the loss


def test_availability_time_is_respected():
    al = align(generate(SCENARIOS["heatwave"], 42))
    cl = al.world.clinic
    t, zi = 60, 0
    m = (cl["signal"] == 0) & (al.rec_zone == zi) & (cl["avail"] <= t) & (cl["obs"] == 59)
    assert al.zone_raw["heat_illness_visits"][t, zi, 59] == m.sum()


def test_baseline_is_past_only():
    rng = np.random.default_rng(0)
    v = rng.poisson(10, 90).astype(float)
    dow = np.arange(90) % 7
    e1 = baseline_at(v, 60, dow, "heat_illness_visits")
    v2 = v.copy()
    v2[59:] = 999  # change the guard band and the future
    e2 = baseline_at(v2, 60, dow, "heat_illness_visits")
    assert e1 == e2


# ---------------------------------------------------------------- detection & alerting
def test_heatwave_raises_action_before_comparator():
    run = demo_run("heatwave", False)
    acts = [i for i in run.incidents if i.action_day is not None]
    assert {i.zone for i in acts} == {"N1", "N2"}
    for i in acts:
        cmp_first = min(run.comparator[(C.ZONE_INDEX[i.zone], "heat")])
        assert i.action_day < cmp_first


def test_contributions_sum_to_one():
    run = demo_run("heatwave", False)
    inc = next(i for i in run.incidents if i.action_day)
    row = next(h for h in inc.history if h["tier"] == "action")
    shares = [c["share"] for c in row["contributions"] if c["share"] is not None]
    assert abs(sum(shares) - 1) < 1e-6
    assert len(row["families"]) >= 2


def test_one_incident_per_episode_deduplicates_daily_evaluations():
    run = demo_run("heatwave", False)
    n1 = [i for i in run.incidents if i.zone == "N1" and i.track == "heat" and 50 <= i.opened <= 60]
    assert len(n1) == 1
    assert run.evaluations_at_or_above_watch > len([e for e in run.alert_events if e["type"] == "opened"])


def test_decoy_exposure_without_health_response_does_not_reach_action():
    run = demo_run("edge_case", False)
    assert not [i for i in run.incidents if i.action_day is not None]


def test_missing_data_is_not_reported_as_normal():
    run = demo_run("edge_case", False)
    s3 = C.ZONE_INDEX["S3"]
    st = run.states[(62, s3)]["pm25"]
    assert st["status"] in ("delayed", "missing")
    s2 = C.ZONE_INDEX["S2"]
    statuses = {zone_status(run, t, s2)["status"] for t in range(59, 64)}
    assert "insufficient_data" in statuses


# ---------------------------------------------------------------- privacy & RBAC (API level)
def test_unauthenticated_requests_are_rejected():
    assert client.get("/api/overview").status_code == 401


def test_hse_lead_scope_is_enforced_server_side():
    h = login(HSE)
    assert client.get("/api/zones/S2/timeseries?signal=heat_index", headers=h).status_code == 403
    assert client.get("/api/zones/N1/timeseries?signal=heat_illness_visits", headers=h).status_code == 403
    assert client.get("/api/sites/B-01/cohorts", headers=h).status_code == 403
    ok = client.get("/api/sites", headers=h).json()["sites"]
    assert {s["employer_id"] for s in ok} == {"EMP_A"}
    ov = client.get("/api/overview?scenario=heatwave", headers=h).json()
    out = [z for z in ov["zones"] if not z["in_scope"]]
    assert out and all("status" not in z for z in out)


def test_policy_analyst_has_no_site_access():
    h = login(ANA)
    assert client.get("/api/sites", headers=h).status_code == 403
    assert client.get("/api/export?kind=sites", headers=h).status_code == 403
    assert client.get("/api/districts/DN/timeseries?signal=heat_illness_visits", headers=h).status_code == 200


def test_contribution_counts_hidden_for_non_health_roles():
    h = login(HSE)
    inc = client.get("/api/incidents?scenario=heatwave&as_of=2026-07-28", headers=h).json()["incidents"]
    act = next(i for i in inc if i["max_tier"] == "action")
    d = client.get(f"/api/incidents/{act['id']}?scenario=heatwave&as_of=2026-07-28", headers=h).json()
    clin = next(c for c in d["contributions"] if c["family"] == "clinical")
    assert clin["value"] is None and clin["expected"] is None
    assert d["playbook"]["owner"].startswith("Site HSE")


def test_no_small_counts_leak_in_any_response():
    for user in (PHO, HSE, ANA):
        h = login(user)
        for scen in SCENARIOS:
            for deg in ("false", "true"):
                q = f"scenario={scen}&degraded={deg}"
                assert_no_small_counts(client.get(f"/api/overview?{q}", headers=h).json())
                incs = client.get(f"/api/incidents?{q}", headers=h).json()["incidents"]
                for i in incs[:5]:
                    assert_no_small_counts(client.get(f"/api/incidents/{i['id']}?{q}", headers=h).json())
                r = client.get(f"/api/sites?{q}", headers=h)
                if r.status_code == 200:
                    assert_no_small_counts(r.json())
                    for s in r.json()["sites"]:
                        c = client.get(f"/api/sites/{s['id']}/cohorts?{q}", headers=h).json()
                        assert_no_small_counts(c)
                        ts = client.get(f"/api/sites/{s['id']}/timeseries?{q}", headers=h).json()
                        assert_no_small_counts(ts)
                        for p in ts["points"]:
                            if p["suppressed"]:
                                assert p["observed"] is None and p["z"] is None


def test_suppressed_cohort_blocks_drilldown():
    h = login(HSE)
    c = client.get("/api/sites/A-03/cohorts?scenario=heatwave", headers=h).json()
    assert any(r["sick_leave_week"]["suppressed"] or r["headcount"]["suppressed"] for r in c["rows"]), "small site A-03 should contain masked cells"
    # Daily workplace counts are never released (policy R1): the old drill-down is gone for every cohort.
    for r in c["rows"]:
        assert client.get(f"/api/sites/A-03/cohorts/{r['cohort']}/daily?scenario=heatwave", headers=h).status_code == 410


def test_complementary_suppression():
    out = suppress_row({"a": 3, "b": 20, "c": 7, "total": 30}, total_key="total")
    assert out["a"]["suppressed"] and out["c"]["suppressed"] and not out["b"]["suppressed"]


def test_csv_export_is_scoped_and_suppressed():
    for user, kind in ((PHO, "zone_daily"), (HSE, "sites"), (PHO, "sites")):
        h = login(user)
        r = client.get(f"/api/export?kind={kind}&scenario=heatwave", headers=h)
        assert r.status_code == 200
        rows = list(csv.reader(io.StringIO("\n".join(l for l in r.text.splitlines() if not l.startswith("#")))))
        header, body = rows[0], rows[1:]
        count_cols = [i for i, n in enumerate(header) if n in ("sick_leave", "fatigue_reports")]
        if kind == "zone_daily":
            obs = header.index("observed")
            sig = header.index("signal")
            for row in body:
                if C.SIGNALS[row[sig]]["kind"] == "count" and row[obs] not in ("", "<5", "masked"):
                    assert float(row[obs]) >= 0
        for row in body:
            for i in count_cols:
                assert row[i] in ("<5", "masked") or int(row[i]) == 0 or int(row[i]) >= 5
        if user == HSE:
            assert {row[header.index("employer")] for row in body} == {"EMP_A"}


def test_decision_is_recorded_and_role_restricted():
    store.reset()
    h = login(PHO)
    inc = next(i for i in client.get("/api/incidents?scenario=heatwave&as_of=2026-07-28", headers=h).json()["incidents"] if i["max_tier"] == "action")
    body = {"scenario": "heatwave", "as_of": "2026-07-28", "status": "acknowledged", "note": "Reviewed with site leads"}
    r = client.post(f"/api/incidents/{inc['id']}/decision", json=body, headers=h)
    assert r.status_code == 200 and r.json()["notification_sent"] is False
    d = client.get(f"/api/incidents/{inc['id']}?scenario=heatwave&as_of=2026-07-28", headers=h).json()
    assert d["decisions"][-1]["status"] == "acknowledged"
    a = login(ANA)
    assert client.post(f"/api/incidents/{inc['id']}/decision", json=body, headers=a).status_code == 403
    store.reset()
