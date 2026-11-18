"""Weekly release policy for workplace counts (sentinel/release.py): no daily workplace numbers, weekly cells stable across
replay dates, and an attacker who combines EVERY released total cannot pin a hidden small cell.

History: an earlier version published daily and 7-day workplace numbers side by side and an API-only attacker recovered
hidden 1-4 counts with 100% accuracy by subtraction. Those attacks are replayed below against the new policy."""
import csv
import io
import warnings
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

warnings.filterwarnings("ignore", category=RuntimeWarning)

from sentinel import config as C  # noqa: E402
from sentinel import release  # noqa: E402
from sentinel.api import app  # noqa: E402
from sentinel.engine import demo_run  # noqa: E402

client = TestClient(app)
USERS = {"pho": "pho-north", "hse": "hse-emp-a", "pol": "policy-analyst"}
HDR = {k: {"Authorization": "Bearer " + client.post("/api/auth/login", json={"user_id": u}).json()["token"]} for k, u in USERS.items()}
BIG = 10_000


def get(role, path, **kw):
    return client.get(path, headers=HDR[role], params=kw)


def day(p):
    return C.date_to_day(p["date"])


# ------------------------------------------------------------------ R1: no daily workplace numbers anywhere
@pytest.mark.parametrize("role,zone_ok", [("pho", True), ("hse", False), ("pol", False)])
def test_every_workplace_series_is_weekly(role, zone_ok):
    for sig in C.OCC_SIGNALS:
        reqs = []
        if zone_ok:
            reqs.append(f"/api/zones/N1/timeseries")
        if role in ("pho", "pol"):
            reqs.append("/api/districts/DN/timeseries")
        if role in ("pho", "hse"):
            reqs.append("/api/sites/A-01/timeseries")
        for path in reqs:
            r = get(role, path, signal=sig, window=90, scenario="heatwave", as_of="2026-08-20")
            assert r.status_code == 200, (role, path)
            j = r.json()
            assert j["grain"] == "week" and j["points"], (role, path)
            for p in j["points"]:
                assert C.START_DATE.__class__.fromisoformat(p["date"]).weekday() == 0  # Monday
                assert p["grain"] == "week" and p["z"] is None
                assert 0 <= release.week_days(day(p) // 7)[1] - day(p) + 6 <= 12


def test_partial_and_unreported_weeks_are_never_released():
    for as_of in ("2026-07-06", "2026-07-12", "2026-07-20", "2026-08-29"):
        t = C.date_to_day(as_of)
        weeks = release.complete_weeks(t)
        assert all(7 * k + 6 <= t - release.OCC_COMPLETE_LAG for k in weeks)
        pts = get("pho", "/api/zones/N1/timeseries", signal="sick_leave", window=90, scenario="heatwave", as_of=as_of).json()["points"]
        assert [day(p) // 7 for p in pts] == weeks


def test_cohort_daily_drilldown_is_gone_for_every_role():
    for role in ("pho", "hse"):
        assert get(role, "/api/sites/A-01/cohorts/outdoor/daily", scenario="heatwave").status_code == 410


def test_incident_payloads_carry_no_daily_workplace_numbers():
    for role in ("pho", "hse", "pol"):
        incs = get(role, "/api/incidents", scenario="heatwave", as_of="2026-07-30").json()["incidents"]
        assert incs
        for inc in incs:
            d = get(role, f"/api/incidents/{inc['id']}", scenario="heatwave", as_of="2026-07-30").json()
            for c in d["contributions"] + d["peak_contributions"] + inc["top_signals"]:
                if c["family"] == "occupational":
                    assert c["z"] is None and c["value"] is None and c["expected"] is None and c["pct_deviation"] is None
            for line in d["evidence"]:
                if line["signal"] in C.OCC_SIGNALS:
                    assert "z =" not in line["text"] and "×" not in line["text"]


# ------------------------------------------------------------------ R2: repeated releases of the same cell
def test_weekly_cells_are_identical_on_every_replay_date():
    seen = {}
    for as_of in ("2026-07-12", "2026-07-26", "2026-08-09", "2026-08-29"):
        for role, path, key in (("pho", "/api/zones/S3/timeseries", "zone"), ("pho", "/api/sites/B-03/timeseries", "site"),
                                ("pol", "/api/districts/DS/timeseries", "dist")):
            for sig in C.OCC_SIGNALS:
                for p in get(role, path, signal=sig, window=90, scenario="heatwave", as_of=as_of).json()["points"]:
                    k = (role, key, sig, p["date"])
                    cell = (p["reported_so_far"]["display"], p["expected"], p["lower"], p["upper"])
                    assert seen.setdefault(k, cell) == cell, f"{k} was revised between replay dates: {seen[k]} vs {cell}"
    assert len(seen) > 40


def test_degraded_inputs_do_not_change_workplace_cells():
    """The degraded toggle affects clinic location and delay only; a workplace cell must not differ between the two worlds."""
    a = get("pho", "/api/sites/A-01/timeseries", signal="sick_leave", window=90, scenario="heatwave", as_of="2026-08-29", degraded="false").json()["points"]
    b = get("pho", "/api/sites/A-01/timeseries", signal="sick_leave", window=90, scenario="heatwave", as_of="2026-08-29", degraded="true").json()["points"]
    assert [(p["date"], p["reported_so_far"]["display"]) for p in a] == [(p["date"], p["reported_so_far"]["display"]) for p in b]


# ------------------------------------------------------------------ R6: derived numbers need released inputs
def test_expected_values_and_rates_never_use_hidden_weeks():
    for sig in C.OCC_SIGNALS:
        for ident, path in (("A-03", "/api/sites/A-03/timeseries"), ("B-03", "/api/sites/B-03/timeseries")):
            pts = get("pho", path, signal=sig, window=90, scenario="heatwave", as_of="2026-08-29").json()["points"]
            for i, p in enumerate(pts):
                if p["expected"] is not None:
                    prior = pts[i - 4: i]
                    assert len(prior) == 4 and not any(q["suppressed"] for q in prior)
    for sc in ("heatwave", "edge_case"):
        for s in get("pho", "/api/sites", scenario=sc, as_of="2026-08-29").json()["sites"]:
            if s["sick_leave_week"]["suppressed"]:
                assert s["absence_rate_per_100_worker_days"] is None
            else:
                v = s["sick_leave_week"]["value"]
                assert s["absence_rate_per_100_worker_days"] == pytest.approx(v / (s["workforce"] * 7) * 100, abs=0.006)


def test_headcount_denominator_cannot_reveal_a_small_cohort():
    c = get("hse", "/api/sites/A-03/cohorts", scenario="heatwave", as_of="2026-08-29").json()
    cells = [r["headcount"] for r in c["rows"]]
    small = [r for r in c["rows"] if r["headcount"]["suppressed"]]
    assert small, "A-03 has an indoor cohort of 4"
    if c["total"]["headcount"]["value"] is not None:
        assert len([x for x in cells if x["suppressed"]]) != 1


# ------------------------------------------------------------------ exports match the API and obey the same policy
def test_exports_are_weekly_for_workplace_counts_and_match_the_api():
    r = client.get("/api/export", headers=HDR["pho"], params={"kind": "sites", "scenario": "heatwave", "as_of": "2026-08-20"})
    rows = [x for x in csv.reader(io.StringIO(r.text)) if x and not x[0].startswith("#")]
    h, body = rows[0], rows[1:]
    assert h[:2] == ["week_start", "week_end"] and body
    api = {(p["date"]): p["reported_so_far"]["display"] for p in get("pho", "/api/sites/A-01/timeseries", signal="sick_leave", window=90, scenario="heatwave", as_of="2026-08-20").json()["points"]}
    exp = {x[0]: x[h.index("sick_leave")] for x in body if x[h.index("site")] == "A-01"}
    assert exp == api
    z = client.get("/api/export", headers=HDR["pho"], params={"kind": "zone_daily", "scenario": "heatwave", "as_of": "2026-08-20"})
    zr = [x for x in csv.reader(io.StringIO(z.text)) if x and not x[0].startswith("#")]
    zh = zr[0]
    for x in zr[1:]:
        if x[zh.index("signal")] in C.OCC_SIGNALS:
            assert x[zh.index("grain")] == "week"


# ------------------------------------------------------------------ the attack: combine EVERY released total
def _bounds(cell):
    if not cell["suppressed"]:
        return cell["value"], cell["value"]
    return (1, 4) if cell["reason"] == "small_cell" else (5, BIG)


def _propagate(nodes, parents):
    changed = True
    while changed:
        changed = False
        for p, ch in parents.items():
            slo, shi = sum(nodes[c][0] for c in ch), sum(nodes[c][1] for c in ch)
            lo, hi = max(nodes[p][0], slo), min(nodes[p][1], shi)
            if [lo, hi] != nodes[p]:
                nodes[p] = [lo, hi]
                changed = True
            for c in ch:
                nlo = max(nodes[c][0], nodes[p][0] - (shi - nodes[c][1]))
                nhi = min(nodes[c][1], nodes[p][1] - (slo - nodes[c][0]))
                if [nlo, nhi] != nodes[c]:
                    nodes[c] = [nlo, nhi]
                    changed = True
    return nodes


def interval_attack(scen, degraded, role, sig):
    """Collect every weekly number the role can obtain, then derive the exact feasible interval of each hidden small cell
    (the sum constraints form a tree, so bounds propagation is exact). Returns (small cells, exact, narrowed, wrong, sum of widths)."""
    run = demo_run(scen, degraded)
    occ = run.al.world.occ[sig].sum(-1)
    wk = occ[:, :, :84].reshape(len(C.SITES), 3, 12, 7).sum(-1)
    q = dict(scenario=scen, degraded=str(degraded).lower(), as_of="2026-08-29")
    nodes, parents, truth = {}, defaultdict(list), {}
    if role in ("pho", "hse"):
        for s in get(role, "/api/sites", **q).json()["sites"]:
            sid, si = s["id"], C.SITE_INDEX[s["id"]]
            for p in get(role, f"/api/sites/{sid}/timeseries", signal=sig, window=90, **q).json()["points"]:
                k = day(p) // 7
                nodes[("site", sid, k)] = list(_bounds(p["reported_so_far"]))
                truth[("site", sid, k)] = int(wk[si, :, k].sum())
            coh = get(role, f"/api/sites/{sid}/cohorts", **q).json()
            for row in coh["rows"]:
                ci = [c["id"] for c in C.COHORTS].index(row["cohort"])
                cells = row["sick_leave_by_week"] if sig == "sick_leave" else [dict(row["fatigue_week"], start=coh["week"]["start"])]
                for c in cells:
                    k = C.date_to_day(c["start"]) // 7
                    nodes[("coh", sid, ci, k)] = list(_bounds(c))
                    truth[("coh", sid, ci, k)] = int(wk[si, ci, k])
                    parents[("site", sid, k)].append(("coh", sid, ci, k))
    if role == "pho":
        for z in C.ZONES:
            for p in get(role, f"/api/zones/{z['id']}/timeseries", signal=sig, window=90, **q).json()["points"]:
                k = day(p) // 7
                nodes[("zone", z["id"], k)] = list(_bounds(p["reported_so_far"]))
                for s in C.SITES:
                    if s["zone"] == z["id"] and ("site", s["id"], k) in nodes:
                        parents[("zone", z["id"], k)].append(("site", s["id"], k))
    if role in ("pho", "pol"):
        for d in C.DISTRICTS:
            for p in get(role, f"/api/districts/{d['id']}/timeseries", signal=sig, window=90, **q).json()["points"]:
                k = day(p) // 7
                nodes[("dist", d["id"], k)] = list(_bounds(p["reported_so_far"]))
                if role == "pho":
                    for z in C.ZONES:
                        if z["district"] == d["id"] and ("zone", z["id"], k) in nodes:
                            parents[("dist", d["id"], k)].append(("zone", z["id"], k))
    full = {}
    for p, ch in parents.items():
        want = {"site": 3, "zone": sum(1 for s in C.SITES if s["zone"] == p[1]), "dist": 3}[p[0]]
        if p in nodes and len(ch) == want and all(c in nodes for c in ch):
            full[p] = ch
    before = {k: list(v) for k, v in nodes.items()}
    nodes = _propagate(nodes, full)
    small = exact = narrowed = wrong = width = 0
    for key, (lo, hi) in nodes.items():
        if before[key] == [1, 4]:
            small += 1
            width += hi - lo + 1
            exact += lo == hi
            narrowed += (hi - lo + 1) < 4
            t_ = truth.get(key)
            wrong += int(t_ is not None and not (lo <= t_ <= hi))
    return small, exact, narrowed, wrong, width


@pytest.mark.parametrize("scen,degraded", [("heatwave", False), ("heatwave", True), ("air_quality", False), ("edge_case", False)])
def test_no_hidden_small_cell_can_be_pinned_by_combining_all_released_totals(scen, degraded):
    total_small = 0
    for role in ("pho", "hse", "pol"):
        for sig in C.OCC_SIGNALS:
            small, exact, narrowed, wrong, width = interval_attack(scen, degraded, role, sig)
            assert wrong == 0, "the attack's bounds must contain the truth (sanity check of the attack itself)"
            assert exact == 0, f"{role} {scen} degraded={degraded} {sig}: {exact} hidden small cells recovered exactly"
            total_small += small
    assert total_small > 0  # the test is only meaningful if cells really were hidden
