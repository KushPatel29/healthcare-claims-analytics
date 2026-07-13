# How to open this Power BI Project

Everything is pre-built in code — no clicking required. The semantic model
(TMDL) has 6 tables, 4 relationships, and 17 DAX measures; the report (PBIR)
has 3 finished pages with 25 visuals (gauge, area, treemap, donut, funnel,
stacked columns), styled with the Meridian Corporate custom theme.

## Steps

1. Double-click **`RevenueCycleAnalytics.pbip`**.
2. When the report opens, click **Refresh** to load the CSVs (`data/` —
   run `python data_generator/generate_claims_data.py` first if it's empty).
3. If you moved/cloned this repo somewhere else: Home → Transform data →
   Edit parameters → set **DataPath** to your local
   `...\healthcare-claims-analytics` repo root, then Refresh.

## Report pages

| Page | What it answers |
|---|---|
| Revenue Cycle Scorecard | Is the revenue cycle healthy? (denial rate vs 5% target, collections trend) |
| Denial Analytics | Why are we being denied, by whom, and what does it cost? |
| AR Aging | Which dollars are stuck, how old are they, and who do we chase? |
