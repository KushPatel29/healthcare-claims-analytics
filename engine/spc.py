"""
Statistical process control for health-system indicators.

The default way a monthly indicator gets reported — "readmissions were 11.4%,
up from 10.8% last month" — is a category error. Every process varies. Reacting
to ordinary variation as though it were a change (tampering) makes the process
worse, and the reverse mistake, sitting on a genuine shift because it looked
small, is how a problem runs for two quarters before anyone escalates.

Control charts are the method health authorities and accreditation bodies use to
separate the two. This module implements the two charts that fit health
indicators, plus the standard special-cause detection rules:

  * **p-chart** — a proportion with a varying denominator (readmission rate,
    mortality rate, C-difficile rate per discharge). Limits are computed per
    point because a month with 300 discharges is noisier than one with 900.

  * **u-chart** — a count per unit of exposure (ALC days per 100 patient days,
    falls per 1,000 patient days). Poisson-based, again with variable limits.

  * **Western Electric rules** — the four classical special-cause tests. Rule 1
    catches a spike; rules 2-4 catch a sustained shift that never breaches 3
    sigma, which is the failure mode a threshold alert always misses.

  * **Laney's correction (p-prime / u-prime)** — the fix for overdispersion,
    which health data has in abundance. See the note below; getting this wrong
    is the most common way a healthcare control chart lies.

Nothing here is health-specific in the maths; it is deliberately a general SPC
library so the same code serves any indicator the authority reports.

Overdispersion, and why `laney=True` exists
-------------------------------------------
A binomial or Poisson chart assumes every unit of exposure is an independent
trial with the same risk. Health data routinely violates both halves of that.
ALC *days* are the clearest case: one patient waiting 60 days for a residential
bed contributes 60 correlated days, not 60 independent ones. The true variance
is then far larger than the Poisson variance the chart assumes, the limits come
out much too tight, and nearly every month signals — a chart that cries wolf
every month gets ignored within a quarter, which is worse than having no chart.

Laney's correction estimates the *actual* dispersion from the data — via the
average moving range of the standardised residuals — and scales sigma by that
factor. When there is genuine overdispersion, only genuinely exceptional months
survive.

One caveat worth stating plainly, because it is easy to sell this as free and it
is not. sigma_z is *not* clamped at 1.0, because Laney's published method does
not clamp it. On well-behaved binomial data the factor lands near 1.0 but not on
it — a 30-point simulation here measures 0.79, which *tightens* the limits by
about 20% and makes the chart slightly more trigger-happy than the uncorrected
one, not less. So `laney=True` is not a no-op you can switch on everywhere and
forget; it is a correction for a condition you have checked for. Clamping at 1.0
is a defensible house rule (underdispersion in attribute data usually means the
denominator is wrong rather than that variation is genuinely sub-binomial) and
would be a no-op on every chart this repo publishes, since all three measure
above 1.0 — but it is a deviation from the published method, so it is offered as
a note rather than taken silently.

The right answer is often also to change the unit of analysis: the proportion
of *stays* with any ALC day is one independent observation per patient and is
nearly free of this problem. Both charts are produced, deliberately.

Baselines, and why `baseline=` exists
-------------------------------------
The centre line has to come from a period the process was stable in — the
"phase I" baseline — and is then *extended forward* over the period being
monitored ("phase II"). Computing it from the whole series instead, including
the months you are trying to assess, is a subtle and very common error with a
counter-intuitive consequence: a genuine upward shift late in the series drags
the centre line up, and every stable month before it is then flagged as a
downward special cause. You end up with a chart that reports a problem in the
period where nothing happened, and understates the period where something did.

Passing `baseline=n` computes the centre from the first n points and applies it
to all of them. Omitting it uses the whole series, which is correct only when
you have independent reason to believe the whole series is stable.
"""

import math

# d2 for a moving range of n=2 — the constant that converts an average moving
# range into a standard-deviation estimate.
D2_N2 = 1.128

# Sigma zones used by the Western Electric rules.
RULES = {
    1: "1 point beyond 3 sigma",
    2: "2 of 3 consecutive points beyond 2 sigma, same side",
    3: "4 of 5 consecutive points beyond 1 sigma, same side",
    4: "8 consecutive points on the same side of the centre line",
}


def dispersion_factor(chart):
    """Laney's sigma_z: the observed dispersion of the standardised residuals.

    1.0 means the data disperses exactly as the binomial/Poisson model predicts.
    Above 1.0 is overdispersion (clustering, heterogeneity between periods);
    below 1.0 is underdispersion, usually a sign the denominator is wrong.

    Estimated from the average *moving range* rather than the standard deviation
    on purpose: a moving range only sees consecutive pairs, so a genuine
    sustained shift inflates it far less than it would inflate an overall SD.
    Using the SD here would let the very signal you are hunting for widen the
    limits until it disappears.
    """
    z = [p["z"] for p in chart if p["z"] is not None]
    if len(z) < 2:
        return 1.0
    mr = [abs(z[i] - z[i - 1]) for i in range(1, len(z))]
    sigma_z = (sum(mr) / len(mr)) / D2_N2
    return sigma_z if sigma_z > 0 else 1.0


def _apply_laney(chart, upper_cap=None, baseline=None):
    """Rescale a finished chart's limits and z-scores by the dispersion factor.

    Dispersion is measured over the baseline period for the same reason the
    centre line is: estimating it from a series that contains the shift lets
    the shift widen the limits until it hides itself.
    """
    sigma_z = dispersion_factor(chart[:baseline] if baseline else chart)
    for p in chart:
        p["dispersion_factor"] = round(sigma_z, 4)
        if p["sigma"] is None:
            continue
        p["sigma"] = p["sigma"] * sigma_z
        p["lcl"] = max(0.0, p["centre"] - 3 * p["sigma"])
        ucl = p["centre"] + 3 * p["sigma"]
        p["ucl"] = min(upper_cap, ucl) if upper_cap is not None else ucl
        p["z"] = (p["value"] - p["centre"]) / p["sigma"] if p["sigma"] > 0 else 0.0
    return chart


def p_chart(points, laney=False, baseline=None):
    """Control chart for a proportion with a varying denominator.

    `points` is an iterable of (label, numerator, denominator).
    Set `laney=True` for the p-prime chart (overdispersion-corrected limits).
    Set `baseline=n` to compute the centre line from the first n points only.

    The centre line is the *pooled* proportion (total numerator / total
    denominator), never the mean of the monthly rates — averaging rates gives
    small months the same vote as large ones and quietly shifts the centre.

    Returns a list of dicts with the per-point limits and the standardised
    deviation `z`, which is what the rule engine consumes. A month with a zero
    denominator has no limits and is passed through with `z = None`.
    """
    points = list(points)
    ref = points[:baseline] if baseline else points
    total_n = sum(d for _, _, d in ref)
    total_x = sum(x for _, x, _ in ref)
    if total_n <= 0:
        raise ValueError("p_chart: total denominator must be positive")
    centre = total_x / total_n

    out = []
    for label, x, n in points:
        if n <= 0:
            out.append({"label": label, "numerator": x, "denominator": n,
                        "value": None, "centre": centre, "sigma": None,
                        "lcl": None, "ucl": None, "z": None})
            continue
        sigma = math.sqrt(max(centre * (1 - centre), 0.0) / n)
        value = x / n
        out.append({
            "label": label, "numerator": x, "denominator": n, "value": value,
            "centre": centre, "sigma": sigma,
            # A proportion cannot leave [0, 1]; clamping the limits keeps the
            # chart honest for rare events instead of drawing a negative LCL.
            "lcl": max(0.0, centre - 3 * sigma),
            "ucl": min(1.0, centre + 3 * sigma),
            "z": (value - centre) / sigma if sigma > 0 else 0.0,
        })
    return _apply_laney(out, upper_cap=1.0, baseline=baseline) if laney else out


def u_chart(points, laney=False, baseline=None):
    """Control chart for a count per unit of exposure (Poisson).

    `points` is an iterable of (label, count, exposure) where exposure is in the
    units the rate is expressed in — e.g. per 100 patient days, pass
    `patient_days / 100`.
    Set `laney=True` for the u-prime chart (overdispersion-corrected limits).
    Set `baseline=n` to compute the centre line from the first n points only.
    """
    points = list(points)
    ref = points[:baseline] if baseline else points
    total_e = sum(e for _, _, e in ref)
    total_c = sum(c for _, c, _ in ref)
    if total_e <= 0:
        raise ValueError("u_chart: total exposure must be positive")
    centre = total_c / total_e

    out = []
    for label, c, e in points:
        if e <= 0:
            out.append({"label": label, "count": c, "exposure": e,
                        "value": None, "centre": centre, "sigma": None,
                        "lcl": None, "ucl": None, "z": None})
            continue
        sigma = math.sqrt(centre / e)
        value = c / e
        out.append({
            "label": label, "count": c, "exposure": e, "value": value,
            "centre": centre, "sigma": sigma,
            "lcl": max(0.0, centre - 3 * sigma),
            "ucl": centre + 3 * sigma,
            "z": (value - centre) / sigma if sigma > 0 else 0.0,
        })
    return _apply_laney(out, baseline=baseline) if laney else out


def western_electric(chart):
    """Apply the four Western Electric special-cause rules to a chart.

    Returns a list of signals, each naming the rule, the point that closed the
    pattern, and the direction — because "above the centre line" and "below it"
    mean opposite things clinically and only one of them is usually good news.

    Points with `z is None` (no denominator) break every run: an absent month is
    not evidence of continuity, and letting it bridge a run would manufacture
    signals out of missing data.
    """
    z = [p["z"] for p in chart]
    labels = [p["label"] for p in chart]
    signals = []

    def side(i):
        if z[i] is None:
            return 0
        return 1 if z[i] > 0 else (-1 if z[i] < 0 else 0)

    def window(end, size):
        """The `size` points ending at index `end`, or None if any are absent."""
        if end - size + 1 < 0:
            return None
        idx = range(end - size + 1, end + 1)
        if any(z[i] is None for i in idx):
            return None
        return list(idx)

    for i in range(len(z)):
        if z[i] is None:
            continue

        # Rule 1 — a single point outside the control limits.
        if abs(z[i]) > 3:
            signals.append({"rule": 1, "label": labels[i], "index": i,
                            "direction": "above" if z[i] > 0 else "below",
                            "z": z[i], "description": RULES[1]})

        # Rule 2 — 2 of 3 consecutive beyond 2 sigma on the same side.
        idx = window(i, 3)
        if idx and abs(z[i]) > 2:
            for s in (1, -1):
                if sum(1 for j in idx if side(j) == s and abs(z[j]) > 2) >= 2 \
                        and side(i) == s:
                    signals.append({"rule": 2, "label": labels[i], "index": i,
                                    "direction": "above" if s > 0 else "below",
                                    "z": z[i], "description": RULES[2]})
                    break

        # Rule 3 — 4 of 5 consecutive beyond 1 sigma on the same side.
        idx = window(i, 5)
        if idx and abs(z[i]) > 1:
            for s in (1, -1):
                if sum(1 for j in idx if side(j) == s and abs(z[j]) > 1) >= 4 \
                        and side(i) == s:
                    signals.append({"rule": 3, "label": labels[i], "index": i,
                                    "direction": "above" if s > 0 else "below",
                                    "z": z[i], "description": RULES[3]})
                    break

        # Rule 4 — 8 consecutive on one side of the centre line. This is the
        # rule that catches a small, permanent shift: none of the eight points
        # is remarkable on its own, and a threshold alert never fires.
        idx = window(i, 8)
        if idx:
            for s in (1, -1):
                if all(side(j) == s for j in idx):
                    signals.append({"rule": 4, "label": labels[i], "index": i,
                                    "direction": "above" if s > 0 else "below",
                                    "z": z[i], "description": RULES[4]})
                    break

    return signals
