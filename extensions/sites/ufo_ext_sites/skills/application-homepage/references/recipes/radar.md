# radar

## Intent

Entries grouped by what shipped them, read one at a time and narrowed by tag.

## Imports

```tsx
import { Button, Header, Page, Segmented } from "ufo/kit";
import {
  BlockRoot,
  Count,
  Item,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemGroup,
  ItemHeader,
  ItemTitle,
  Prose,
  Tag,
} from "ufo/blocks";
```

## Roles

- Required — group list, record list with a group key, primary text, secondary text, a tag set the
  records are narrowed by.
- Optional — a note per group, a date per group.

## Regions

```
Page → Header, its acts at most three: review in chat, setup, the page-wide menu
BlockRoot wraps every band below the header

Segmented → the tag set, and one member for the whole list, active on the one in force
the record list → ItemGroup, one ItemHeader per group, the newest group first, carrying its Count
└── the group note → Prose under the heading, capping its own measure
└── Item variant="outline", selected on the opened record
    ├── ItemContent → ItemTitle of the primary text
    ├── ItemDescription of the secondary text, in full while the record is open
    └── ItemFooter → Tag per record tag
```

Long-form secondary text goes in `Prose`, which holds its own measure rather than running the
container's width.

## Controls

- A row → the opened record, `Item` selected → its secondary text opens to full and the rest cut.
- An `ItemHeader` → its group's open state → the note and rows under it fold, the `Count` staying.
- `Segmented`, a tag → only records carrying that tag draw, and every `Count` answers what is drawn.
- `Button` reading `Review in chat` → the whole page hands off, drawn once in the `Header` acts.

## What the contract keeps out

The product supplies the page chrome and chat carries every member act, so the original's bar, its
read flag and its pressable tags do not ship.

- `ActionBar`, `ActionBarTitle`, `ActionBarActions` and `IconButton` — the page's bar is `Header`,
  whose acts hold at most three.
- `Checkbox` — marking an entry read is a write, and this page carries none.
- `SearchField` — one narrowing control per page, and it is `Segmented`.
- `Prompt` and `Prompts` — a chip that narrows records is `Segmented`.
- `ItemSection` — the group heading is `ItemHeader`, whose unread badge would count a flag the page
  cannot write.
- `ItemMedia` — with no read flag to draw there is nothing for a row's leading slot to hold.

## Checklist

- Each `ItemHeader` reads its group, its date and the `Count` of the records under it.
- Opening a row draws its secondary text in full and closes the one before it.
- Folding a group hides its note and its rows and leaves the other headings drawn.
- The tag in force draws active, and every record drawn carries it.
- At the narrow lane the secondary text runs the row's full width; wider, `Prose` holds it to its
  own measure rather than stretching it.
