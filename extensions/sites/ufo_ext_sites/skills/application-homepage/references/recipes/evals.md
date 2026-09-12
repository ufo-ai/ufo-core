# evals

## Intent

Runs scored over a series, the newest run's rows sorted and opened.

## Imports

```tsx
import { Button, Header, Page, Segmented } from "ufo/kit";
import {
  BlockRoot,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  DataTable,
  Delta,
  Mark,
  Prose,
  ScoreDot,
  StatGrid,
  StatTile,
  useDataTable,
} from "ufo/blocks";
```

## Roles

- Required — point list with a label and a series value per point, the measures each point carries,
  row list for the newest point with a primary text and a measure.
- Optional — a delta per point, a status field per row, lesser columns per row, a detail per row,
  a warn flag per row.

## Regions

```
Page → Header, its acts at most three: review in chat, setup, the page-wide menu
BlockRoot wraps every band below the header

the selected point → StatGrid at four columns, collapsing as the container narrows
└── StatTile per measure: label, value, and Delta where the point carries one
the point list → a series over time
└── Card variant="plain" → CardContent → ChartContainer of a line, one dot per point,
    the selected one active, with ChartTooltip and ChartTooltipContent
the row list → the newest point's records, more than five fields over many rows
└── Card variant="plain" → CardHeader → CardTitle
    └── CardContent → useDataTable and DataTable: sortable on the primary text and the measure,
        lesser columns dropping at the narrow lane
        ├── Mark on the warn flag, then the primary text
        └── ScoreDot of the score, right-aligned
the opened row → Prose of its detail, under the row
```

## Controls

- A chart dot → the selected point → every `StatTile` and its `Delta` swap to that point, and the
  dot draws active.
- A `DataTable` heading → the sort key and its direction → the rows reorder; a second press
  reverses them.
- A row → its open state → its detail draws beneath it in `Prose`; a second press folds it.
- `Segmented`, a status value → only rows carrying it draw.
- `Button` reading `Review in chat` → the whole page hands off, drawn once in the `Header` acts.

## What the contract keeps out

The product supplies the page chrome and chat carries every member act, so the original's bar and
its search field do not ship.

- `ActionBar`, `ActionBarTitle`, `ActionBarActions` and `IconButton` — the page's bar is `Header`,
  whose acts hold at most three.
- `SearchField` and `DataTableToolbar` — one narrowing control per page, and it is `Segmented`.
- `Prompt` and `Prompts` — a chip that narrows rows is `Segmented`.
- `Table`, `TableHeader`, `TableBody`, `TableRow`, `TableCell` — a row list this wide is a
  `DataTable`, which carries the sorting the plain table would leave to the page.

## Checklist

- The tiles read the selected point, and the chart draws one dot per point with that one active.
- Picking a dot changes every tile and leaves the rows alone.
- Pressing a sortable heading reorders the rows and marks that column sorted; again reverses it.
- Opening a row draws its detail beneath it and closes it on a second press.
- At the narrow lane the `StatGrid` draws one column and the lesser columns are gone.
