# metrics

## Intent

Measures with deltas over a window the member switches.

## Imports

```tsx
import { Button, Header, Page, Segmented } from "ufo/kit";
import {
  BlockRoot,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Delta,
  ProgressStat,
  Prose,
  StatGrid,
  StatTile,
} from "ufo/blocks";
```

## Roles

- Required — section list, measure list with a group key onto those sections, a label and a value
  per measure, the window labels.
- Optional — a note per section, a delta and its direction per measure, a target per measure.

## Regions

```
Page → Header, its acts at most three: review in chat, setup, the page-wide menu
BlockRoot wraps every band below the header

Segmented → one member per window label, active on the window in force
the section list → one band each, in list order
└── Card variant="plain" → CardHeader
    ├── CardTitle of the section name
    └── CardDescription of the note and the window label
    └── CardContent → StatGrid at two to four columns, collapsing as the container narrows
        └── StatTile per measure: label, value, and Delta where the measure carries one
            └── a measure with a target is a ProgressStat instead
the section notes → Prose, capping their own measure; every band runs the full width
```

Four columns is the practical ceiling of the content box; a fifth measure belongs in a second row.

## Controls

- `Segmented`, a window → every value and `Delta` on the page swaps to that window, and each
  band's `CardDescription` reads the new label.
- `Button` reading `Review in chat` → the whole page hands off, drawn once in the `Header` acts.

## What the contract keeps out

The product supplies the page chrome and chat carries every member act, so the original's bar, its
per-band toggles and its composer do not ship.

- `ActionBar`, `ActionBarTitle`, `ActionBarActions` and `IconButton` — the page's bar is `Header`,
  whose acts hold at most three.
- `Composer` — a member asks in chat, never in a field on the page, so the original's ask list is
  a set of measures here and the asking is a turn.
- `CardButton` and `CardAction` — turning a section on or off is a write; a page that held it would
  keep state no turn recorded.
- `Menu`, `MenuCheckboxItem` — the same, one level down.
- `Prompt` and `Prompts` — a chip that narrows is `Segmented`.

## Checklist

- Each band reads its section name, its note with the window label, and one `StatGrid`.
- Switching the window swaps every value and every `Delta`, and moves the active member.
- A measure with a target draws a `ProgressStat`; one without draws a `StatTile`.
- At the narrow lane every `StatGrid` draws one column; at the full width each draws its own count.
- No band draws a control that writes.
