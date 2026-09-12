# meetings

## Intent

A dated record list with people, read one at a time.

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
} from "ufo/blocks";
```

## Roles

- Required — record list, group key, which is the date the records fall on, primary text.
- Optional — secondary text, date, people, one agent line over the first group.

## Regions

```
Page → Header, its acts at most three: review in chat, setup, the page-wide menu
BlockRoot wraps every band below the header

the agent line → Prose over the first group, one paragraph, capping its own measure
Segmented → the groups, and one member for the whole list, active on the one in force
the record list → ItemGroup, one ItemHeader per group, the current group first, carrying its Count
└── Item, accent primary in the current group, accent muted state past in the rest,
    selected on the opened record
    ├── ItemMedia variant="avatar" → Avatar of one person on the record
    ├── ItemContent → ItemTitle of the primary text, ItemDescription of the secondary text
    ├── ItemMeta of the date
    └── ItemActions → Count of the people, and ApplicationAction where the record has one act
```

## Controls

- A row → the opened record, `Item` selected → its secondary text opens to full and the rest cut
  to one line.
- An `ItemHeader` → its group's open state → the rows fold, the heading and its `Count` staying.
- `Segmented`, a group → only that group's records draw.
- `Button` reading `Review in chat` → the whole page hands off, drawn once in the `Header` acts.

## What the contract keeps out

The product supplies the page chrome and chat carries every member act, so the original's bar, its
read flag and its composer do not ship.

- `ActionBar`, `ActionBarTitle`, `ActionBarActions` and `IconButton` — the page's bar is `Header`,
  whose acts hold at most three.
- `Checkbox` — marking a record read is a write; it is staged by `ApplicationAction`, and a page
  that ticked it would change state no turn recorded.
- `Composer` — a member types into chat, never into a page.
- `Prompt` and `Prompts` — a chip that narrows records is `Segmented`; a chip that fills a line
  with something to ask is a chat turn.
- `ItemSection` — the group heading is `ItemHeader`, whose unread badge would count a flag the
  page cannot write.

## Checklist

- Each `ItemHeader` reads its group and the `Count` of the records under it.
- Opening a row draws it selected with its secondary text in full and closes the one before it.
- Folding the current group leaves the other heading and its rows drawn.
- The agent line draws once, above the first group, and never repeats the page name.
- At the narrow lane a row keeps its primary text, one line of secondary text and its people
  `Count`; the `Avatar` may go.
