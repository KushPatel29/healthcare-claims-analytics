# BRIEFING NOTE

**To:** Vice President, Clinical Operations
**From:** Decision Support
**Date:** 30 July 2026
**Subject:** Alternate Level of Care — sustained increase since January, and what it is costing
**Classification:** For Decision

> All figures in this note are reproducible from the command line
> (`python engine/build_activity_metrics.py`) and are re-verified by 92
> automated tests on every code change. Source: synthetic DAD-shaped abstracts,
> 24 months to 30 June 2026, 39,573 discharges. **No real patient data.**

---

## Issue

Alternate Level of Care (ALC) days have moved to a **new, higher level since
January 2026** and have stayed there for six consecutive months. This is a
sustained shift, not month-to-month variation — the statistical test is set out
below. At the current level ALC consumes **56.9 staffed acute beds** across the
authority and **$47.8M over 24 months**, and it is the largest single block of
recoverable inpatient capacity we hold.

## Background

ALC days are days spent in an acute bed by a patient who no longer requires
acute care and is waiting for a discharge destination — most often residential
care, a home-support package, or an assisted-living placement. Clinically the
patient is ready. Operationally the bed is unavailable, which converts directly
into Emergency Department boarding and surgical postponement.

Two points of framing matter for the decision:

1. **ALC is not a hospital performance failure.** It measures community
   capacity, not acute-care quality. Our sites cannot solve it inside their own
   walls, which is why it has no single accountable owner today.
2. **ALC cost is not efficiency.** Cost per weighted case at Harbourview is
   $8,519, the second highest in the authority. Strip the ALC per-diem out and
   it falls to $7,502 — mid-pack. Reporting the headline figure without the
   split has been telling us the wrong story about which site is spending well.

## Analysis

### The increase is a genuine signal, not noise

Monthly indicators were assessed on statistical process control charts with
limits set from the 18-month baseline (July 2024 – December 2025) and extended
across the current monitoring window. Three findings:

| Indicator | Result |
|---|---|
| Stays with any ALC day | **Special cause from January 2026** — six consecutive months above the upper control limit, peaking at 5.2 sigma |
| ALC days per 100 patient days | **Special cause from February 2026** — consistent with the above |
| 30-day readmission rate | **No special cause.** The process is stable; the two indicators are not moving together |

The readmission result matters as much as the ALC result: it tells us this is a
discharge-destination problem specifically, not a general deterioration in care.

**A methodological caution worth recording.** The ALC-days chart is heavily
overdispersed — measured dispersion is **4.8x** the Poisson assumption, because
one patient waiting sixty days contributes sixty correlated days rather than
sixty independent events. Run without correction that chart signals in 41
instances, nearly all of them false. Corrected (Laney u′) it signals 10 times,
and a chart built on *stays* rather than *days* — one observation per patient —
signals cleanly and immediately. **If ALC days per 100 patient days is being
monitored anywhere in the organisation on ordinary control limits, that chart
is generating false alarms and should be reviewed.**

### Where it sits

| Site | ALC rate (% of patient days) | Bed equivalents | 24-month ALC cost |
|---|---:|---:|---:|
| Harbourview Hospital | **15.7%** | 10.7 | $8.95M |
| Riverbend Regional | 14.6% | **20.3** | **$17.0M** |
| Two Rivers Health Centre | 13.9% | 2.6 | $2.18M |
| Cedar Valley General | 12.8% | 10.0 | $8.38M |
| Fernwood Memorial | 12.6% | 7.9 | $6.59M |
| Mount Ashton General | 11.7% | 5.6 | $4.70M |

Harbourview has the highest *rate*; Riverbend holds the largest *volume*. A
program targeted only at the worst rate would leave the largest pool of bed days
untouched. By program, **Medicine accounts for 26.6 of the 56.9 bed
equivalents** — 47% of the total from 28% of discharges.

Risk-adjusted 30-day readmission (observed/expected, indirectly standardised on
program, age band, and comorbidity level) ranges from 0.94 to 1.08. No site is
an outlier, and crude comparison ranks the sites differently from adjusted
comparison — Riverbend's crude rate looks poor until its case mix is accounted
for.

## Options

| | Option | Annual cost | Effect |
|---|---|---:|---|
| 1 | **Status quo** | $0 | ALC continues at the new level; 56.9 beds remain unavailable |
| 2 | **Transitional care and community bridging, authority-wide** | $1.45M | ~22% reduction, 4,572 ALC days released (12.5 beds) |
| 3 | **Phase 1 at Harbourview only** | ~$0.4M | ~2.3 beds released; leaves 79% of the problem untouched |
| 4 | **Purchase residential capacity directly** | Not costed | Longer lead time, outside our capital envelope; requires Ministry engagement |

## Financial and economic analysis

A full economic evaluation is at [`docs/BUSINESS_CASE.md`](BUSINESS_CASE.md).
The result is genuinely conditional and the condition is the decision:

- **If the freed beds are used** — backfilled from the ED boarding and surgical
  queues — the program is **dominant**: it costs less than it saves and improves
  health outcomes. Probability cost-effective at $50,000/QALY: **99.7%**.
- **If the freed beds are not used** — no ward closes, no staffing changes, the
  capacity simply exists — only the variable cost is released. The program then
  costs **$659,000 net** for 3.43 QALYs, an ICER of **$192,000/QALY**. At the
  conventional $50,000 threshold, probability cost-effective: **17%**.

The same program, the same evidence, opposite recommendations. The variable
driving it is not clinical effectiveness; it is whether the organisation
converts released bed days into activity.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Freed capacity is absorbed without being counted, so the benefit is never demonstrated | **High** | Pre-register the backfill measure before go-live; report released bed days against ED boarding hours monthly |
| Effectiveness below the 10% lower bound | Medium | Staged funding; continue only on a measured phase-1 result |
| Community partners cannot absorb the referral volume | Medium | Confirm capacity with community partners before phase 2 |
| Displacement — ALC pressure moves to a neighbouring site | Medium | Authority-wide scope rather than single-site |

## Recommendation

**Approve Option 2, conditional on a capacity-realisation commitment.**

Fund the program authority-wide at $1.45M annually, with three conditions:

1. **Do not book it as a savings initiative.** On the cash-releasing view it is
   not one, and a business case that overstates savings is discovered at the
   first variance report.
2. **Attach an explicit backfill commitment** — a named plan for what the
   released beds will be used for, with a baseline measure taken before go-live.
   This is the assumption the entire economic case rests on and it is the one
   thing inside our control.
3. **Report monthly on the stay-level ALC control chart**, with a scheduled
   review at 12 months against the phase-1 result.

## Next steps

| Action | Owner | By |
|---|---|---|
| Confirm community partner capacity | Community Services | 15 Sep 2026 |
| Establish backfill baseline (ED boarding hours, surgical postponements) | Decision Support | 30 Sep 2026 |
| Phase 1 go-live at Harbourview and Riverbend | Clinical Operations | 1 Nov 2026 |
| First control-chart review post-implementation | Decision Support | 31 Mar 2027 |

---

*Prepared by Decision Support. Analysis, data, and every figure in this note are
reproducible: `python canadian/generate_activity_data.py && python
engine/build_activity_metrics.py && python engine/health_economics.py`.*
