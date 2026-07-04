# `xlsx_repl` workflow (multi-step REPL iteration)

> **Path placeholders.** Examples below use `INPUT_PATH` and `OUTPUT_PATH` variables. Set them in your first `xlsx_repl` call to the actual paths from the user's task. Variables persist across REPL calls, so later calls can reuse them.

## Workflow

A typical task is **2–3 calls** (read, write+save, verify). The Python below is the `code` field of each call.

```python
# Call 1: Open + explore + read
import openpyxl
INPUT_PATH = "<replace with the user's input file path>"
OUTPUT_PATH = "<replace with the desired output file path>"

wb = openpyxl.load_workbook(INPUT_PATH)
print("Sheets:", wb.sheetnames)
ws = wb["Sheet1"]
print(f"Dimensions: {ws.dimensions}")
# Print headers
headers = [cell.value for cell in ws[1]]
print("Headers:", headers)
# Read data
for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 20), values_only=False):
    print("\t".join(str(c.value) for c in row))
# Variables (wb, ws, INPUT_PATH, OUTPUT_PATH) persist — reuse in later calls
```

```python
# Call 2: Write + format + save + recalc
ws["C2"] = "=B2*1.1"
ws["C3"] = "=B3*1.1"
ws["C5"] = "=SUM(C2:C4)"

from openpyxl.styles import Font, numbers
for cell in ["C2", "C3", "C5"]:
    ws[cell].number_format = "$#,##0"

wb.save(OUTPUT_PATH)

# Recalculate formulas via LibreOffice
import subprocess, json, os
result = subprocess.run(
    ["python3", "scripts/recalc.py", os.path.abspath(OUTPUT_PATH)],
    capture_output=True, text=True, cwd="/home/user/workspace/skills/office/xlsx",
    env={**os.environ, "PYTHONPATH": "/home/user/workspace/skills/office/xlsx/scripts"}
)
print(result.stdout)

# Reload to get recalculated values
wb = openpyxl.load_workbook(OUTPUT_PATH)
ws = wb.active
```

**Anti-pattern — do NOT split into many calls:**
```python
# WRONG — 5 separate calls for what should be 2
wb = openpyxl.load_workbook(INPUT_PATH)   # call 1
print(wb.sheetnames)                       # call 2
ws["C2"] = "=B2*1.1"                      # call 3
wb.save(OUTPUT_PATH)                       # call 4
# recalc                                   # call 5
```

## Formula Recalculation

openpyxl does NOT evaluate formulas — it only stores formula strings. After saving, run the recalc script:

```python
import subprocess, json, os
result = subprocess.run(
    ["python3", "scripts/recalc.py", os.path.abspath(OUTPUT_PATH)],
    capture_output=True, text=True, cwd="/home/user/workspace/skills/office/xlsx",
    env={**os.environ, "PYTHONPATH": "/home/user/workspace/skills/office/xlsx/scripts"}
)
recalc_result = json.loads(result.stdout)
print(recalc_result)
# Reload workbook to pick up recalculated values
wb = openpyxl.load_workbook(OUTPUT_PATH)
```

The recalc script uses LibreOffice headless (pre-installed in sandbox) to evaluate all formulas and reports any errors (#REF!, #DIV/0!, etc.).

## Sheet Name Quoting

```python
ws = wb["My Sheet"]           # access by name
ws = wb.active                # access active sheet
```

## Bulk Writes

```python
# Efficient — write all cells then save once
for i in range(2, 102):
    ws[f"A{i}"] = i
    ws[f"B{i}"] = f"=A{i}*2"
wb.save(OUTPUT_PATH)
```

## Number Formatting (openpyxl)

```python
for row in range(2, 21):
    ws.cell(row=row, column=2).number_format = "$#,##0"
    ws.cell(row=row, column=3).number_format = "0.0%"
```

## Formulas Over Hardcodes (openpyxl)

```python
# WRONG — value dies when inputs change
ws["D5"] = ws["B5"].value - ws["C5"].value

# RIGHT — formula stays live
ws["D5"] = "=(B5-C5)/B5"
ws["D5"].number_format = "0.0%"

# WRONG — snapshot of a sum
total = sum(ws.cell(row=r, column=6).value or 0 for r in range(2, 20))
ws["F20"] = total

# RIGHT — Excel does the aggregation
ws["F20"] = "=SUM(F2:F19)"
ws["F20"].number_format = "#,##0"
```

## Styling

```python
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# Font
ws["A1"].font = Font(bold=True, size=14, color="0000FF")

# Header row
header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
header_font = Font(bold=True, color="FFFFFF")
for cell in ws[1]:
    cell.fill = header_fill
    cell.font = header_font
    cell.alignment = Alignment(horizontal="center")

# Borders
thin_border = Border(bottom=Side(style="thin", color="D9D9D9"))
for row in ws.iter_rows(min_row=1, max_row=10, min_col=1, max_col=5):
    for cell in row:
        cell.border = thin_border
```

## Layout (openpyxl)

```python
ws.column_dimensions["A"].width = 3
ws.column_dimensions["B"].width = 20
ws.column_dimensions["C"].width = 15
ws.row_dimensions[1].height = 30
```

## Navigation & Structure

```python
# Freeze panes (for tables with >10 rows)
ws.freeze_panes = "A2"

# Auto-filter (for tables with >20 rows)
ws.auto_filter.ref = "A1:E50"
```

## Common Pitfalls

### Floating-Point Comparisons
When comparing percentages or ratios, always use an epsilon tolerance to avoid rounding issues.
```python
# WRONG — fails due to floating-point: 110 >= 100 * 1.1 is False
if value >= base * 1.1:

# RIGHT — epsilon tolerance
if value >= base * 1.1 - 1e-9:
```

### Deleting Rows
```python
# Use openpyxl's delete_rows for structural operations
ws.delete_rows(100, 400)  # delete 400 rows starting at row 100
```

### openpyxl Naming

- Worksheet attrs are **long**: `ws.max_column`, `ws.min_column` (no `max_col`/`min_col`).
- `iter_rows()` / `iter_cols()` / `Reference()` kwargs are **short**: `min_col=`, `max_col=`.
- `ws.cell()` kwargs are **long**: `row=`, `column=` (no `col=`).

### Iterating Sheets

Use `wb.worksheets`, not `wb.sheetnames` — the latter mixes in `Chartsheet` names, and `Chartsheet` has no `dimensions` / `max_row` / `iter_rows` / `cell` (raises `AttributeError`).

```python
for ws in wb.worksheets:  # Worksheet objects only
    print(ws.title, ws.dimensions)
```

## Verification (include in the write+save call)

Before saving, in the **same call**:
1. Spot-check 1-2 formula cells
2. `wb.save(OUTPUT_PATH)`
3. Run recalc script
4. Check recalc output for errors — MUST be zero

Do NOT make separate calls for verification — include these checks in the same call as your writes.
