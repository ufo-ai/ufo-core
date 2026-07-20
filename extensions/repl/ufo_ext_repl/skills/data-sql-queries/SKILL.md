---
name: data-sql-queries
description: Load before writing or tuning SQL — .sql files, schema DDL, or queries against tables.
---
# SQL Queries

Dialect-specific reference and common analytical patterns for data warehouse SQL.

## Dialect Quick Reference

### Date Arithmetic

| Operation | PostgreSQL                      | Snowflake                   | BigQuery                              | Redshift                    | Databricks                 |
| --------- | ------------------------------- | --------------------------- | ------------------------------------- | --------------------------- | -------------------------- |
| Add days  | `+ INTERVAL '7 days'`           | `DATEADD(day, 7, col)`      | `DATE_ADD(col, INTERVAL 7 DAY)`       | `DATEADD(day, 7, col)`      | `DATE_ADD(col, 7)`         |
| Date diff | `end - start`                   | `DATEDIFF(day, start, end)` | `DATE_DIFF(end, start, DAY)`          | `DATEDIFF(day, start, end)` | `DATEDIFF(end, start)`     |
| Truncate  | `DATE_TRUNC('month', col)`      | `DATE_TRUNC('month', col)`  | `DATE_TRUNC(col, MONTH)`              | `DATE_TRUNC('month', col)`  | `DATE_TRUNC('MONTH', col)` |
| Extract   | `EXTRACT(DOW FROM col)` (0=Sun) | `DAYOFWEEK(col)`            | `EXTRACT(DAYOFWEEK FROM col)` (1=Sun) | `DATE_PART('dow', col)`     | `DAYOFWEEK(col)`           |

### Case-Insensitive Matching

| Dialect                         | Pattern                       |
| ------------------------------- | ----------------------------- |
| PostgreSQL, Snowflake, Redshift | `ILIKE '%pattern%'`           |
| BigQuery                        | `LOWER(col) LIKE '%pattern%'` |

### JSON / Semi-Structured Access

| Dialect    | Pattern                                                                         |
| ---------- | ------------------------------------------------------------------------------- |
| PostgreSQL | `data->>'key'` (text), `data#>>'{path,to,key}'` (nested)                        |
| Snowflake  | `col:key::STRING` (VARIANT dot notation), `LATERAL FLATTEN(input => array_col)` |
| BigQuery   | `UNNEST(array_col)`, `struct_col.field_name`                                    |

### Performance Tips by Dialect

- **PostgreSQL**: `EXPLAIN ANALYZE`, `EXISTS` over `IN` for subqueries, partial indexes
- **Snowflake**: Clustering keys (not indexes), filter on cluster columns, `RESULT_SCAN(LAST_QUERY_ID())` to reuse results
- **BigQuery**: Always filter on partition column, `APPROX_COUNT_DISTINCT()` for cardinality, avoid `SELECT *` (billed per byte)
- **Redshift**: DISTKEY for collocated joins, SORTKEY for filters, watch for DS_BCAST/DS_DIST in plan
- **Databricks**: `OPTIMIZE` + `ZORDER`, `CACHE TABLE`, partition by low-cardinality date columns

## Window Functions

```sql
ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at DESC)
SUM(revenue) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as running_total
AVG(revenue) OVER (ORDER BY date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) as moving_avg_7d
LAG(value, 1) OVER (PARTITION BY entity ORDER BY date) as prev_value
revenue / SUM(revenue) OVER (PARTITION BY category) as pct_of_category
```

## Common Analytical Patterns

**Deduplication:**

```sql
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY entity_id ORDER BY updated_at DESC) as rn
    FROM source_table
)
SELECT * FROM ranked WHERE rn = 1;
```

**Cohort retention:**

```sql
SELECT
    c.cohort_month,
    COUNT(DISTINCT c.user_id) as cohort_size,
    COUNT(DISTINCT CASE WHEN a.month = c.cohort_month + INTERVAL '1 month' THEN a.user_id END) as month_1
FROM cohorts c LEFT JOIN activity a ON c.user_id = a.user_id
GROUP BY c.cohort_month;
```

**Funnel conversion:**

```sql
SELECT
    SUM(CASE WHEN event = 'view' THEN 1 END) as viewed,
    SUM(CASE WHEN event = 'signup' THEN 1 END) as signed_up,
    ROUND(100.0 * SUM(CASE WHEN event = 'signup' THEN 1 END)
        / NULLIF(SUM(CASE WHEN event = 'view' THEN 1 END), 0), 1) as conv_pct
FROM events WHERE event_date >= CURRENT_DATE - INTERVAL '30 days';
```

## Gotchas

- **BigQuery DATE_TRUNC argument order is reversed** — It's `DATE_TRUNC(col, MONTH)` not `DATE_TRUNC('month', col)`. This silently produces wrong results in some cases instead of erroring.
- **LAST_VALUE needs explicit frame** — Without `ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING`, `LAST_VALUE` uses the default frame that ends at the current row, giving you the current row's value back.
- **COUNT(DISTINCT) in window functions** — Not supported in most dialects. Use a subquery or CTE to pre-aggregate instead.
- **Integer division truncates silently** — `5 / 2 = 2` in most dialects. Cast to float first: `5.0 / 2` or `CAST(x AS FLOAT) / y`.
- **NULL poisons comparisons** — `WHERE col != 'x'` excludes NULLs. Use `WHERE col IS DISTINCT FROM 'x'` (Postgres) or `WHERE COALESCE(col, '') != 'x'` if you want NULLs included.
