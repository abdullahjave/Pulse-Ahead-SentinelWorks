"""Postal-code fallback (spec: "fall back to postal codes, district zones, or workplace site IDs when exact GPS is missing").
Rules under test: a record is placed through its postal code only if the documented crosswalk maps the code to exactly one zone;
boundary codes, unknown codes and missing codes are never guessed; no record is ever dropped."""
import warnings

import numpy as np
import pytest

warnings.filterwarnings("ignore", category=RuntimeWarning)

from sentinel import config as C  # noqa: E402
from sentinel.pipeline import align, location_quality  # noqa: E402
from sentinel.synth import LOC_DISTRICT, LOC_GPS, LOC_NONE, LOC_POSTAL, LOC_SITE, SCENARIOS, generate  # noqa: E402


@pytest.mark.parametrize("degraded", [False, True])
def test_postal_placement_follows_the_crosswalk_exactly(degraded):
    w = generate(SCENARIOS["heatwave"], 42, degraded)
    al = align(w)
    cl = w.clinic
    code_zone = {int(c): C.ZONE_INDEX[v["zone"]] for c, v in C.POSTAL_CROSSWALK.items() if "zone" in v}
    placed = al.rec_level == LOC_POSTAL
    assert placed.sum() > 500
    for i in np.flatnonzero(placed)[:2000]:
        assert code_zone[int(cl["postal"][i])] == al.rec_zone[i]  # zone comes from the table, nothing else
        assert C.ZONE_DISTRICT_IDX[al.rec_zone[i]] == cl["district"][i]  # and agrees with the record's district


def test_boundary_unknown_and_missing_codes_are_never_guessed():
    w = generate(SCENARIOS["heatwave"], 42, True)
    al = align(w)
    cl = w.clinic
    boundary = {int(c) for c, v in C.POSTAL_CROSSWALK.items() if "zone" not in v}
    unknown = {int(c) for c in C.POSTAL_UNMAPPED}
    for codes in (boundary, unknown):
        m = np.isin(cl["postal"], list(codes))
        assert m.sum() > 50
        assert (al.rec_zone[m] == -1).all()  # no zone assigned
        assert (al.rec_level[m] == LOC_DISTRICT).all()  # kept at the reliable district level
    # no location at all stays unlocated
    none = cl["loc_type"] == LOC_NONE
    assert none.sum() > 100 and (al.rec_zone[none] == -1).all() and (al.rec_level[none] == LOC_NONE).all()
    # only records without GPS or site ID can carry a postal code
    assert (cl["postal"][np.isin(cl["loc_type"], [LOC_GPS, LOC_SITE, LOC_NONE])] == 0).all()


def test_postal_fallback_keeps_every_record_and_reports_its_own_level():
    for degraded in (False, True):
        w = generate(SCENARIOS["heatwave"], 42, degraded)
        al = align(w)
        q = location_quality(al)
        by = {l["id"]: l for l in q["levels"]}
        assert q["total_records"] == len(w.clinic["obs"])  # nothing dropped
        assert sum(l["records"] for l in q["levels"]) == q["total_records"]
        assert by["zone_postal"]["records"] > 0 and by["region"]["label"].startswith("Unlocated")
    a = location_quality(align(generate(SCENARIOS["heatwave"], 42, False)))
    b = location_quality(align(generate(SCENARIOS["heatwave"], 42, True)))
    pa = next(l["share"] for l in a["levels"] if l["id"] == "zone_postal")
    pb = next(l["share"] for l in b["levels"] if l["id"] == "zone_postal")
    assert pb > pa  # in degraded mode the postal fallback absorbs part of the lost GPS


def test_crosswalk_is_unambiguous_and_documented():
    zones = [v["zone"] for v in C.POSTAL_CROSSWALK.values() if "zone" in v]
    assert set(zones) == set(C.ZONE_IDS)  # every zone has a code
    assert len(C.POSTAL_CROSSWALK) == len(set(C.POSTAL_CROSSWALK))  # one entry per code
    assert not (set(C.POSTAL_UNMAPPED) & set(C.POSTAL_CROSSWALK))  # unknown codes are not in the table
    for v in C.POSTAL_CROSSWALK.values():
        assert ("zone" in v) != ("district" in v)  # a code maps to a zone XOR to a district only
