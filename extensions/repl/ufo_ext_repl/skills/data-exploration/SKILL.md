---
name: data-exploration
description: Load when exploring a dataset — profiling structure, summary statistics, distributions, and missing values before deeper analysis.
---
# Data Exploration

Profile datasets to understand shape, quality, and patterns before analysis.

## Column Classification

Categorize each column as one of:

- **Identifier**: Unique keys, foreign keys, entity IDs
- **Dimension**: Categorical attributes for grouping/filtering (status, type, region)
- **Metric**: Quantitative values for measurement (revenue, count, duration)
- **Temporal**: Dates and timestamps (created_at, event_date)
- **Text**: Free-form text fields
- **Boolean**: True/false flags
- **Structural**: JSON, arrays, nested structures

## Column-Level Profiling

| Column type | Key stats                                                                         |
| ----------- | --------------------------------------------------------------------------------- |
| **All**     | Null count/rate, distinct count, cardinality ratio, top/bottom 5 values           |
| **Numeric** | min, max, mean, median, stddev, p1/p5/p25/p75/p95/p99, zero count, negative count |
| **String**  | min/max/avg length, empty string count, pattern consistency, whitespace           |
| **Date**    | min/max date, future dates, gaps in series, distribution by period                |
| **Boolean** | true/false/null counts, true rate                                                 |

## Quality Assessment

**Completeness scoring:**

- **Complete** (>99% non-null) — Green
- **Mostly complete** (95-99%) — Yellow, investigate the nulls
- **Incomplete** (80-95%) — Orange, understand whether it matters
- **Sparse** (<80%) — Red, may need imputation

**Consistency red flags:**

- Value format drift ("USA", "US", "United States")
- Numbers stored as strings, dates in mixed formats
- Broken referential integrity (orphaned foreign keys)
- Business rule violations (negative quantities, end < start, pct > 100)
- Cross-column contradictions (status = "completed" but completed_at is null)

**Accuracy red flags:**

- Placeholder values (0, -1, 999999, "N/A", "TBD")
- Suspiciously high frequency of a single default value
- Stale updated_at in an active system
- Round number bias (suggests estimation, not measurement)

## Relationship Discovery

- **Foreign key candidates**: ID columns that might link to other tables
- **Hierarchies**: Natural drill-down paths (country > state > city)
- **Correlations**: Numeric columns that move together (|r| > 0.7 warrants investigation)
- **Derived columns**: Columns computed from others
- **Redundant columns**: Identical or near-identical information

## Gotchas

- **Cardinality ratio is your best friend** — A column with 5 distinct values in 1M rows is a dimension; one with 999K distinct values is likely an identifier. Misclassifying this derails the entire analysis.
- **Nulls mean different things** — A null email might mean "not collected" while a null revenue means "zero." Never assume null semantics without checking with the data owner.
- **Profiling on a sample can miss rare values** — A column looks clean in 10K rows but has garbage in the long tail. Profile the full dataset for quality checks, sample only for distribution shape.
- **Schema != reality** — A column typed as INTEGER can still contain sentinel values (-1, 0, 9999) that aren't real data. Always check value distributions, not just types.
- **First row is not representative** — `LIMIT 10` shows you the storage order, which is often insertion order. The oldest rows may look nothing like current data.
