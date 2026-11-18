# SentinelWorks

**Population and workforce health early warning: Hacks2Skills Population Health Challenge 06 prototype.**

SentinelWorks combines environmental exposure data (heat index, PM2.5) with aggregated clinic and
workplace signals (heat-illness and respiratory visits, sick leave, fatigue reports). It detects unusual
patterns against past-only baselines, explains which signals drove each alert, suggests a proportionate
action with a named responsible role, and enforces privacy controls in the backend. A built-in
validation harness measures how the method performs.

> **All data are synthetic** and replayed day by day. Geography, employers, sites, postal codes and workers
> are fictional. Nothing in the dashboard is a live feed. Estimated health benefits are estimates, not
> measurements.

## Start and stop

Requirements: Python 3.10+ (tested with 3.14 on Windows 11). Node 20+ is needed only to rebuild the frontend.
The built frontend (`frontend/dist`) and the computed validation results (`backend/data/validation_results.json`,
`backend/data/tuned_params.json`) are already in the repository. No internet connection, API keys, map tiles or
paid services are required.

**Start (Windows PowerShell)**

```powershell
cd "D:\Hackatons\Hack2Skill Hackaton\backend"
pip install -r requirements.txt          # first time only
python -m uvicorn sentinel.api:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. The first request after start takes a few seconds while the scenarios are computed.
On macOS or Linux: `cd backend && python3 -m uvicorn sentinel.api:app --port 8000`, or `./run.sh` (builds the
frontend if missing and computes validation if missing, then starts the server).

**Stop**

- In the window that is running the server: press **Ctrl+C**.
- From another PowerShell window, if the server was started in the background:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess }
```

The server keeps one small SQLite file (`backend/data/state.db`) with recorded responses. Stopping loses nothing else.
If port 8000 is already in use, the start command fails with "address already in use": stop the old server first.

| Task | Command (from `backend/`) |
|---|---|
| Run the tests | `pip install -r requirements-dev.txt` then `python -m pytest -q tests` (about 30 s) |
| Recompute tuning, validation and the stress test (runtime varies by machine) | `python -m sentinel.validation` |
| Rebuild the frontend after editing it | `cd ..\frontend; npm install; npm run build` |
| Frontend dev server (hot reload, proxies `/api` to :8000) | `cd ..\frontend; npm run dev` |

## What is in the product

| View | What it shows |
|---|---|
| **Situation overview** | KPIs, a labelled synthetic 6-zone schematic coloured by tier, alert feed (incident changes only), observed-vs-expected timeline for the selected zone, feed freshness, location quality with the five fallback levels and the postal crosswalk, filters (district, risk track, signal, time range) |
| **Incidents** | Incident list (open, past, all). For each incident: score against thresholds, per-signal contribution shares, observed-vs-expected charts, daily tier strip, signal onsets, location quality, data-quality issues, role-specific playbook (actions, owner, evidence, guidance links, assumptions), and a **response record** stored in SQLite |
| **Workplaces & privacy** | What the current role can see; **weekly** site table; weekly cohort table with `<5` and "masked" cells; weekly trend; server-generated CSV exports; district-level series |
| **Validation** | Measured sensitivity, PPV, false alerts, lead time versus a comparator, latency per feed, location fallback (0 dropped records), standard versus degraded inputs, a **stress test** with varied exposure-to-health lags, per-scenario replay, tuning grid. Design targets are labelled "not a result" |
| **Impact estimates** | Measured inputs (excess visits, excess sick-leave days, share of excess still ahead when the alert fires) and an estimator whose assumptions you edit. Visits, people and sick-leave days are separate columns; all outputs are labelled as estimates |

Top-bar controls: **scenario**, **replay date** (step, play, slider, **Jump to event**), **degraded inputs** (30% of GPS tags
removed and clinic feed delayed by 2 extra days) and **role** (demo sign-in). A **Privacy guard** banner states the active rules.

## How it works

1. **Synthetic data** (`synth.py`): 90 days (1 Jun to 29 Aug 2026), 6 zones in 2 districts, 12 sites from 3 employers.
   Clinic visits are individual pseudonymous records with an *observation day*, an *availability day* (reporting lag)
   and incomplete location (GPS 68%, site ID 14%, district only 14%, none 4%). A record that has a district but no GPS
   or site ID may also carry a postal code. HR and safety data are aggregated counts by site, cohort and day.
   Generation is seeded and reproducible.
2. **Alignment** (`pipeline.py`): records go to the most precise reliable level: GPS, then site ID, then **postal code**
   (only if the documented crosswalk maps it to exactly one zone), then district, otherwise unlocated. Nothing is
   guessed: boundary codes, unknown codes and missing codes stay at district level or are labelled unlocated. The
   crosswalk (`config.POSTAL_CROSSWALK`) is a synthetic stand-in for an official postcode-to-zone table. No record
   is dropped. Counts are rebuilt *as of* each replay day from records available by that day. Days with less than 60%
   expected completeness are "pending", not zero.
3. **Detection** (`detection.py`): for each signal, a baseline from the 28 days ending 2 days before the evaluated day
   (past-only), with day-of-week factors for counts. For clinical counts the baseline **ignores days whose count is
   withheld (1 to 4 visits)**, so published scores cannot be used to recover them. z-scores are clipped to [0, 4].
   Environmental exposure uses its peak over the last 72 hours.
4. **Fusion and tiers**: composite = sum of weight x clipped z per risk track (heat: heat index 0.35, heat-illness visits
   0.35, sick leave 0.15, fatigue 0.15; respiratory: PM2.5 0.40, respiratory visits 0.40, sick leave 0.20).
   - **Action required**: score >= 2.4, at least two different signal families with z >= 2, on two consecutive days (or score >= 3.0).
   - **Watch**: score >= 1.4 on two consecutive days, or an exposure reading at z >= 3 that also exceeds published guidance
     (NWS heat index 39.4 C "Danger"; US EPA PM2.5 55.5 ug/m3 "Unhealthy").
   - **Insufficient data**: a quiet track requires a same-day environmental observation, a usable clinical feed within the configured delay tolerance, and sufficient signal coverage. Missing exposure data or a clinical reporting gap interrupts the two-day recovery count. Corroborated warnings remain visible during an outage.
5. **Incidents** (`engine.py`): one incident per zone, track and episode. It closes after 2 normal days. A re-trigger
   within 3 days re-opens the same incident (cooldown and de-duplication).
6. **Playbooks** (`playbooks.py`): a rules table keyed by track, tier and role. Each entry gives proportionate actions, an
   owner, guidance sources (NWS, OSHA, US EPA, with links), assumptions and a human-review note.
7. **Response record** (`store.py`): "Record response" saves an acknowledgement, planned response or "reviewed: not
   actionable", with role and time. **It does not notify anyone and does not dispatch anything.**
8. **Release policy** (`release.py`, `privacy.py`, `auth.py`, `api.py`): see the next section.

| Role | Sees |
|---|---|
| District Public Health Officer | All zones and signals; site aggregates; weekly cohort tables; both exports |
| Workplace HSE Safety Lead (Employer A) | Only zones with Employer A sites; environmental series; own sites and cohorts (weekly). Clinic data appear only as contribution shares |
| Policy & Regulatory Analyst | All zone statuses and incidents; environmental series; clinical counts (daily) and workplace counts (weekly) at district level only; no site data |

The impact estimator uses an independent synthetic population (seed 901) so its event totals cannot expose hidden replay counts.

## Release policy for counts

Why: an API-only attack script showed that publishing a total next to its daily cells, or a parent next to its children,
let a reader subtract the visible numbers and recover hidden 1 to 4 counts with 100% accuracy (four routes, hundreds
of recoveries). Masking one endpoint at a time cannot fix that, so one policy governs every endpoint and export that
the same role can reach:

- **R1 Weekly grain.** Sick-leave and fatigue counts are released only as complete Monday to Sunday weeks, at site, cohort,
  zone and district level. No daily workplace count, rate, expected value or band is released anywhere. Incident pages show
  a workplace signal's share of the score, not its daily value or z.
- **R2 Final weeks only, stable cells.** A week is released only after reporting is complete, and its masks are computed from
  final data. The same cell is therefore identical on every replay date: asking again, or with another date, reveals nothing new.
- **R3 Small cells.** 1 to 4 is shown as `<5`; zero is released.
- **R4 Secondary suppression, per role.** If a small cell is hidden, a large sibling is hidden too, and if the remaining sum is
  too small for that to protect the cell, the parent total is withheld instead. Applied only between levels the same role can
  request. Withheld large cells read "masked".
- **R5 Consistency downwards.** A withheld parent's visible children are withheld too.
- **R6 Derived numbers.** Rates and weekly expected values are released only if every input is released.
- **R7 Denominators.** Workforce and headcount come from the site register. A headcount below 5 is masked with complementary suppression.
- **R8 Baselines from released data only.** Displayed baselines, bands and z-scores for clinic counts, and the detection baseline
  for clinical counts, leave out withheld days.

Effect on usability: for the officer on 29 Jul, 8 of 12 sites show a released weekly sick-leave total (4 are withheld).
Under the earlier daily-plus-weekly design it was 2 of 48 sampled rows.

## Measured validation results

Thresholds were selected on **tuning seeds 1 to 20** by grid search (maximum F1, at most 0.25 false Action alerts per
zone-month). The figures below come from **30 held-out seeds (101 to 130)**. Each seed contains a random heatwave (1 to 2 zones),
a PM2.5 episode and a heat spike with no health response: 77 health events in total. They were regenerated on 18 Nov 2026 after the
postal fallback and the released-data baseline were added. Reproduce with `python -m sentinel.validation`.

| Metric | Standard inputs | Degraded inputs | Single-signal comparator (standard) |
|---|---|---|---|
| Sensitivity, Action tier | **90.9%** (70/77) | 77.9% | 81.8% |
| Sensitivity, Watch or higher | 100% | 98.7% | n/a |
| PPV, Action tier | **73.7%** (70/95) | 78.9% (60/76) | 94.0% |
| False Action alerts per zone-month | **0.076** | 0.048 | 0.012 |
| Watch-only incidents not linked to an event, per zone-month | 0.81 | 0.60 | n/a |
| Median lead time vs comparator (detected by both) | **3 days** (n = 61) | 3 days (n = 53) | reference |
| Median lead time vs weekly report | 5 days | 3.5 days | n/a |
| Clinic records dropped by location fallback | 0 of 17,014 | 0 of 17,014 | n/a |

The comparator is single-signal clinic surveillance: syndrome count z >= 3 on the two newest complete days, using the same data
availability. It is **more precise** (PPV 94% vs 74%, 0.012 vs 0.076 false alerts per zone-month); SentinelWorks buys broader and
earlier detection with more false Action alerts. The earlier proposal targets (sensitivity >= 85%, PPV >= 60%, lead >= 2 days) are design goals, not results.
The selected Action score (2.4) is the highest value in the tested grid, so a better setting may lie beyond it.

**The lead time depends strongly on the timing we injected into the synthetic worlds** and on how the comparator is defined (it needs two
complete reporting days). It is not evidence of the same lead time against real reporting.

### Stress test (fixed thresholds, 12 held-out worlds per row, seeds 301 to 312, nothing re-tuned)

The health response (clinic visits, sick leave, fatigue) starts `lag` days after exposure onset; lag 0 means every signal moves on the same day.

| Condition | Events | Action sensitivity | PPV | False Action / zone-month | Comparator sensitivity | Median lead vs comparator |
|---|---|---|---|---|---|---|
| lag 0 d (simultaneous) | 29 | 93% | 82% | 0.045 | 83% | 3 d (n = 23) |
| lag 1 d | 29 | 93% | 90% | 0.023 | 76% | 3 d (n = 21) |
| lag 2 d | 29 | 90% | 87% | 0.030 | 66% | 3 d (n = 19) |
| lag 3 d | 29 | **76%** | 81% | 0.038 | 48% | 3 d (n = 13) |
| lag 4 d | 29 | **55%** | 80% | 0.030 | 52% | 3 d (n = 7) |
| compound heat + PM2.5, same zone, lag 1 d | 29 | 100% | 85% | 0.038 | 69% | 3 d (n = 20) |

With the thresholds held fixed, Action sensitivity falls below the 85% design target at lags of 3 and 4 days (13 of 29 events missed at lag 4), while
Watch-level detection stays at 100% (Watch fires on exposure above published guidance alone). The comparator also degrades. The median lead is 3 days in
every row, which shows it is largely a property of the comparator's definition and the scenarios, not a general result.

## Five-minute demo walkthrough

1. **Overview, Heatwave scenario.** Use **Jump to event** to open 26 Jul (heatwave begins). Step forward. On **27 Jul**, N2 goes to
   **Watch**; on **28 Jul**, N1 and N2 go to **Action required**. The alert feed shows only changes.
2. **Click N1** for the heat-index timeline with Watch and Action shading. Switch to heat-illness visits: provisional days are hollow
   points and withheld days are grey ticks on the baseline (a gap, never zero). Switch to sick leave: it shows complete weeks only.
3. **Open incident details.** Score against thresholds, contribution shares, evidence, the playbook for the District Public Health Officer,
   and the data-quality box. Press **Record response**, then reload: it is still there. The panel states that nobody is notified.
4. **Switch role to HSE Safety Lead.** S2 and S3 are hatched as out of scope; clinic counts are hidden but shares remain. Under
   **Workplaces & privacy**, the site table and cohort table show released weekly totals, `<5` and "masked" cells with tooltips.
5. **Switch role to Policy Analyst.** Site views are blocked. District series and the zone signals export remain.
6. **Turn on degraded inputs.** GPS-located records fall (68% to 47%); the postal fallback places 27% of records instead, so zone-located
   records fall only from 93% to 88%. N1 still reaches Action; no records are dropped.
7. **Edge-case scenario, 28 to 31 Jul.** S1 heat spike: Watch, never Action. S2 clinic batch-reports late: **Insufficient data**, not Normal.
8. **Validation, then Impact.** Measured table, trade-off chart, stress test. On the Impact page change the effectiveness assumption.

## Remaining privacy limitations

SentinelWorks reduces re-identification risk. **It does not guarantee privacy.** What was tested, and what remains:

- **Not differential privacy.** The method is small-cell and secondary suppression plus a weekly release grain. Nothing adds noise.
- **Measured against an interval attack.** An attacker who combines every released weekly total (cohort, site, zone, district tables, series and
  exports) and reasons exactly about the sum constraints recovered **0 of 908** hidden small cells in the three scenarios, standard and degraded inputs,
  three roles, two signals. For 18 of them the candidate set shrank from 4 values to 2 or 3 (mean 3.98 of 4). That residual narrowing is real.
- **Masking pattern is information.** A reader learns that a cell is below 5 (`<5`) or that a larger neighbour exists (`masked`), and learns released
  totals exactly.
- **Repeated releases.** Weekly cells are final and identical on every replay date (tested). Clinic counts are still daily and provisional; their withheld
  days are fixed once reporting is final.
- **Baseline and score channels (measured, then closed).** Before the fix an oracle attacker recovered 62% of hidden clinic days from the published baseline
  and 45% from rounded scores, shares and tiers. After the fix, payloads and engine outputs for the next 28 days are identical whatever the hidden count
  (tested). This covers the 28 days **after** a hidden day. **Not measured:** the alert tier on a hidden day itself and the day after, which still depends
  on the hidden value (a threshold crossing can reveal one bit); and the use of workplace z-scores, which feed composite scores and shares at zone level.
  Zone-level workplace values aggregate several sites and cohorts, so they cannot isolate a single hidden site or cohort cell, but this was reasoned, not tested.
- **Other outputs.** Rates and expected values are released only from released inputs (tested). Location-quality record counts below 5 are masked. The
  Impact page publishes totals summed over a whole event; they were not tested for small-cell exposure.
- **Attacks not tried.** Linking with outside information, collusion between users with different roles, timing or error-message side channels, and
  attackers who learn the method from this repository and combine it with other data sources.
- **Demo authentication.** Tokens are in memory, no passwords, and there is no audit log of data access (only of responses).

## Other honest limitations

- **Synthetic data only.** The results show how the method behaves on simulated patterns that we designed. They are not evidence of
  real-world performance, and the simulation's effect sizes are our assumptions. The postal crosswalk is synthetic.
- **Weekly workplace data are coarser.** Site and zone workplace figures arrive as weeks; the alert tiers still use the underlying daily data
  internally. Workplace signals no longer show their own z or deviation on incident pages.
- **Degraded inputs** reduce Action sensitivity (90.9% to 77.9%) mostly because delayed clinic data arrive after short events have passed.
- **Contribution shares** describe the scoring rule. They are not causal attributions or calibrated probabilities. "Data confidence" describes
  data completeness and location quality, not event likelihood.
- **Thresholds** come from a small grid on synthetic tuning seeds. Guidance thresholds (NWS, EPA) are US sources and may need local equivalents.
- **Impact units.** Clinic visits are not unique people or cases and not only workers, so lost workdays are not derived from them. Avoided
  sick-leave days use the measured excess sick-leave days and are estimates; no avoided case has been observed.
- **Not built:** Bayesian fusion, scan statistics, differential privacy, enteric and chemical scenarios, responder-feedback re-tuning, fairness dashboard,
  AI agents, chatbots, external messaging or dispatch, a back-test risk-score replay chart, interval widening below 80% completeness.

## Repository layout

```
backend/sentinel/   config, synth, pipeline, detection, engine, playbooks, privacy, release, auth, store, validation, api
backend/tests/      83 tests: data, detection, RBAC via direct API calls, suppression, weekly release policy and interval attack,
                    inference through baselines and scores, postal fallback, exports, responses, validation arithmetic, event matching
backend/data/       tuned_params.json, validation_results.json (state.db is created at runtime)
frontend/src/       React + TypeScript + Tailwind CSS + Recharts + Lucide
docs/               REQUIREMENTS_CHECKLIST.md (rebuilt from Population Health.docx)
```
