# digest

## Intent

One period's records and measures, the period picked and its questions read.

## Imports

```tsx
import { Button, Header, Page, Segmented } from "ufo/kit";
import {
  BlockRoot,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemGroup,
  ItemMeta,
  ItemTitle,
  Prose,
  StatGrid,
  StatTile,
} from "ufo/blocks";
```

## Roles

- Required — period list with a label per period, record list with a period key, primary text and
  secondary text, the measures each period carries.
- Optional — a question list, a detail per record, people, a measure per record, a source.

## Regions

```
Page → Header, its acts at most three: review in chat, setup, the page-wide menu
BlockRoot wraps every band below the header

Segmented → one member per period label, active on the period in force
the selected period → StatGrid at four columns, collapsing as the container narrows
└── StatTile per measure: the records drawn, their counts summed, and the period's own
the record list → the selected period's records
└── Card variant="outline" → CardHeader → CardTitle naming the period
    └── CardContent → ItemGroup
        └── Item variant="outline", selected while open
            ├── ItemContent → ItemTitle of the primary text, ItemDescription of the secondary text
            ├── ItemActions → ItemMeta of the people and the measure
            └── ItemFooter → the detail, only while the record is open
the question list → Card variant="plain" → CardTitle
└── CardContent → ItemGroup → Item, ItemTitle of the question, ItemMeta of the source and the wait
    └── empty → one line of Prose naming what will appear here
```

## Controls

- `Segmented`, a period → the record card, its `CardTitle` and every `StatTile` swap to it.
- A record row → its open state, `Item` selected → the detail draws in its `ItemFooter`; a second
  press folds it.
- `Button` reading `Review in chat` → the whole page hands off, drawn once in the `Header` acts.

## What the contract keeps out

The product supplies the page chrome and chat carries every member act, so the original's bar, its
question checkboxes and its clear-all button do not ship.

- `ActionBar`, `ActionBarTitle`, `ActionBarActions` and `IconButton` — the page's bar is `Header`,
  whose acts hold at most three.
- `Checkbox` — resolving a question is a write; the page reads the questions and chat clears them.
- `CardAction` and `CardButton` — clearing every question at once is one write, and it is a turn.
- `SearchField` — one narrowing control per page, and it is `Segmented`.
- `Prompt` and `Prompts` — a chip that narrows records is `Segmented`.
- `Tag` — a state a record is in reads in its `ItemMeta` here; the card needs no badge of its own.

## Checklist

- Every `StatTile` reads the selected period, and the record card's `CardTitle` names it.
- Picking another period swaps every row and every measure.
- Opening a record draws its detail beneath its secondary text and closes it on a second press.
- With no question left to read, one line names what appears there.
- At the narrow lane the `StatGrid` draws one column; at the full width it draws four.
