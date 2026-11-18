"""Inference through derived outputs. A withheld count can still leak if a published number is computed from it: the baseline
(expected, band, z) of the following 28 days, and composite scores, shares and tiers.

Measured before the fixes (oracle attacker who knows the method and every other input):
  * chart baselines: 62% of hidden clinic days pinned to one value, mean candidate set 1.99 of 4
  * scores / shares / tiers (rounded): 45% pinned, mean candidate set 2.29 of 4
After release rule R8 (baselines ignore withheld days) both are invariant to the hidden value, which these tests check directly."""
import copy
import warnings

import numpy as np
import pytest

warnings.filterwarnings("ignore", category=RuntimeWarning)

from sentinel import config as C  # noqa: E402
from sentinel.api import _series_points, _small  # noqa: E402
from sentinel.detection import fuse, signal_state  # noqa: E402
from sentinel.engine import demo_run  # noqa: E402
from sentinel.pipeline import zone_series  # noqa: E402

T = C.N_DAYS - 1


@pytest.mark.parametrize("scen", ["heatwave", "edge_case"])
def test_chart_payload_does_not_depend_on_a_hidden_clinic_count(scen):
    run = demo_run(scen, False)
    al = run.al
    checked = 0
    for zi in range(len(C.ZONES)):
        for sig in C.CLINIC_SIGNALS:
            est, raw, comp = zone_series(al, sig, T, zi, 0.0)
            est_used = np.where(comp >= run.params["min_completeness"], est, np.nan)
            base = _series_points(est_used, raw, comp, T, 90, sig, al.dow, "count", True)
            for d in [d for d in range(T + 1) if 0 < round(raw[d]) < C.SMALL_CELL_THRESHOLD][:4]:
                for x in (1, 2, 3, 4):
                    est2, raw2 = est_used.copy(), raw.copy()
                    est2[d], raw2[d] = est_used[d] * x / raw[d], x
                    assert _series_points(est2, raw2, comp, T, 90, sig, al.dow, "count", True) == base, (zi, sig, d, x)
                checked += 1
    assert checked > 10


def test_scores_shares_and_tiers_do_not_depend_on_a_hidden_clinic_count():
    """Counterfactual: set a hidden clinic zone-day to 1..4, recompute the engine for the next 28 replay days and compare the
    published (rounded) score, shares and tier. They must be identical for every x."""
    run = demo_run("heatwave", False)
    al, params = run.al, run.params
    checked = 0
    for zi in (0, 1, 2):
        for track, tr in C.TRACKS.items():
            sig = tr["clinical_signal"]
            raw_final = al.zone_raw[sig][T, zi]
            for d in [d for d in range(C.WARMUP_DAYS - 29, T - 1) if d >= 0 and 0 < round(raw_final[d]) < 5][:3]:
                seqs = {}
                for x in range(1, 5):
                    zr = al.zone_raw[sig].copy()
                    zr[:, zi, d] = zr[:, zi, d] * (x / raw_final[d])
                    al2 = copy.copy(al)
                    al2.zone_raw = {**al.zone_raw, sig: zr}
                    seq = []
                    for D in range(max(d + 2, C.WARMUP_DAYS), min(d + 30, T + 1)):
                        st = dict(run.states[(D, zi)])
                        st[sig] = signal_state(al2, sig, D, zi, params)
                        f = fuse(track, st, params)
                        withheld = any(C.SIGNALS[c["signal"]]["kind"] == "count" and bool(_small(run.states[(D, zi)][c["signal"]].get("raw"))) for c in f["contributions"])
                        seq.append((None if withheld else round(f["score"], 1),
                                    tuple(None if (c["share"] is None or withheld) else round(c["share"], 2) for c in f["contributions"]), f["raw_tier"]))
                    seqs[x] = tuple(seq)
                assert len({seqs[x] for x in seqs}) == 1, f"zone {zi} {sig} day {d}: published outputs differ with the hidden count"
                checked += 1
    assert checked >= 5
