# evals

## Intent

A run history: a point picked on the series fills the measures, and the newest run's rows sort,
narrow and open.

## Data

`data/evals.json` is one instance.

- Required — point list: `nights` (point label `night`, series `rate`, measures `label`, `counts`,
  `suites`, `shards`); row list: `suites` (primary text `suite`, measure `rate`, detail `detail`).
- Optional — point delta: `delta`, `direction`, and measures `excluded`, `shardRows`, `previous`;
  row warn flag: `warn`; lesser columns: `shard`, `model`, `passed`; status field: `movement`.

## Flowchart

```
Layout → fills the container, no width and no max width
├── the measures → StatGrid columns={4}, collapsing as the container narrows
├── the series → ChartContainer, taking whatever width the container gives it
└── the row list → Table, scrolling sideways in its own box; lesser columns take hideBelow

Header → ActionBar variant="header"
├── ActionBarTitle → the app name, and a menu of the recent points
└── ActionBarActions → IconButton to search, and for the list-wide menu

Prompts → a Prompt chip per movement value and one for the whole list, wrap, active on the one held

the selected point → its measures, several side by side
└── StatGrid → StatTile per measure: label, value, delta where one is carried, note

the point list → a series over time, one series
└── Card variant="plain" → CardContent → ChartContainer of LineChart, one dot per point, the
    selected one active, with ChartTooltip and ChartTooltipContent

the row list → the newest point's records, several fields over many rows
└── Card variant="plain" → CardHeader of CardTitle and CardAction of the SearchField, with onClear
    └── CardContent → Table density="dense"
        ├── TableHeader → TableHead sortable on the primary text, the count and the measure
        └── TableBody → TableRow, selected while its detail is open
            ├── TableCell → Mark on the warn flag then the primary text, TableCell hideBelow → the
            │   lesser columns, TableCell align="right" → ScoreDot of the score
            └── a following TableRow → TableCell colSpan → Prose of the detail, while it is open
```

## Interactions

- A chart dot → the selected point → every StatTile swaps to that point's measures and the dot draws active.
- ActionBarTitle menu, one item per recent point → the same selected point → the tiles and the active dot follow.
- TableHead → the sort key and its direction → the arrow moves and the rows reorder; a second press reverses them.
- A row → its open state, TableRow selected → a detail row opens beneath it; pressing it again folds it.
- Prompt chip → the movement filter, chip active → only rows carrying that movement draw.
- Search action → the SearchField draws → typing narrows the rows; onClear restores them.
- List-wide menu, a MenuCheckboxItem for warned rows → whether unwarned rows draw → the table loses them.

## Checklist

- The tiles read the selected point, and the chart draws one dot per point with that one active.
- Picking a dot changes every tile and leaves the table alone.
- Picking a point from the title menu moves the active dot to the same point.
- Pressing a sortable heading reorders the rows and marks that column sorted; pressing it again reverses.
- Opening a row draws a detail line under it spanning every column, and closes it on a second press.
- A movement chip in force draws filled, and every row drawn carries that movement.
- Clearing the search field restores every row.
- Hiding unwarned rows leaves only the rows carrying the mark.
- At 349 the StatGrid draws one column and the lesser columns are gone; at 1200 four tiles draw.
- Sample: fourteen points and fourteen rows.
