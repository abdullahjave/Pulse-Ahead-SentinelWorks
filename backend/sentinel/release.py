"""Release policy for workplace (HR / safety) counts: weekly grain only, hierarchy-safe.

WHY
  An API-only attack showed that publishing a total together with its daily cells (or a parent total together
  with its children) lets a reader subtract the visible numbers and recover a suppressed 1-4 exactly. Masking
  cells one endpoint at a time cannot fix that, so the policy below is applied everywhere a workplace count
  can leave the server.

POLICY (every endpoint and every export obeys the same rules)
  R1  Grain. Sick-leave and fatigue COUNTS are released only as calendar-week totals (Monday-Sunday). No
      daily workplace count (observed, reported-so-far, value, expected, band, rate) is released for any
      site, cohort, zone or district. Clinic and environmental series are not covered by this module.
  R2  Complete weeks only. A week is released only once all of its days are past the reporting lag, so
      the numbers are final. Masks are computed from final data, hence identical on every replay date:
      repeated requests with different replay dates cannot reveal a cell that was hidden earlier, and no
      cell is ever revised.
  R3  Small cells. A weekly count of 1-4 is withheld (<5). Zero is released.
  R4  Secondary suppression, per role. Within one week, if a parent total is released and some of its
      children (cohorts of a site, sites of a zone, zones of a district) are withheld because they are
      small, at least one LARGE sibling is withheld as well, so the hidden sum is not a small, guessable
      number. If no large sibling exists, the parent is withheld instead. This is applied only between
      levels the same role can request (the officer sees every level, the HSE lead sees cohorts and sites of
      one employer, the policy analyst sees districts only).
  R5  Consistency downwards. If a parent is withheld, its children that could be summed back to it are
      withheld too.
  R6  Derived numbers. A rate or an expected value is released only if every input is itself released
      (the expected value of a week needs the four preceding weeks to be visible).
  R7  Denominators. Workforce and headcount come from the site register (not event counts). A headcount
      below 5 is masked with complementary suppression, as before.

WHAT THIS DOES NOT DO (see README, "Remaining privacy limitations")
  It is secondary suppression, not differential privacy. A reader still learns that a masked cell is below
  5 or that a withheld large sibling exists, learns the released totals exactly, and can use outputs that are
  not counts of the same cell (alert tiers, composite scores, daily z-scores of other signals) to form rough
  beliefs about workplace activity. Those channels were measured; see tests/test_inference.py.
"""
from __future__ import annotations

import numpy as np

from . import config as C

T = C.SMALL_CELL_THRESHOLD
OCC_COMPLETE_LAG = 2  # synth.OCC_LAG_P has three lags (0, 1, 2 days): a day is final two days later
N_WEEKS = C.N_DAYS // 7  # 12 full Monday-Sunday weeks; the last 6 days of the replay form no released week


def complete_weeks(t: int) -> list[int]:
    """Weeks whose last day is at least OCC_COMPLETE_LAG days before replay day t."""
    return [k for k in range(N_WEEKS) if 7 * k + 6 <= t - OCC_COMPLETE_LAG]


def week_days(k: int) -> tuple[int, int]:
    return 7 * k, 7 * k + 6


def _small(v):
    v = np.asarray(v)
    return (v > 0) & (v < T)


def role_flags(permissions: dict, sig: str) -> tuple:
    """(cohorts, sites, zones, districts) levels this role may request for this workplace signal."""
    return (bool(permissions["cohort_drilldown"]), permissions["site_scope"] != "none",
            sig in permissions["zone_signals"], bool(permissions["district_series"]))


class Weekly:
    """Final weekly values and withhold masks for one signal and one role-visibility pattern."""

    def __init__(self, wk_cohort: np.ndarray, flags: tuple):
        cohort_vis, site_vis, zone_vis, dist_vis = flags
        S, _, K = wk_cohort.shape
        Z, Ds = len(C.ZONES), len(C.DISTRICTS)
        self.K = K
        self.coh = wk_cohort.astype(int)
        self.site = self.coh.sum(axis=1)
        self.zone = np.zeros((Z, K), dtype=int)
        for si in range(S):
            self.zone[C.SITE_ZONE_IDX[si]] += self.site[si]
        self.dist = np.zeros((Ds, K), dtype=int)
        for zi in range(Z):
            self.dist[C.ZONE_DISTRICT_IDX[zi]] += self.zone[zi]
        # levels the role cannot request are withheld outright (they are never released anyway)
        self.coh_h = _small(self.coh) | (not cohort_vis)
        self.site_h = _small(self.site) | (not site_vis)
        self.zone_h = _small(self.zone) | (not zone_vis)
        self.dist_h = _small(self.dist) | (not dist_vis)
        zone_sites = {zi: [si for si in range(S) if C.SITE_ZONE_IDX[si] == zi] for zi in range(Z)}
        dist_zones = {d: [zi for zi in range(Z) if C.ZONE_DISTRICT_IDX[zi] == d] for d in range(Ds)}
        for k in range(K):
            # R4 bottom-up: cohorts -> site -> zone -> district. Each hidden node carries a floor (the smallest value
            # it can still have) and a risk flag (a small cell is hidden at or below it).
            site_fl = np.zeros(S, dtype=int)
            site_rk = np.zeros(S, dtype=bool)
            if cohort_vis and site_vis:
                for si in range(S):
                    cv = self.coh[si, :, k]
                    if not self.site_h[si, k]:
                        fl = np.where(self.coh_h[si, :, k], np.where(_small(cv), 1, T), cv)
                        rk = self.coh_h[si, :, k] & _small(cv)
                        self.coh_h[si, :, k], pm = _secure(cv, self.coh_h[si, :, k], fl, rk)
                        self.site_h[si, k] |= pm
                    fl = np.where(self.coh_h[si, :, k], np.where(_small(cv), 1, T), cv)
                    if self.site_h[si, k]:
                        site_fl[si] = 1 if _small(self.site[si, k]) else max(T, int(fl.sum()))
                        site_rk[si] = bool(_small(self.site[si, k]) or (self.coh_h[si, :, k] & _small(cv)).any())
                    else:
                        site_fl[si] = self.site[si, k]
            else:
                for si in range(S):
                    if self.site_h[si, k]:
                        site_fl[si] = 1 if _small(self.site[si, k]) else T
                        site_rk[si] = bool(_small(self.site[si, k]))
                    else:
                        site_fl[si] = self.site[si, k]
            zone_fl = np.zeros(Z, dtype=int)
            zone_rk = np.zeros(Z, dtype=bool)
            for zi in range(Z):
                ss = zone_sites[zi]
                if site_vis and zone_vis and not self.zone_h[zi, k]:
                    fl = np.where(self.site_h[ss, k], site_fl[ss], self.site[ss, k])
                    rk = self.site_h[ss, k] & site_rk[ss]
                    # a site hidden only for being small has floor 1 and is itself risky
                    h, pm = _secure(self.site[ss, k], self.site_h[ss, k], fl, rk)
                    self.site_h[ss, k] = h
                    self.zone_h[zi, k] |= pm
                if self.zone_h[zi, k]:
                    sub = np.where(self.site_h[ss, k], site_fl[ss], self.site[ss, k])
                    zone_fl[zi] = 1 if _small(self.zone[zi, k]) else max(T, int(sub.sum()))
                    zone_rk[zi] = bool(_small(self.zone[zi, k]) or (self.site_h[ss, k] & site_rk[ss]).any())
                else:
                    zone_fl[zi] = self.zone[zi, k]
            if zone_vis and dist_vis:
                for d in range(Ds):
                    zs = dist_zones[d]
                    if not self.dist_h[d, k]:
                        fl = np.where(self.zone_h[zs, k], zone_fl[zs], self.zone[zs, k])
                        rk = self.zone_h[zs, k] & zone_rk[zs]
                        h, pm = _secure(self.zone[zs, k], self.zone_h[zs, k], fl, rk)
                        self.zone_h[zs, k] = h
                        self.dist_h[d, k] |= pm
            # R5 top-down: a withheld parent must not be recoverable by summing its visible children
            if zone_vis and dist_vis:
                for d in range(Ds):
                    if self.dist_h[d, k] and self.dist[d, k] > 0:
                        for zi in dist_zones[d]:
                            self.zone_h[zi, k] |= self.zone[zi, k] > 0
            if site_vis and zone_vis:
                for zi in range(Z):
                    if self.zone_h[zi, k] and self.zone[zi, k] > 0:
                        for si in zone_sites[zi]:
                            self.site_h[si, k] |= self.site[si, k] > 0
            if cohort_vis and site_vis:
                for si in range(S):
                    if self.site_h[si, k] and self.site[si, k] > 0:
                        self.coh_h[si, :, k] |= self.coh[si, :, k] > 0


def _secure(vals: np.ndarray, hidden: np.ndarray, floor: np.ndarray, risk: np.ndarray):
    """Return (hidden', parent_must_be_withheld) for one parent and its children.

    Needs: at least two hidden children, at least one large; and enough headroom that the "withheld cell is >= T"
    floors cannot narrow a small cell: S >= sum(floor of hidden large) + 4*m (+3 when only nested risks exist)."""
    hidden = hidden.copy()
    floor = floor.copy()
    if not hidden.any():
        return hidden, False
    if hidden.sum() < 2 or not (hidden & (vals >= T)).any():
        cand = np.flatnonzero(~hidden & (vals >= T))
        if cand.size == 0:
            return hidden, True
        j = int(cand[int(np.argmax(vals[cand]))])  # largest visible sibling: the hidden sum stays uninformative
        hidden[j] = True
        floor[j] = T
    small = hidden & _small(vals)
    large = hidden & (vals >= T)
    m = int(small.sum())
    nested = bool(risk[large].any())
    if m == 0 and not nested:
        return hidden, False  # nothing sensitive is hidden here (only complementary cells)
    need = int(floor[large].sum()) + 4 * m + (3 if m == 0 else 0)
    if int(vals[hidden].sum()) < need:
        return hidden, True
    return hidden, False


def weekly(run, sig: str, flags: tuple) -> Weekly:
    cache = run.__dict__.setdefault("_release_cache", {})
    key = (sig, flags)
    if key not in cache:
        occ = run.al.world.occ[sig].sum(axis=-1)  # [site, cohort, day]: all reporting lags summed = final
        wk = occ[:, :, : 7 * N_WEEKS].reshape(occ.shape[0], occ.shape[1], N_WEEKS, 7).sum(axis=-1)
        cache[key] = Weekly(wk, flags)
    return cache[key]


def cell(value, hidden) -> dict:
    v = int(value)
    if not hidden:
        return {"value": v, "display": str(v), "suppressed": False}
    if 0 < v < T:
        return {"value": None, "display": f"<{T}", "suppressed": True, "reason": "small_cell"}
    return {"value": None, "display": "masked", "suppressed": True, "reason": "complementary"}


def series_points(vals, hidden, weeks, iso) -> list:
    """Weekly chart points. `expected` needs the four preceding weeks to be released (R6)."""
    pts = []
    for k in weeks:
        exp = lo = hi = None
        prior = [k - j for j in range(1, 5)]
        if k >= 4 and not np.asarray(hidden)[prior].any():
            base = np.asarray(vals)[prior].astype(float)
            m = float(base.mean())
            sd = max(float(base.std(ddof=1)), float(np.sqrt(max(m, 1.0))))
            exp, lo, hi = int(round(m)), max(int(round(m - 2 * sd)), 0), int(round(m + 2 * sd))
        c = cell(vals[k], hidden[k])
        s, e = week_days(k)
        pts.append({"date": iso(s), "week_end": iso(e), "observed": c["value"], "suppressed": c["suppressed"],
                    "derived": c.get("reason") == "complementary", "reported_so_far": c, "expected": exp, "lower": lo,
                    "upper": hi, "z": None, "status": "complete", "completeness": 1.0, "grain": "week"})
    return pts
