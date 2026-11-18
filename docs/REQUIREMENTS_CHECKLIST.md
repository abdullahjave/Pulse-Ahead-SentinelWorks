# SentinelWorks: requirement status against "Population Health.docx"

Primary specification: `Population Health.docx` (text read in full, plus its embedded dashboard mock-ups). `SentinelWorks.docx` in the project folder is only a folder-structure skeleton and is **not** used as a source. This checklist was rebuilt from the specification on 18 Nov 2026, not carried over from an earlier version.

**Status key.** **T** = implemented and exercised by an automated test. **B** = implemented and checked in the browser or through the live API only (no automated test). **P** = partially implemented (limit stated). **M** = missing.
All data are synthetic. Figures quoted in the specification ("28% rise", "60% / 40%", "38 °C for 48 h", "3.5 days earlier", "24 months of logs") are illustrations; every figure in the product is computed from a pipeline run on synthetic data.

Tests: `cd backend && python -m pytest -q tests` (83 tests: `test_sentinel.py`, `test_review.py`, `test_release_policy.py`, `test_inference.py`, `test_postal.py`).

## A. The six requirements

| # | Specification | Where | Evidence | Status |
|---|---|---|---|---|
| 1 | **Multi-signal surveillance**: at least two distinct stream types (environment plus clinical or HR) | `synth.py`, `pipeline.py`, `detection.py::fuse` | `test_ninety_days_and_two_families_minimum`; Action needs two families | **T** |
| 2a | **Explainable detection**: deviation from a baseline | `detection.py` | `test_baseline_is_past_only`, `test_replay_status_on_a_day_does_not_depend_on_later_data` | **T** |
| 2b | Attribution: which signals drove it (% weights) | `api.py::incident_detail` | `test_active_incident_workflow_end_to_end_and_persisted` | **T**. **P** for workplace signals: only share and weight are released. Their daily value, z and % deviation are withheld (weekly release policy), so the spec's "(+40% above baseline) contributed 45%" line is complete only for environmental and clinical signals |
| 2c | Where (district / site) and over what timeframe (start-to-peak window) | incident `where`, `when`, `timeline` | same test; browser check | **T** |
| 3 | **Actionable decision support**: what to do, who is responsible, evidence and assumptions | `playbooks.py`, `Incidents.tsx` | `test_active_incident_workflow_end_to_end_and_persisted`, `test_decision_rights_by_role` | **T**. Proportionality is a text review, not an automated test. Guidance links are US sources (NWS, OSHA, EPA) |
| 4a | **Privacy**: small-cell suppression (<5 masked) in the backend | `privacy.py`, `release.py`, `api.py` | `test_no_small_counts_leak_in_any_response`, `test_exports_never_contain_small_counts_or_identifiers`, `test_hidden_component_cannot_be_recovered_from_incident_payloads`, `test_release_policy.py`, `test_inference.py` | **T** for the measured channels. **P** overall: see README, "Remaining privacy limitations" |
| 4b | Drill-down disabled when a cell is small | `api.py` | `test_suppressed_cohort_blocks_drilldown`, `test_cohort_daily_drilldown_is_gone_for_every_role` | **T**. The daily drill-down was removed for all roles; the weekly cohort table masks small cells |
| 4c | Role-based access; no names, IDs or disciplinary fields | `auth.py`, `api.py` | `test_role_matrix_on_direct_requests`, `test_role_is_not_taken_from_query_or_headers`, `test_hse_lead_sees_only_own_employer_everywhere`, `test_missing_or_forged_token_is_rejected`, `test_heavy_and_destructive_endpoints_are_restricted` | **T**. Demo sign-in: no passwords, in-memory tokens |
| 5a | **Operational validation**: lead time vs traditional reporting | `validation.py`, `Validation.tsx` | `test_reported_validation_metrics_are_arithmetically_consistent_with_the_stored_run` | **T**. Lead time depends strongly on the injected scenario timing and on the comparator's definition: the stress test shows it constant at 3 d while sensitivity falls |
| 5b | Sensitivity, PPV, false-alarm rate | same | `test_event_matching_window_edges`, `test_wrong_zone_or_track_does_not_match`, `test_duplicate_alerts_for_one_event_are_not_extra_true_positives` | **T**. PPV (73.7%) and false alerts (0.076 per zone-month) are worse than the single-signal comparator (94.0%, 0.012) |
| 5c | Data latency and missing-data behaviour | latency and location tables, degraded toggle | `test_missing_data_is_not_reported_as_normal`, `test_availability_time_is_respected`, `test_degraded_mode_reduces_zone_resolution_but_keeps_records` | **T** |
| 6 | **Health and resilience impact**: fewer emergencies, fewer lost workdays, evidence for regulators | `Impact.tsx` | `test_impact_keeps_visits_and_absence_in_separate_units` | **P** by nature: estimates from user-set assumptions; no avoided case, visit or absence day is measured. Visits, people and sick-leave days are kept apart. Regulator evidence is a stored response log only, not quantified |

## B. "Quick checklist" and design guidance

| Specification | Evidence | Status |
|---|---|---|
| Pick one sharp, high-impact use case | Two risk tracks (heat stress in outdoor work; air-quality respiratory risk) | **P**: two, not one |
| Messy geolocation: fall back to postal codes, district zones or workplace site IDs; never drop a record; tag confidence | GPS, then site ID, then **postal-code crosswalk**, then district, then unlocated (`config.POSTAL_CROSSWALK`, `pipeline.resolve_location`). `test_postal_placement_follows_the_crosswalk_exactly`, `test_boundary_unknown_and_missing_codes_are_never_guessed`, `test_postal_fallback_keeps_every_record_and_reports_its_own_level`, `test_no_record_is_dropped_by_location_fallback` | **T**. The crosswalk is a synthetic stand-in for an official table; boundary, unknown and missing codes are never guessed |
| Calibrated alerts: confidence or severity tier (Watch / Warning / Action) | Normal, Watch, Action Required, plus Insufficient data and a data-confidence label | **T**. "Warning" is not a separate tier |

## C. Blueprint specs 1 to 5

| Spec | Specification | Evidence | Status |
|---|---|---|---|
| 1a | Align by time bucket and spatial zone | daily buckets; 6 zones | **T** (`test_availability_time_is_respected`). 6-hour buckets not built |
| 1b | Hierarchical location matcher (lat/long to micro-zone, then facility / district code) | see B | **T** |
| 1c | Synthetic generator, 60 to 90 days, at least 2 injected anomalies | `synth.py` (90 days, reproducible) | **T** (`test_generator_is_reproducible`) |
| 2a | Rolling 14- or 28-day baseline, day-of-week adjusted, z-score or % deviation | 28-day, 2-day guard, past-only | **T**. The "14-day" view is a chart range, not a second baseline |
| 2b | Weighted fusion | `config.TRACKS` weights | **T** |
| 2c | Structured attribution output | see 2b | **T** / **P** as stated |
| 2d | Three tiers with cooldown and de-duplication | `engine.py` | **T** (`test_one_incident_per_episode_deduplicates_daily_evaluations`) |
| 3a | Backend small-cell filter, mask as <5, disable drill-down | `privacy.py`, `release.py` | **T** / **P** as in 4a |
| 3b | Role switcher in the top bar, zero names/IDs/disciplinary flags | `Shell.tsx`, `auth.py` | **T** (server side); the switcher itself **B** |
| 4 | Playbook card on every Action alert: action, owner, evidence and assumptions | `playbooks.py` | **T** |
| 5a | "Model Performance and Validation" tab with lead time, sensitivity, PPV, false alarms | `Validation.tsx` | **B** (page) / **T** (numbers) |
| 5b | Degraded-input toggle | top bar; API `degraded` | **T** (Action sensitivity 90.9% to 77.9%, 0 records dropped) |

## D. Dashboard section and the mock-up images

| Specification | Evidence | Status |
|---|---|---|
| Role switcher among three roles that adapts playbooks, owners and scope | `test_role_matrix_on_direct_requests` | **T** |
| **Privacy Guard banner** ("Small-Cell Suppression (<5 masked) ... Boundary Enforced") | top-bar banner added 18 Nov | **B** |
| Interactive 6-zone grid; click updates a baseline-vs-live chart showing the trigger window ahead of the clinical peak | `Overview.tsx`, `SignalChart.tsx` | **B**. Workplace signals now show weekly points (no daily series) |
| Zone cards showing exposure % and health/absence % and location confidence | cards show tier, composite score and "district records zone-located" | **P**: no per-signal % on the card |
| Attribution drawer with exact % weights, evidence, and a **"Dispatch Action Protocol"** button | `Incidents.tsx`; the button is **"Record response"** | **P by design**: it records an acknowledgement or planned response; it does not notify or dispatch (`notification_sent: false`). No external messaging was added |
| Small-cell table with hover tooltip explaining masking | `Workplaces.tsx`, `CountCell` | **B** (tooltips), **T** (masking) |
| Degraded toggle and validation tab replaying **three** scenarios with lead time, sensitivity, PPV, false alarms | heatwave, air quality, edge case; "Jump to event" | **T** (`test_key_dates_come_from_the_scenario_definition`). The mock's "Scenario C: 30% missing GPS and delayed logs" is the degraded toggle here |
| Back-test replay chart: risk score vs traditional retrospective curve, trigger day and ER-alert day marked | the Validation page has a lead-time table, a histogram and a per-scenario table | **M** as a time-series chart |
| "Dynamic confidence bands: intervals widen when completeness drops below 80%" | provisional days are drawn hollow; days under 60% completeness are "pending" and not used | **P**: no interval widening is implemented |
| "Delayed clinical feed: environmental and HR signals maintain Watch-level vigilance until the clinical batch arrives" | exposure above published guidance raises Watch at once; a late clinic shows "Insufficient data", never Normal | **P** (`test_missing_data_is_not_reported_as_normal`, `test_decoy_exposure_without_health_response_does_not_reach_action`). HR-only vigilance is not implemented |
| "Evaluated across 24 months of historical logs" (mock header) | 90 synthetic days | **M**, and not claimed. The specification's text asks for 60 to 90 days |

## E. Added in this round (and what verified it)

| Change | Verified by |
|---|---|
| Weekly workplace release policy (`release.py`) on every endpoint and export | `test_release_policy.py`: interval-propagation attack over all released totals, 0 exact recoveries of 908 hidden cells (3 roles, 2 signals, standard and degraded) |
| Displayed baselines and engine baselines for clinical counts use released data only | `test_inference.py`: payloads, scores, shares and tiers identical for hidden counts 1 to 4 |
| Postal-code fallback with a documented crosswalk | `test_postal.py` |
| "Record response" wording | DOM check: no "dispatched" or "notification sent" text on the incident page |
| Validation rerun plus stress test | `data/validation_results.json` regenerated; page rendering checked |

## F. Not built (deliberate)
Bayesian fusion, scan statistics, differential privacy, enteric and chemical scenarios, responder-feedback re-tuning, fairness dashboard, AI agents, chatbots, real GPS or map tiles, external messaging or dispatch.

## G. Checks that were not completed
- The Workplaces page was screenshotted only at phone width, and otherwise checked through its DOM text. The browser's screen-capture call hung intermittently during this session (the machine had about 1 GB of free memory).
- Overview with the five-level location bar was confirmed through DOM text, not a desktop screenshot.
- CSV download buttons were not clicked in the browser; the same files were fetched and scanned over HTTP (96 files, no exact 1 to 4 values) before the weekly redesign, and the weekly exports are covered by `test_exports_are_weekly_for_workplace_counts_and_match_the_api`.
- Neither the interval-widening nor the back-test replay chart exists, so nothing tests them.
