# tasks

## Intent

A record list whose ordered state a member advances, grouped by that state.

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
  ItemMeta,
  ItemTitle,
  Prose,
  StatGrid,
  StatTile,
  StatusIcon,
  Tag,
} from "ufo/blocks";
```

## Roles

- Required — record list, primary text, status field, which is also the group key.
- Optional — tags, people, date, an owner per record.

## Regions

```
Page → Header, its acts at most three: review in chat, setup, the page-wide menu
BlockRoot wraps every band below the header, so the records draw on their own surface

StatGrid → one StatTile per state, or the count in each of the first three states
Segmented → the states, and one member for the whole list, active on the one in force
the record list → ItemGroup, one ItemHeader per state, in state order, carrying its own Count
└── Item per record, state past on the last state value
    ├── StatusIcon on the status field, ItemContent → ItemTitle of the primary text
    ├── ItemDescription of the secondary text, cut at one line
    ├── ItemMeta of the date, Tag per tag
    └── ItemActions → ApplicationAction staging the next state change
an empty group draws one line of Prose naming what will appear there
```

A board of states is a `Segmented` that picks one, never a row of columns: four columns is the
practical ceiling of the content box and a fifth lands outside the pane.

## Controls

- `Segmented`, a state → only that state's records draw, and every `StatTile` answers what is drawn.
- An `ItemHeader` → its group's open state → the rows fold, the heading and its `Count` staying.
- A row's `StatusIcon` → nothing durable on its own; it stages the change through
  `ApplicationAction`, which chat approves.
- `Button` reading `Review in chat` → the whole page hands off, drawn once in the `Header` acts.

## What the contract keeps out

The product supplies the page chrome and chat carries every member act, so the original's own bar,
its composer and its per-row writes do not ship.

- `ActionBar`, `ActionBarTitle`, `ActionBarActions` and `IconButton` — the page's bar is `Header`,
  whose acts hold at most three.
- `Composer` — a member types into chat, never into a page.
- `CardButton` — a write is staged by `ApplicationAction` and created only once chat approves it.
- `Prompt` and `Prompts` — a chip that narrows records is `Segmented`; a chip that asks for
  something is a chat turn.
- `Checkbox` — ticking a row would write it; the row's state moves through `ApplicationAction`.

## Checklist

- Each `ItemHeader` reads its state and the `Count` of the records under it.
- Picking a state leaves one group drawn and every `StatTile` counting it.
- Folding a group hides its rows and leaves the other headings drawn.
- A row's staged change creates nothing until chat approves it.
- The state, the primary text and the date hold at the narrow lane; tags and people may go.
