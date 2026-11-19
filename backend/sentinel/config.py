"""Central configuration for SentinelWorks.

All geography, employers and sites are SYNTHETIC. Thresholds marked "guidance" come from
published sources listed in GUIDANCE_SOURCES; detection thresholds are prototype settings that
are checked on tuning seeds (see validation.py) and are not clinically validated.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:  # read-only deployment filesystem; the packaged data files are still readable
    pass

START_DATE = date(2026, 6, 1)  # Monday
N_DAYS = 90
BASELINE_WINDOW = 28  # days in the rolling baseline
GUARD_DAYS = 2  # most recent days excluded from the baseline (guard band)
DOW_WINDOW = 56  # days used to estimate day-of-week factors
WARMUP_DAYS = 35  # detection starts after the first full baseline is available
COMPLETENESS_ESTIMATION_DAYS = 28  # reporting-lag profile estimated from the first 4 weeks only


def day_to_date(d: int) -> date:
    return START_DATE + timedelta(days=int(d))


def date_to_day(s: str) -> int:
    return (date.fromisoformat(s) - START_DATE).days


DISTRICTS = [
    {"id": "DN", "name": "North District"},
    {"id": "DS", "name": "South District"},
]

# Schematic grid: 3 columns x 2 rows. Synthetic GPS coordinates are expressed in grid units.
ZONES = [
    {"id": "N1", "name": "Riverside Works", "district": "DN", "col": 0, "row": 0, "pop_factor": 1.0, "heat_offset": 0.4, "pm_offset": 2.0},
    {"id": "N2", "name": "Ring Road Corridor", "district": "DN", "col": 1, "row": 0, "pop_factor": 1.1, "heat_offset": 0.6, "pm_offset": 4.0},
    {"id": "N3", "name": "Industrial Estate", "district": "DN", "col": 2, "row": 0, "pop_factor": 0.9, "heat_offset": 0.9, "pm_offset": 9.0},
    {"id": "S1", "name": "Old Market", "district": "DS", "col": 0, "row": 1, "pop_factor": 1.2, "heat_offset": 0.3, "pm_offset": 3.0},
    {"id": "S2", "name": "Port Logistics", "district": "DS", "col": 1, "row": 1, "pop_factor": 1.0, "heat_offset": 0.5, "pm_offset": 6.0},
    {"id": "S3", "name": "New Town", "district": "DS", "col": 2, "row": 1, "pop_factor": 0.8, "heat_offset": 0.0, "pm_offset": 0.0},
]
ZONE_IDS = [z["id"] for z in ZONES]
ZONE_INDEX = {z["id"]: i for i, z in enumerate(ZONES)}
DISTRICT_IDS = [d["id"] for d in DISTRICTS]
DISTRICT_INDEX = {d["id"]: i for i, d in enumerate(DISTRICTS)}
ZONE_DISTRICT_IDX = [DISTRICT_INDEX[z["district"]] for z in ZONES]

# ---------------------------------------------------------------------------------------------
# Postal-code crosswalk (SYNTHETIC; stands in for an official postcode-to-zone table).
# Rule: a record is placed in a zone through its postal code ONLY if the code appears here with a single zone.
#   * "zone" entry      -> unambiguous: record is placed in that zone (level "zone_postal").
#   * "district" entry  -> the code straddles zones (boundary code): record stays at district level.
#   * code not listed   -> unknown or legacy code: never guessed; the record keeps the district it already
#                          has, or stays "unlocated" (region only) if it has none.
# ---------------------------------------------------------------------------------------------
POSTAL_CROSSWALK = {
    "11001": {"zone": "N1"}, "11002": {"zone": "N1"},
    "12001": {"zone": "N2"}, "12002": {"zone": "N2"},
    "13001": {"zone": "N3"}, "13002": {"zone": "N3"},
    "21001": {"zone": "S1"}, "21002": {"zone": "S1"},
    "22001": {"zone": "S2"}, "22002": {"zone": "S2"},
    "23001": {"zone": "S3"}, "23002": {"zone": "S3"},
    "12099": {"district": "DN", "note": "N1/N2 boundary"},
    "13099": {"district": "DN", "note": "N2/N3 boundary"},
    "22099": {"district": "DS", "note": "S1/S2 boundary"},
    "23099": {"district": "DS", "note": "S2/S3 boundary"},
}
# Codes that appear in the synthetic clinic feed but are NOT in the crosswalk (typos, retired codes).
POSTAL_UNMAPPED = ["99999", "98765", "11000"]
# Which boundary codes a record of a given zone can carry.
POSTAL_BOUNDARY_FOR_ZONE = {"N1": ["12099"], "N2": ["12099", "13099"], "N3": ["13099"],
                            "S1": ["22099"], "S2": ["22099", "23099"], "S3": ["23099"]}

EMPLOYERS = {
    "EMP_A": "Employer A (construction contractor)",
    "EMP_B": "Employer B (logistics operator)",
    "EMP_C": "Employer C (municipal works)",
}

COHORTS = [
    {"id": "outdoor", "label": "Outdoor crew"},
    {"id": "yard", "label": "Yard & logistics"},
    {"id": "indoor", "label": "Indoor & office"},
]

# cohort shares by site type
SITE_TYPES = {
    "construction": {"label": "Construction site", "shares": [0.80, 0.12, 0.08]},
    "road_works": {"label": "Road maintenance depot", "shares": [0.85, 0.08, 0.07]},
    "logistics": {"label": "Logistics yard", "shares": [0.25, 0.60, 0.15]},
    "warehouse": {"label": "Warehouse", "shares": [0.05, 0.55, 0.40]},
}

SITES = [
    {"id": "A-01", "name": "Site A-01", "zone": "N1", "employer": "EMP_A", "type": "construction", "workforce": 320},
    {"id": "A-02", "name": "Site A-02", "zone": "N2", "employer": "EMP_A", "type": "construction", "workforce": 260},
    {"id": "A-03", "name": "Site A-03", "zone": "S1", "employer": "EMP_A", "type": "construction", "workforce": 45},
    {"id": "A-04", "name": "Site A-04", "zone": "N3", "employer": "EMP_A", "type": "construction", "workforce": 180},
    {"id": "B-01", "name": "Site B-01", "zone": "S2", "employer": "EMP_B", "type": "logistics", "workforce": 410},
    {"id": "B-02", "name": "Site B-02", "zone": "N3", "employer": "EMP_B", "type": "warehouse", "workforce": 230},
    {"id": "B-03", "name": "Site B-03", "zone": "S3", "employer": "EMP_B", "type": "warehouse", "workforce": 150},
    {"id": "B-04", "name": "Site B-04", "zone": "N2", "employer": "EMP_B", "type": "logistics", "workforce": 60},
    {"id": "C-01", "name": "Site C-01", "zone": "N1", "employer": "EMP_C", "type": "road_works", "workforce": 140},
    {"id": "C-02", "name": "Site C-02", "zone": "S1", "employer": "EMP_C", "type": "road_works", "workforce": 120},
    {"id": "C-03", "name": "Site C-03", "zone": "S2", "employer": "EMP_C", "type": "road_works", "workforce": 95},
    {"id": "C-04", "name": "Site C-04", "zone": "S3", "employer": "EMP_C", "type": "road_works", "workforce": 70},
]
SITE_IDS = [s["id"] for s in SITES]
SITE_INDEX = {s["id"]: i for i, s in enumerate(SITES)}
SITE_ZONE_IDX = [ZONE_INDEX[s["zone"]] for s in SITES]

SIGNALS = {
    "heat_index": {"label": "Heat index (daily max)", "short": "Heat index", "unit": "°C", "family": "environmental", "kind": "value", "source": "Environmental sensor network (synthetic)"},
    "pm25": {"label": "PM2.5 (24-hour mean)", "short": "PM2.5", "unit": "µg/m³", "family": "environmental", "kind": "value", "source": "Air-quality monitors (synthetic)"},
    "heat_illness_visits": {"label": "Clinic visits: heat illness", "short": "Heat-illness visits", "unit": "visits/day", "family": "clinical", "kind": "count", "source": "Clinic syndromic feed (synthetic)"},
    "resp_visits": {"label": "Clinic visits: respiratory", "short": "Respiratory visits", "unit": "visits/day", "family": "clinical", "kind": "count", "source": "Clinic syndromic feed (synthetic)"},
    "sick_leave": {"label": "Sick-leave absences", "short": "Sick leave", "unit": "absences/day", "family": "occupational", "kind": "count", "source": "Employer HR absence logs (synthetic, aggregated)"},
    "fatigue_reports": {"label": "Fatigue / heat-stress reports", "short": "Fatigue reports", "unit": "reports/day", "family": "occupational", "kind": "count", "source": "Site safety reports (synthetic, aggregated)"},
}
SIGNAL_IDS = list(SIGNALS)
CLINIC_SIGNALS = ["heat_illness_visits", "resp_visits"]
ENV_SIGNALS = ["heat_index", "pm25"]
OCC_SIGNALS = ["sick_leave", "fatigue_reports"]

FAMILY_LABELS = {"environmental": "Environmental exposure", "clinical": "Clinical / service", "occupational": "Workplace / HR"}

# Risk tracks: transparent weighted fusion of signals from different families.
TRACKS = {
    "heat": {
        "label": "Heat stress (outdoor work)",
        "weights": {"heat_index": 0.35, "heat_illness_visits": 0.35, "sick_leave": 0.15, "fatigue_reports": 0.15},
        "clinical_signal": "heat_illness_visits",
    },
    "respiratory": {
        "label": "Air-quality respiratory risk",
        "weights": {"pm25": 0.40, "resp_visits": 0.40, "sick_leave": 0.20},
        "clinical_signal": "resp_visits",
    },
}

DEFAULT_DETECTION = {
    "z_clip": 4.0,  # per-signal z-scores are clipped to [0, z_clip] before weighting
    "watch_score": 1.4,  # composite score for Watch (needs two consecutive evaluations)
    "watch_single_z": 3.0,  # exposure z needed (with a guidance exceedance) for an immediate Watch
    "action_score": 2.4,  # shipped Action operating point; grid search overrides it during tuning
    "corroboration_z": 2.0,  # a family counts as corroborating when one of its signals is >= this z
    "min_families_action": 2,  # Action needs at least two materially different families
    "persistence_days": 2,  # Action conditions must hold on consecutive evaluations ...
    "extreme_score": 3.0,  # ... unless the composite is extreme
    "close_after_normal_days": 2,  # incident closes after this many consecutive Normal evaluations
    "cooldown_days": 3,  # a re-trigger within this many days re-opens the previous incident
    "min_completeness": 0.6,  # minimum estimated reporting completeness to use a day's count
    "stale_days": 2,  # signal is "delayed" when its newest usable day is older than this
    "min_weight_available": 0.5,  # below this share of track weight, a quiet zone is "Insufficient data"
}

TUNED_PATH = DATA_DIR / "tuned_params.json"


def detection_params() -> dict:
    params = dict(DEFAULT_DETECTION)
    try:
        tuned = json.loads(TUNED_PATH.read_text())["params"]
        for key in ("action_score", "corroboration_z"):
            value = tuned[key]
            if type(value) not in (float, int) or not 0 < value <= params["z_clip"]:
                raise ValueError(key)
            params[key] = value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Invalid or missing tuned configuration. Run python -m sentinel.validation from backend.") from exc
    return params


SMALL_CELL_THRESHOLD = 5

# Published guidance used in evidence boxes. Thresholds are cited, not invented.
HEAT_INDEX_DANGER_C = 39.4  # NWS "Danger" category starts at 103 °F
HEAT_INDEX_EXTREME_CAUTION_C = 32.2  # NWS "Extreme Caution" starts at 90 °F
PM25_UNHEALTHY = 55.5  # US EPA 2024 AQI "Unhealthy" breakpoint (24-h, µg/m³)
PM25_USG = 35.5  # US EPA AQI "Unhealthy for Sensitive Groups" breakpoint (24-h, µg/m³)

GUIDANCE_SOURCES = {
    "nws_heat_index": {
        "title": "US National Weather Service: Heat Index categories",
        "url": "https://www.weather.gov/ama/heatindex",
        "note": "Danger: 103–124 °F (39.4–51.1 °C), heat cramps or heat exhaustion likely; Extreme Danger: 125 °F (51.7 °C) or higher. The page gives °F; the °C values are conversions.",
    },
    "nws_full_sun": {
        "title": "US National Weather Service: Heat Index (shade assumption)",
        "url": "https://weather.gov/safety/heat-index",
        "note": "Heat index values assume shade; full sunshine can increase them by up to 15 °F (about 8 °C, a conversion of the published °F figure).",
    },
    "osha_water_rest_shade": {
        "title": "US OSHA: 'Water. Rest. Shade.' heat illness campaign",
        "url": "https://www.dol.gov/newsroom/releases/osha/osha20170626",
        "note": "OSHA's Water. Rest. Shade. message: water about every 15 minutes and frequent breaks in the shade. Acclimatisation of new workers is covered in other OSHA heat publications. This news release could not be opened by an automated check on 18 Nov 2026 (HTTP 403), so the wording was corroborated in other OSHA material.",
    },
    "osha_proposed_rule": {
        "title": "US OSHA: Heat injury and illness prevention, proposed rule fact sheet",
        "url": "https://www.osha.gov/sites/default/files/publications/heat-rulemaking-factsheet.pdf",
        "note": "Proposed in OSHA's August 2024 notice of proposed rulemaking: paid rest breaks of at least 15 minutes every 2 hours at a heat index of 90 °F or higher. The rule's current status was not verified; the fact sheet itself could not be opened by an automated check on 18 Nov 2026 (HTTP 403).",
    },
    "epa_aqi_pm25": {
        "title": "US EPA: 2024 Air Quality Index update for PM2.5",
        "url": "https://www.epa.gov/system/files/documents/2024-02/pm-naaqs-air-quality-index-fact-sheet.pdf",
        "note": "24-hour PM2.5 'Unhealthy' category begins at 55.5 µg/m³; effective 6 May 2024.",
    },
}
