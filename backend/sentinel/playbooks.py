"""Action playbooks: rules table mapping (risk track, tier, audience role) to a proportionate,
human-reviewed suggestion with a named responsible role, evidence and explicit assumptions.

Suggestions are advisory. They require review by the responsible officer and do not trigger any
notification or dispatch in this prototype.
"""
from __future__ import annotations

from . import config as C

ROLES = {
    "public_health_officer": {"label": "District Public Health Officer", "short": "Public Health Officer"},
    "hse_lead": {"label": "Workplace HSE Safety Lead (Employer A)", "short": "HSE Safety Lead"},
    "policy_analyst": {"label": "Policy & Regulatory Analyst", "short": "Policy Analyst"},
}

RULES = {
    ("heat", "action"): {
        "hse_lead": {
            "owner": "Site HSE Safety Lead / Occupational Health & Safety Manager",
            "title": "Adjust outdoor work at affected sites",
            "actions": [
                "Move the heaviest outdoor tasks out of the hottest hours for the affected zone's sites.",
                "Enforce scheduled water, rest and shade breaks for outdoor crews; make cool drinking water available at the work area.",
                "Use buddy checks and supervisor observation for heat-illness symptoms; apply acclimatisation plans for new or returning workers.",
                "Confirm first-aid and emergency response arrangements for heat illness at each affected site.",
            ],
            "guidance": ["nws_heat_index", "osha_water_rest_shade", "osha_proposed_rule", "nws_full_sun"],
        },
        "public_health_officer": {
            "owner": "District Public Health Officer",
            "title": "Targeted heat-health advisory for the affected zone",
            "actions": [
                "Issue a targeted heat-health advisory to employers with outdoor workforces in the affected zone (not a district-wide shutdown).",
                "Ask clinics serving the zone to prioritise heat-illness triage and report daily.",
                "Check availability of public cooling and drinking-water points in the zone.",
                "Coordinate with workplace safety leads on site-level heat plans; review again in 24 hours.",
            ],
            "guidance": ["nws_heat_index", "osha_water_rest_shade"],
        },
        "policy_analyst": {
            "owner": "Policy & Regulatory Analyst",
            "title": "Record event for threshold and inspection review",
            "actions": [
                "Log the event, the signals involved and the response timeline for the regulatory evidence base.",
                "Compare the heat-index level at which health signals rose with current workplace heat-plan triggers.",
                "Flag affected sectors for follow-up review of heat-illness prevention plans.",
            ],
            "guidance": ["nws_heat_index", "osha_proposed_rule"],
        },
    },
    ("heat", "watch"): {
        "*": {
            "owner": "Site HSE Safety Lead (workplaces); District Public Health Officer (zone)",
            "title": "Heightened monitoring",
            "actions": [
                "Remind site supervisors in the zone of the heat-illness prevention plan and the forecast.",
                "Check that water, rest and shade provisions are ready to be used if conditions persist.",
                "Re-check clinic and absence signals at the next update before escalating.",
            ],
            "guidance": ["nws_heat_index", "osha_water_rest_shade"],
        },
    },
    ("respiratory", "action"): {
        "public_health_officer": {
            "owner": "District Public Health Officer",
            "title": "Targeted air-quality health advisory",
            "actions": [
                "Issue an air-quality health advisory for the affected zone, with specific advice for people with asthma, COPD or heart disease.",
                "Advise reduced strenuous outdoor activity in the zone while PM2.5 remains elevated.",
                "Inform clinics in the zone of the respiratory uptick and ask for daily reporting.",
                "Share the episode with the environmental agency for source investigation.",
            ],
            "guidance": ["epa_aqi_pm25"],
        },
        "hse_lead": {
            "owner": "Site HSE Safety Lead",
            "title": "Reduce outdoor exertion and dust exposure at affected sites",
            "actions": [
                "Reschedule strenuous outdoor tasks at sites in the affected zone while PM2.5 is elevated.",
                "Apply site dust-suppression controls and review the site's existing respiratory-protection programme.",
                "Encourage workers with respiratory conditions to report symptoms early (aggregated reporting only).",
            ],
            "guidance": ["epa_aqi_pm25"],
        },
        "policy_analyst": {
            "owner": "Policy & Regulatory Analyst",
            "title": "Record episode for air-quality policy review",
            "actions": [
                "Log the episode, affected zone and health-signal response for the evidence base.",
                "Compare episode frequency in industrial zones with other zones for planning review.",
            ],
            "guidance": ["epa_aqi_pm25"],
        },
    },
    ("respiratory", "watch"): {
        "*": {
            "owner": "District Public Health Officer (zone); Site HSE Safety Lead (workplaces)",
            "title": "Heightened monitoring",
            "actions": [
                "Monitor PM2.5 and respiratory clinic visits at the next update.",
                "Prepare advisory text in case the signal persists or is corroborated.",
            ],
            "guidance": ["epa_aqi_pm25"],
        },
    },
}

ASSUMPTIONS = {
    "heat": [
        "Zone heat-index readings represent conditions at outdoor work sites; full sun can add up to about 8 °C (15 °F) to heat-index values.",
        "Clinic visits coded as heat illness reflect true heat-related illness (syndromic coding is approximate).",
        "Outdoor workforce shares come from the (synthetic) site register and are assumed current.",
        "Clinic data normally arrive within about 3 days; delayed or missing feeds lower data confidence.",
    ],
    "respiratory": [
        "Zone PM2.5 monitors represent exposure across the zone.",
        "Respiratory clinic visits are an indicator; they are not specific to air pollution.",
        "Clinic data normally arrive within about 3 days; delayed or missing feeds lower data confidence.",
    ],
}


def playbook(track: str, tier: str, role: str) -> dict | None:
    if tier not in ("watch", "action"):
        return None
    rules = RULES[(track, tier)]
    rule = rules.get(role) or rules.get("*")
    if rule is None:
        return None
    others = [ROLES[r]["label"] for r in rules if r not in (role, "*")]
    return {
        "track": track, "tier": tier, "audience_role": role,
        "title": rule["title"], "owner": rule["owner"], "actions": rule["actions"],
        "other_roles_with_playbooks": others,
        "guidance": [{"id": g, **C.GUIDANCE_SOURCES[g]} for g in rule["guidance"]],
        "assumptions": ASSUMPTIONS[track],
        "review_note": "The cited sources support only the statements in their notes (exposure categories and general preventive measures). The specific actions, responsible owners, reporting schedules and review intervals (for example 'review again in 24 hours') are proposed locally by this prototype and are not taken from those sources. The named officer decides how to respond.",
    }


def evidence_lines(track: str, contributions: list, t: int) -> list:
    """Plain-language evidence built from the incident's actual values (traceable to the data)."""
    lines = []
    for c in contributions:
        if c["value"] is None or c["expected"] is None:
            lines.append({"signal": c["signal"], "text": f"{C.SIGNALS[c['signal']]['label']}: no usable data ({c['status']}).", "kind": "gap"})
            continue
        sig = C.SIGNALS[c["signal"]]
        day = C.day_to_date(c["day"]).isoformat()
        unit = sig["unit"]
        if sig["kind"] == "value":
            txt = f"{sig['label']}: {c['value']:.1f} {unit} on {day} vs expected {c['expected']:.1f} {unit} (z = {c['z']:+.1f})."
            if c["signal"] == "heat_index" and c["value"] >= C.HEAT_INDEX_DANGER_C:
                txt += " Above the NWS 'Danger' heat-index level (39.4 °C / 103 °F)."
            if c["signal"] == "pm25" and c["value"] >= C.PM25_UNHEALTHY:
                txt += " In the US EPA AQI 'Unhealthy' range (≥ 55.5 µg/m³, 24-h)."
            elif c["signal"] == "pm25" and c["value"] >= C.PM25_USG:
                txt += " In the US EPA AQI 'Unhealthy for Sensitive Groups' range (≥ 35.5 µg/m³)."
        else:
            ratio = c["value"] / c["expected"] if c["expected"] else None
            txt = f"{sig['label']}: about {ratio:.1f}× the expected level on {day} (z = {c['z']:+.1f})." if ratio else f"{sig['label']}: z = {c['z']:+.1f}."
        lines.append({"signal": c["signal"], "text": txt, "kind": "signal"})
    return lines
