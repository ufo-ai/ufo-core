---
name: data-statistical-analysis
description: Load when applying statistical methods to data — hypothesis tests, regression, correlation, significance, or confidence intervals.
---
# Statistical Analysis

Statistical methods, test selection, and guidance on when to be cautious about claims.

## Central Tendency Selection

| Situation                                  | Use           | Why                                   |
| ------------------------------------------ | ------------- | ------------------------------------- |
| Symmetric, no outliers                     | Mean          | Most efficient estimator              |
| Skewed distribution                        | Median        | Robust to outliers                    |
| Categorical / ordinal                      | Mode          | Only option for non-numeric           |
| Skewed business metric (revenue, duration) | Median + mean | Gap between them shows skew magnitude |

## Hypothesis Test Selection

| Scenario                                 | Test                   |
| ---------------------------------------- | ---------------------- |
| Two group means, normal data             | Independent t-test     |
| Two group proportions (conversion rates) | z-test for proportions |
| Before/after on same entities            | Paired t-test          |
| 3+ group means                           | ANOVA                  |
| Two groups, non-normal data              | Mann-Whitney U         |
| Two categorical variables                | Chi-squared            |

**Practical vs. statistical significance:** A difference can be statistically significant but business-irrelevant (common with large samples). Always report effect size, confidence interval, and business-dollar impact alongside the p-value.

**Sample size rule of thumb:** At least 30 events per group for proportions. Detecting a 1% conversion change may need thousands per group.

## Outlier Detection Methods

**Z-score** (normal data): Flag |z| > 3. Fast but assumes normality.

**IQR** (robust): Outliers below Q1 - 1.5*IQR or above Q3 + 1.5*IQR. Works on skewed data.

**Percentile** (simplest): Flag below p1 or above p99.

Do NOT auto-remove outliers. Investigate first: data error (fix/remove), genuine extreme (use robust stats), or distinct population (segment separately). Always document what was excluded and why.

## Trend Analysis

**Moving averages:** 7-day smooths weekly noise; 28-day smooths monthly patterns.

**Period comparisons:** WoW, MoM, YoY. For seasonal businesses, YoY is the gold standard.

**Growth rates:**

- Simple: `(current - previous) / previous`
- CAGR: `(ending / beginning) ^ (1/years) - 1`
- Log growth: `ln(current / previous)` — better for volatile series

## Statistical Traps

- **Correlation != causation**: Always consider reverse causation, confounders, and coincidence.
- **Multiple comparisons**: Testing 20 metrics at p=0.05 yields ~1 false positive. Apply Bonferroni or report total tests run.
- **Simpson's paradox**: Aggregate trend can reverse within every segment due to mix shift.
- **Survivorship bias**: You only see entities that survived to be in the dataset. Ask who is missing.
- **Ecological fallacy**: Group-level trends do not apply to individuals.
- **False precision**: "Churn will be 4.73%" implies unwarranted certainty. Prefer ranges: "4-6%."

## Gotchas

- **Mean of means is wrong** — Averaging pre-computed averages ignores group size. Always aggregate from raw data: `SUM(total) / SUM(count)`, not `AVG(avg)`.
- **Standard deviation requires context** — A stddev of 10 means nothing without the mean. Use coefficient of variation (stddev/mean) to compare variability across different-scale metrics.
- **Small-sample p-values are unreliable** — With n=50 per group, a "significant" result has wide confidence intervals. Report the interval, not just p < 0.05.
- **Seasonality masquerades as trend** — A Q4 spike looks like growth if you only have 6 months of data. Always get at least 13 months before claiming a trend.
- **Percentiles shift meaning at different scales** — p99 latency on 1K requests/day is noisy; on 1M requests/day it is stable. Sample size determines how seriously to take extreme percentiles.
