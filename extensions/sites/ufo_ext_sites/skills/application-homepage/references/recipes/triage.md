# triage

## Intent

A queue the member reads one record at a time, each carrying a drafted reply.

## Imports

```tsx
import { ApplicationAction, Button, Header, Page, Segmented } from "ufo/kit";
import {
  Avatar,
  BlockRoot,
  Count,
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemHeader,
  ItemMedia,
  ItemMeta,
  ItemTitle,
  Prose,
  StatGrid,
  StatTile,
} from "ufo/blocks";
```

## Roles

- Required — record list, primary text, a group key the queue divides on, one drafted reply per
  record.
- Optional — people, secondary text, date.

## Regions

```
Page → Header, its acts at most three: review in chat, setup, the page-wide menu
BlockRoot wraps every band below the header

StatGrid → StatTile of the records still waiting, its label naming what it counts
Segmented → the groups, and one member for the whole list, active on the one in force
the record list → ItemGroup, one ItemHeader per group, the priority group first, carrying its Count
└── Item, accent primary in the priority group, selected on the opened record
    ├── ItemMedia variant="avatar" → Avatar of the person
    ├── ItemContent → ItemTitle of the primary text, ItemDescription of the secondary text
    ├── ItemMeta of the person and the date
    └── ItemActions → ApplicationAction staging the drafted reply
the opened record → Prose of the draft, under the row, read before it is staged
```

## Controls

- A row → the opened record, `Item` selected → its draft draws in `Prose` beneath it.
- An `ItemHeader` → its group's open state → the rows fold, the heading and its `Count` staying.
- `Segmented`, a group → only that group's records draw, and the `StatTile` counts what is drawn.
- A row's `ApplicationAction` → stages the reply and sends nothing until chat approves it.
- `Button` reading `Review in chat` → the whole page hands off, drawn once in the `Header` acts.

## What the contract keeps out

The product supplies the page chrome and chat carries every member act, so the original's bar, its
composer and its per-row sends do not ship.

- `ActionBar`, `ActionBarTitle`, `ActionBarActions` and `IconButton` — the page's bar is `Header`,
  whose acts hold at most three.
- `Composer` — a reply is read on the page and sent from chat; a field on the page would send a
  message no turn recorded, which is the one thing this recipe must not do.
- `Prompt` and `Prompts` — a chip that fills a composer has no composer to fill, and a chip that
  narrows records is `Segmented`.
- `Menu` and `MenuItem` — archiving or starring a record is a write, and it is a turn.
- `ItemSection` — the group heading is `ItemHeader`.

## Checklist

- The `StatTile` reads the records still in the queue, and each `ItemHeader` reads its own `Count`.
- Opening a row draws its draft beneath it and closes the one before it.
- Staging a reply creates nothing and sends nothing until chat approves it.
- Folding a group hides its rows and leaves the other heading drawn.
- At the narrow lane a row keeps its primary text, its person and its date.
