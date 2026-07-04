# Financial Model Conventions

## Color Coding Standards

Unless otherwise stated by the user or existing template:

- **Blue text (0,0,255)**: Hardcoded inputs, numbers users will change for scenarios
- **Black text (0,0,0)**: ALL formulas and calculations
- **Green text (0,128,0)**: Links pulling from other worksheets within same workbook
- **Red text (255,0,0)**: External links to other files
- **Yellow background (255,255,0)**: Key assumptions needing attention

```python
from openpyxl.styles import Font, PatternFill
ws["B5"].font = Font(color="0000FF")  # blue = input
ws["C5"].font = Font(color="000000")  # black = formula
ws["D5"].font = Font(color="008000")  # green = cross-sheet link
```

## Number Formatting Standards

- **Years**: Format as text strings (e.g., "2024" not "2,024")
- **Currency**: Use $#,##0 format; ALWAYS specify units in headers ("Revenue ($mm)")
- **Zeros**: Use number formatting to make all zeros "-" (e.g., `"$#,##0;($#,##0);-"`)
- **Percentages**: Default to 0.0% format (one decimal)
- **Multiples**: Format as 0.0x for valuation multiples (EV/EBITDA, P/E)
- **Negative numbers**: Use parentheses (123) not minus -123

```python
for row in range(2, 21):
    ws.cell(row=row, column=2).number_format = "$#,##0"
    ws.cell(row=row, column=3).number_format = "0.0%"
    ws.cell(row=row, column=4).number_format = "#,##0"
```

## Formula Construction Rules

### Assumptions Placement
- Place ALL assumptions (growth rates, margins, multiples, etc.) in separate assumption cells
- Use cell references instead of hardcoded values in formulas
- Example: Use `=B5*(1+$B$6)` instead of `=B5*1.05`

### Formula Error Prevention
- Verify all cell references are correct
- Check for off-by-one errors in ranges
- Ensure consistent formulas across all projection periods
- Test with edge cases (zero values, negative numbers)

### Documentation Requirements for Hardcodes
- Comment or in cells beside (if end of table)
- Format: "Source: [System/Document], [Date], [Specific Reference], [URL if applicable]"
