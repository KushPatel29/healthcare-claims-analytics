# BUSINESS CASE
## Transitional Care and Community Bridging Program

**Sponsor:** Vice President, Clinical Operations
**Prepared by:** Decision Support
**Date:** 30 July 2026 · **Version:** 1.0 · **Status:** For approval

> Every figure reproduces from `python engine/health_economics.py` and is
> pinned by the test suite in `tests/test_health_economics.py`. Synthetic data
> throughout — no real patients, sites, or budgets.

---

## 1. Executive summary

We are asked to fund a $1.45M annual program to reduce alternate-level-of-care
(ALC) days across the authority. The clinical case is straightforward. The
financial case is not, and this document is largely about why.

**The finding in one paragraph.** ALC currently consumes 56.9 staffed acute beds
and has been running at an elevated level since January 2026. A transitional
care program at 22% effectiveness would release 4,572 ALC days a year — 12.5
beds. Whether that is worth $1.45M depends entirely on one thing the analysis
cannot settle and the organisation can: **whether those beds get used.** If they
do, the program is dominant (it saves more than it costs and improves health).
If they do not, it costs $659,000 net per year for 3.43 QALYs — an ICER of
$192,000 per QALY, well outside the range we would normally fund.

**Recommendation: approve, conditional on a documented backfill commitment.**

---

## 2. The problem

| | |
|---|---|
| ALC days, 24 months | 41,567 |
| Bed equivalents | **56.9** |
| Cost at $1,150/day | **$47.8M** |
| Share of all patient days | 13.8% |
| Trend | Step increase from January 2026, statistically confirmed |
| Concentration | Medicine = 47% of ALC bed days from 28% of discharges |

ALC is the largest recoverable block of inpatient capacity in the authority. It
is also the one least amenable to action inside a hospital, because the
constraint sits in community placement rather than in acute care.

---

## 3. Options considered

| Option | Description | Annual cost | Why / why not |
|---|---|---:|---|
| 1 | Status quo | $0 | Fails: the trend is upward and confirmed as a special cause |
| 2 | **Transitional care, authority-wide** | **$1.45M** | **Recommended.** Addresses the full pool; phased rollout limits downside |
| 3 | Harbourview only | $0.40M | Highest rate but only 19% of the bed days; risks displacing pressure |
| 4 | Purchase residential capacity | Not costed | Correct long-term answer, wrong instrument for this budget cycle; requires Ministry engagement and 18–24 months' lead |

---

## 4. Costs

| Component | Annual |
|---|---:|
| 5.0 FTE care coordinators | $525,000 |
| 2.0 FTE occupational / physical therapy | $230,000 |
| 1.0 FTE program manager | $135,000 |
| Purchased interim community capacity | $460,000 |
| Program overhead, training, evaluation | $100,000 |
| **Total** | **$1,450,000** |

Offsetting cost: patients discharged earlier still consume community services,
at $195 per day. At the base case that is **$892,000** a year. This is the line
most commonly omitted from bed-day business cases, and omitting it overstates
the benefit by roughly a third.

---

## 5. The costing question that decides this case

An avoided bed day is not automatically a dollar. It becomes one only under
specific conditions, and the two perspectives below make the difference
explicit rather than burying it in an assumption.

### Perspective A — opportunity cost ($1,150/day, fully absorbed)

Values the released day at the full per-diem, on the argument that the bed is
immediately reoccupied by a patient currently boarding in Emergency or waiting
for a surgical slot. Defensible: our sites run at high occupancy and the queues
are real.

| | |
|---|---:|
| ALC days avoided | 4,572 |
| Value of avoided days | $5,258,000 |
| Program + community cost | $2,342,000 |
| **Incremental cost** | **−$2,916,613** |
| Incremental QALYs | 3.43 |
| **Result** | **Dominant** (cost-saving *and* health-improving) |
| Net monetary benefit @ $50k/QALY | **$3,088,077** |

### Perspective B — cash-releasing (32% of per-diem)

Values only the genuinely variable component — supplies, medication, food,
premium and agency nursing. The ward, the establishment, the heat, and the
overhead do not go anywhere unless a bed is actually closed.

| | |
|---|---:|
| Value of avoided days | $1,683,000 |
| Program + community cost | $2,342,000 |
| **Incremental cost** | **+$658,980** |
| Incremental QALYs | 3.43 |
| **ICER** | **$192,163 per QALY** |
| Net monetary benefit @ $50k/QALY | **−$487,516** |

**Both are correct.** They answer different questions. Perspective A asks what
the program is worth to the health system; Perspective B asks what it does to
next year's operating budget. A business case that presents only A is the reason
finance departments distrust business cases.

---

## 6. Sensitivity analysis

### 6.1 One-way (tornado), on Perspective B

| Parameter | Low | High | Swing in NMB | Flips the decision? |
|---|---|---|---:|---|
| Cash-releasing share of per-diem | 20% | 45% | $1,314,556 | **Yes** |
| Effectiveness (ALC reduction) | 10% | 35% | $1,093,732 | **Yes** |
| Program cost | $1.10M | $1.90M | $800,000 | No |
| ALC per-diem | $950 | $1,350 | $585,263 | No |
| Community per-diem | $140 | $260 | $548,684 | No |
| QALY gain per day avoided | 0.0002 | 0.0015 | $297,204 | No |

Two parameters can flip the recommendation on their own, and neither is a
clinical parameter. The decision is governed by **how much cost is genuinely
released** and **how well the program works** — not by what it costs to run.
That is a useful finding: negotiating the program price down does not rescue a
weak case, and it should not be where the effort goes.

### 6.2 Probabilistic (10,000 Monte Carlo iterations)

All six parameters varied simultaneously — Beta distributions for proportions,
Gamma for costs and utilities, each fitted to the plausible range above.

| Willingness to pay | P(cost-effective), Perspective A | P(cost-effective), Perspective B |
|---:|---:|---:|
| $0 | 99.6% | 10.4% |
| $50,000 | **99.7%** | **16.9%** |
| $100,000 | 99.8% | 26.0% |
| $150,000 | 99.8% | 36.0% |

Probability the program is outright cost-saving on the cash-releasing view:
**10.4%**.

The two curves do not converge at any threshold we would consider. The
uncertainty that matters here is structural — which perspective applies — and no
amount of additional Monte Carlo sampling resolves a structural question.

---

## 7. Benefits not monetised

Deliberately excluded from the ICER, and stated here rather than quietly folded
into the numbers:

- **Emergency Department decompression.** Released beds reduce boarding, which
  is the single largest driver of ED length of stay. Real, and the strongest
  operational argument for the program.
- **Surgical access.** Fewer cancellations for bed unavailability.
- **Patient experience and family burden.** An extended non-therapeutic stay is
  a poor experience and a poor use of anyone's time.
- **Staff retention.** Wards carrying a heavy ALC census report lower morale.

These are excluded because monetising them requires assumptions I cannot
defend, not because they are unimportant. Under Perspective B they are, in
practice, the actual case for the program.

---

## 8. Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Released capacity never converted to activity | **Critical** — invalidates the entire case | High | Pre-registered backfill plan and baseline measure before go-live |
| Effectiveness below 10% | High | Medium | Staged funding; phase 2 contingent on measured phase-1 result |
| Community partners cannot absorb volume | High | Medium | Confirm capacity in writing before phase 2 |
| Benefit attributed to the program that would have occurred anyway | Medium | **High** | Control-chart monitoring against the established baseline, not a before/after average |
| Cost pressure moves to the community budget | Medium | High | Community per-diem is already costed in; monitor as a separate line |

---

## 9. Measurement and evaluation

The program will be evaluated on the **stay-level ALC control chart**, with
limits fixed from the pre-implementation baseline and extended forward. Two
deliberate choices:

- **Not a before/after average.** Comparing mean ALC before and after would
  claim credit for regression to the mean, and the January 2026 shift means the
  "before" period is itself not homogeneous.
- **Not ALC days per 100 patient days.** That metric is overdispersed by a
  factor of 4.8 and its control limits are unusable without correction. Stays
  with any ALC day is one independent observation per patient and behaves.

| Measure | Baseline | Target at 12 months |
|---|---|---|
| Stays with any ALC day | 11.1% | Special-cause decrease sustained ≥ 6 months |
| ALC bed equivalents | 56.9 | ≤ 46 |
| ED boarding hours (backfill realisation) | To be established | Improvement, or the capacity benefit is not being realised |
| Program cost variance | — | Within 5% |

---

## 10. Recommendation

**Approve Option 2 at $1.45M annually, conditional on:**

1. A documented backfill commitment naming what the released beds will be used
   for, signed before go-live.
2. Baseline measurement of ED boarding hours and surgical postponements,
   established before implementation.
3. Staged funding — phase 2 released only on a measured phase-1 result.
4. The program presented to the Board as a **capacity and access** initiative,
   not a savings initiative.

Condition 4 is not presentational. On the evidence, this program is very
probably the right thing to do and very probably not a saving. Saying both is
what makes the first half believable.

---

## Appendix A — model parameters

| Parameter | Base | Range | Distribution | Source of range |
|---|---:|---|---|---|
| ALC reduction | 22% | 10–35% | Beta | Published transitional-care and early-discharge-planning programs |
| Program cost | $1.45M | $1.10–1.90M | Gamma | Staffing establishment ±20%, purchased capacity ±35% |
| ALC per-diem | $1,150 | $950–1,350 | Gamma | Fully absorbed staffed acute bed-day cost |
| Cash-releasing share | 32% | 20–45% | Beta | Variable-cost share of ward operating cost |
| Community per-diem | $195 | $140–260 | Gamma | Home support / interim residential day rate |
| QALY per ALC day avoided | 0.00075 | 0.0002–0.0015 | Gamma | Deconditioning, delirium, hospital-acquired infection avoided |
| Willingness to pay | $50,000/QALY | — | — | Conventional Canadian threshold |

## Appendix B — reproducing this analysis

```bash
python canadian/generate_activity_data.py    # 40k synthetic DAD abstracts
python engine/build_activity_metrics.py      # CPWC, LOS index, ALC, SPC charts
python engine/health_economics.py            # base case, tornado, PSA/CEAC
pytest tests/ -v                             # 92 invariants
```

Outputs: `output/hta_base_case.csv`, `output/hta_tornado.csv`,
`output/hta_psa_ceac.csv`, `output/activity_by_facility.csv`,
`output/spc_signals.csv`.
