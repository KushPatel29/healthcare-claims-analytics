# How to open this Power BI Project

Everything is pre-built in code — no clicking required. The semantic model
(TMDL) has 14 tables, 10 relationships, and 42 DAX measures; the report (PBIR)
has 6 finished pages with 53 visuals, styled with the Meridian Corporate custom
theme.

## Steps

1. Double-click **`RevenueCycleAnalytics.pbip`**.
2. When the report opens, click **Refresh** to load the CSVs. Run the pipeline
   first if `data/` or `output/` is empty:
   ```
   python data_generator/generate_claims_data.py
   python engine/build_rcm_metrics.py
   python canadian/generate_activity_data.py
   python engine/build_activity_metrics.py
   python engine/health_economics.py
   ```
3. If you moved/cloned this repo somewhere else: Home → Transform data →
   Edit parameters → set **DataPath** to your local
   `...\healthcare-claims-analytics` repo root, then Refresh.

## Report pages

| Page | What it answers |
|---|---|
| Acute Activity & Funding | What does a weighted case cost, and do patients stay longer than their case mix predicts? |
| ALC, Flow & SPC | How many beds are lost to patients with nowhere to go — and is this month's rate a signal or noise? |
| Revenue Cycle Scorecard | Is the revenue cycle healthy? (denial rate vs 5% target, collections trend) |
| Denial Analytics | Why are we being denied, by whom, and what does it cost? |
| AR Aging | Which dollars are stuck, how old are they, and who do we chase first? |
| Predictive Yield (NRV) | How much of the open AR will we actually collect, and which accounts yield the most cash? |

The two Canadian pages lead deliberately: in a single-payer system there are no
payers, denials, or bad-debt reserve, so a health-authority reader should not
have to page past four screens of US revenue cycle to find the work that speaks
to them.

## Two modelling decisions worth knowing

**There are two date tables, and that is on purpose.** `dim_month` covers the
revenue-cycle claims period (2025-07 onward); `dim_activity_month` covers the
inpatient abstracts (2024-07 to 2026-06). Twelve activity months fall outside
`dim_month` entirely, so relating them would push half the authority's activity
into a blank row and every month-sliced visual would under-report silently.
Widening `dim_month` backwards instead would hang twelve empty months off the
left of every revenue-cycle chart. The two subject areas cover different periods
and are never cross-filtered, so they get their own calendars.

**Risk-adjusted figures are read, not recomputed.** `LOS Index` and
`Readmission O/E` come from `activity_by_facility`, and the control limits on
the p′ chart come from `spc_alc_stay` — both engine outputs. They are indirectly
standardised with empirical-Bayes shrinkage, and the control limits are set from
an 18-month phase I baseline and extended forward. Neither is something a DAX
measure evaluating in filter context reproduces correctly, and a second, subtly
different answer with no test behind it is worse than no answer. Power BI is the
presentation layer; `pytest` guards the arithmetic.
