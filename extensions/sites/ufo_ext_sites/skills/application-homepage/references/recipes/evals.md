# evals

## Intent

A run history: a point picked on the series fills the measures, and the newest run's rows sort and
open.

## Roles

- Required — point list with a label and a series value, measures per point, row list with primary
  text and a measure, a detail per row.
- Optional — a delta per point, a status field saying which way a row moved, a warn flag, lesser
  columns (the shard, the model, what passed).

## Regions

```
Header → the app name, and up to 3 acts: Review in chat, prepare a rerun, setup
Stat row → the selected point's measures, 2 to 4 across
Chart → the series over the points, the selected one marked; Legend under it when it carries
        more than one tone
Segmented → the movement values, and one member for the whole list, active on the one in force
the row list → the newest point's records, more than 5 fields over many rows
└── DataTable
    ├── Lede of the primary text, with a Badge on the warn flag
    ├── TdFact per lesser column, compared straight down
    ├── Td of the measure at the row's end
    └── PressRow → Sheet holding the detail in full
Empty → the table's own note when the narrowing leaves nothing
```

## Controls

- Segmented, a movement → only the rows carrying it draw, and the table's note stands when none do.
- The DataTable's own heading → the rows reorder on that column; a second press reverses them.
- A row → its Sheet opens on the run, holding the detail the row could not.

## Checklist

- The Stat row reads the selected point, and the Chart marks that same point.
- The Chart draws the series over every point, and one figure alone is a Stat rather than a Chart.
- Pressing a heading reorders the rows and marks that column sorted.
- A movement in force draws selected, and every row drawn carries it.
- The primary text and the measure hold at the narrow lane; the lesser columns may go.
