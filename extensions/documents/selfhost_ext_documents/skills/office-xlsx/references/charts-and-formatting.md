# Charts & Advanced Formatting

## Charts

```python
from openpyxl.chart import BarChart, LineChart, PieChart, ScatterChart, Reference

# Basic bar chart
chart = BarChart()
chart.title = "Revenue by Region"
data = Reference(ws, min_col=2, min_row=1, max_row=6)
cats = Reference(ws, min_col=1, min_row=2, max_row=6)
chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)
ws.add_chart(chart, "A8")

# Line chart
chart = LineChart()
chart.title = "Monthly Trends"
chart.x_axis.title = "Month"
chart.y_axis.title = "Revenue ($)"
chart.width = 20
chart.height = 12
data = Reference(ws, min_col=2, max_col=3, min_row=1, max_row=12)
cats = Reference(ws, min_col=1, min_row=2, max_row=12)
chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)
ws.add_chart(chart, "E1")

# Pie chart
chart = PieChart()
chart.title = "Market Share"
data = Reference(ws, min_col=2, min_row=1, max_row=5)
cats = Reference(ws, min_col=1, min_row=2, max_row=5)
chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)
ws.add_chart(chart, "A8")
```

| Chart Type | Use When |
|------------|----------|
| `BarChart` | Comparing values across categories |
| `LineChart` | Time series, trends over time |
| `PieChart` | Part-to-whole (6 categories max) |
| `ScatterChart` | Correlation between two variables |

Place charts below tables with a 2-row gap, left-aligned with content. Charts must never overlap each other or tables.

## Conditional Formatting

```python
from openpyxl.formatting.rule import DataBarRule, ColorScaleRule, CellIsRule, FormulaRule, IconSetRule

# Data bars
rule = DataBarRule(start_type="min", end_type="max", color="4472C4")
ws.conditional_formatting.add("C5:C50", rule)

# Two-color scale (white to blue)
rule = ColorScaleRule(start_type="min", start_color="FFFFFF",
                      end_type="max", end_color="4472C4")
ws.conditional_formatting.add("D5:H20", rule)

# Three-color scale (red-yellow-green)
rule = ColorScaleRule(start_type="min", start_color="F8696B",
                      mid_type="percentile", mid_value=50, mid_color="FFEB84",
                      end_type="max", end_color="63BE7B")
ws.conditional_formatting.add("D5:H20", rule)

# Highlight cells above threshold
rule = CellIsRule(operator="greaterThan", formula=["100"],
                  fill=PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"))
ws.conditional_formatting.add("B2:B20", rule)

# Formula-based rule
rule = FormulaRule(formula=["$E2<0"],
                   fill=PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"))
ws.conditional_formatting.add("A2:E20", rule)

# Icon sets (traffic lights)
rule = IconSetRule(icon_style="3TrafficLights1", type="num", values=[0, 33, 67])
ws.conditional_formatting.add("F2:F20", rule)
```

Always use conditional formatting rules — never loop through cells setting static fills. Static fills don't update when values change.

## Excel Tables

```python
from openpyxl.worksheet.table import Table, TableStyleInfo

tab = Table(displayName="SalesTable", ref="A1:E20")
style = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False,
                       showLastColumn=False, showRowStripes=True)
tab.tableStyleInfo = style
ws.add_table(tab)
```

## Merged Cells

```python
ws.merge_cells("B2:H2")
ws["B2"] = "Report Title"
ws["B2"].font = Font(bold=True, size=14)
ws["B2"].alignment = Alignment(horizontal="center")
```

## Data Validation

```python
from openpyxl.worksheet.datavalidation import DataValidation

# Dropdown list
dv = DataValidation(type="list", formula1='"Open,Closed,Pending"')
dv.prompt = "Select a status"
dv.promptTitle = "Status"
ws.add_data_validation(dv)
dv.add("B2:B100")

# Number range
dv = DataValidation(type="whole", operator="between", formula1="1", formula2="100")
dv.error = "Enter 1-100"
dv.errorTitle = "Invalid"
ws.add_data_validation(dv)
dv.add("C2:C100")
```

## Embedded Images

```python
from openpyxl.drawing.image import Image

img = Image("logo.png")
img.width = int(3 * 72)   # 3 inches
img.height = int(1.5 * 72) # 1.5 inches
ws.add_image(img, "A1")
```
