---
name: data-validation
description: Load when data or a computed result must be checked against rules that should already hold. E.g. validate rows against a schema; check uniqueness, ranges, and referential integrity across files; QA analysis output before delivery.
---
# Data Validation

Pre-delivery QA checks, common pitfalls, and result sanity checking.

## Pre-Delivery Checklist

**Data quality:**

- Source tables are correct for this question; freshness noted ("as of" date)
- No unexpected gaps in time series or missing segments
- Null rates checked; nulls handled explicitly (excluded, imputed, or flagged)
- No double-counting from bad joins or duplicate source records

**Calculations:**

- GROUP BY includes all non-aggregated columns; aggregation grain matches the question
- Rate/percentage denominators are correct and non-zero
- Time period comparisons use equal-length complete periods
- JOIN types are appropriate; row counts verified before and after joins
- Metric definitions match stakeholder expectations

**Reasonableness:**

- Numbers in plausible range; cross-referenced against dashboards or prior reports
- No unexplained jumps/drops in time series
- Edge cases checked (empty segments, zero-activity periods, new entities)

## Common Pitfalls

**Join explosion:** A many-to-many join silently multiplies rows. Always check `COUNT(*)` before and after. Use `COUNT(DISTINCT entity_id)` when counting through joins.

**Incomplete period comparison:** Comparing a partial month to a full month. Filter to complete periods or compare same number of days.

**Denominator shifting:** Rate improves because the definition of "eligible" changed, not because performance improved. Use consistent definitions across compared periods.

**Average of averages:** Averaging pre-aggregated averages ignores group size differences. Aggregate from raw data.

**Timezone mismatch:** Event timestamps in UTC vs. user-facing dates in local time cause off-by-one-day errors. Standardize to a single timezone before analysis.

**Selection bias in segmentation:** "Power users have higher revenue" is circular if you defined power users BY revenue. Define segments on pre-treatment characteristics.

## Sanity Checks

| Metric type | Check                                   |
| ----------- | --------------------------------------- |
| User counts | Match known MAU/DAU figures?            |
| Revenue     | Right order of magnitude vs. known ARR? |
| Rates       | Between 0-100%? Match dashboard?        |
| Growth      | Is 50%+ MoM realistic or a data issue?  |
| Percentages | Segment percentages sum to ~100%?       |

**Red flags:** >50% period-over-period change without cause, exact round numbers, rates at exactly 0% or 100%, results that perfectly confirm the hypothesis, identical values across segments.

**Cross-validation:** Calculate the same metric two ways. Spot-check individual records. Reverse-engineer totals (per-user \* users ~= total).

## Gotchas

- **LEFT JOIN hides missing data** — A LEFT JOIN silently fills unmatched rows with NULLs. If you then filter on a right-table column in WHERE (not ON), it becomes an INNER JOIN. Put right-table filters in the ON clause.
- **Rounding before aggregation compounds error** — Round display values at the end, not intermediate calculations. Rounding percentages per-row then summing can produce totals that don't add to 100%.
- **"No rows returned" is not "zero"** — A query that returns no rows for a segment is different from a segment with zero activity. Missing rows won't show up in aggregations; use COALESCE or a complete date/segment spine.
- **Dashboard numbers include filters you forgot** — When cross-referencing against a dashboard, check its hidden filters (date range, excluded segments, bot filtering). Mismatch is usually a filter difference, not a bug.
