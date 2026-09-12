# issues

## Intent

A tracker's records a member owns, grouped by where each one stands.

## Imports

```tsx
import { ApplicationAction, Button, Header, Page, Segmented } from "ufo/kit";
import {
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
  Tag,
} from "ufo/blocks";
```

## Roles

- Required — record list, primary text, a status field the records group by.
- Optional — an identifier per record, a date, tags, one line of facts over the list.

## Regions

```
Page → Header, its acts at most three: review in chat, setup, the page-wide menu
BlockRoot wraps every band below the header

the fact line → Prose, one line, the texts joined by a middle dot
StatGrid → one StatTile per status the member watches
Segmented → the statuses, and one member for the whole list, active on the one in force
the record list → ItemGroup, one ItemHeader per status, carrying its own Count
└── Item, state past on a closed record
    ├── ItemMedia variant="default" → the identifier
    ├── ItemContent → ItemTitle of the primary text, ItemDescription of the secondary text
    ├── ItemMeta of the date, Tag per tag
    └── ItemActions → ApplicationAction staging the one act the record carries
```

## Controls

- `Segmented`, a status → only that status's records draw, and every `Count` and `StatTile`
  answers what is drawn.
- An `ItemHeader` → its group's open state → the rows fold, the heading and its `Count` staying.
- A row's `ApplicationAction` → stages one write and creates nothing until chat approves it.
- `Button` reading `Review in chat` → the whole page hands off, drawn once in the `Header` acts.

## What the contract keeps out

The product supplies the page chrome and chat carries every member act, so the original's armed
bands, its composer and its start button do not ship.

- `ActionBar`, `ActionBarTitle`, `ActionBarActions` and `IconButton` — the page's bar is `Header`,
  whose acts hold at most three.
- `Card`, `CardHeader`, `CardTitle`, `CardAction`, `CardContent`, `CardFooter` and `CardButton` —
  the original's bands each held a switch the member armed, which is a write; the notes that
  survive are `Prose` and the arming is a chat turn.
- `Composer` — a member types into chat, never into a page.
- `Prompt` and `Prompts` — a chip that starts work is a chat turn, and a chip that narrows records
  is `Segmented`.
- `ItemSection` — the group heading is `ItemHeader`.

## Checklist

- Each `ItemHeader` reads its status and the `Count` of the records under it.
- Picking a status leaves one group drawn and every `StatTile` counting it.
- Folding a group hides its rows and leaves the other headings drawn.
- A row's staged act creates nothing until chat approves it.
- At the narrow lane a row keeps its identifier, primary text and date; tags may go.
