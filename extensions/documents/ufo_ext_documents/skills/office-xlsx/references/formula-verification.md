# Formula Verification

## Error Recovery

| Error | Cause | Fix |
|-------|-------|-----|
| `ValueError: Sheet 'X' not found` | Wrong sheet name | Check `wb.sheetnames` |
| `#DIV/0!` | Division by zero | Check denominator cells |
| `#REF!` | Invalid reference | Check if cells/sheets were deleted |
| `#NAME?` | Unknown function | Check function name spelling |
| `#VALUE!` | Wrong argument type | Check cell types |

## Formula Verification Checklist

### Essential Verification
- [ ] Confirm Excel column mapping (column 64 = BL, not BK)
- [ ] Remember Excel rows are 1-indexed
- [ ] After save, run recalc script and check for errors

### Common Pitfalls
- [ ] Division by zero in formulas
- [ ] Wrong cell references (off-by-one errors)
- [ ] Cross-sheet references with wrong sheet names
- [ ] Circular references

## Recalculation

openpyxl does NOT evaluate formulas. After saving, recalculate via LibreOffice:

```python
import subprocess, json, os
result = subprocess.run(
    ["python3", "scripts/recalc.py", os.path.abspath("output.xlsx")],
    capture_output=True, text=True, cwd="/home/user/workspace/skills/office/xlsx",
    env={**os.environ, "PYTHONPATH": "/home/user/workspace/skills/office/xlsx/scripts"}
)
recalc_result = json.loads(result.stdout)
if recalc_result.get("total_errors", 0) > 0:
    print("ERRORS:", recalc_result["error_summary"])
```

Then reload the workbook to get computed values:
```python
wb = openpyxl.load_workbook("output.xlsx")
```
