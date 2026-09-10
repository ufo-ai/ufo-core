# issues

## Intent

Bands of agent behaviour the member arms, over a queue of records the member starts and approves.

## Data

`data/issues.json` is one instance.

- Required — note list: `notes` (group key `band`, label `label`, secondary text `body`); armed
  flag per band: `armed`; record list: `queue` (primary text `title`, flags `approved`, `running`).
- Optional — chips: `prompts`; facts: `facts`; identifier: `number`; date: `date`; setting: `cadence`.

## Flowchart

```
Layout → fills the container, no width and no max width
└── the facts and note bodies → Prose, capping their own measure; the queue → ItemGroup rows

Header → ActionBar variant="header"
├── ActionBarTitle → the app name, and a menu of the queue's sort keys
└── ActionBarActions → IconButton for the setting and to search

the fact list → Prose with one small, the texts joined by a middle dot

Search → SearchField over the identifier and the primary text, drawn by the search action, onClear

the note list → notes group by the band key, each body a paragraph
└── Card variant="plain" per band
    ├── CardHeader → CardTitle of the band, CardAction of Tag on the armed flag and a
    │   CardButton variant="secondary" that flips it
    ├── CardContent → Prose of label over body, one pair per note
    └── CardFooter → Prompts of the band's chips, each disabled while the band is not armed

the queue → records that group by the approved flag
└── ItemSection per group, counting its records, folding its rows
    └── Item, state past on an approved record
        ├── ItemMedia variant="default" → the identifier, ItemContent → ItemTitle of the text
        └── ItemActions → ItemMeta of the date, Tag while running, Prompt that starts it, and
            CardButton variant="secondary" that approves it

Foot → Composer, filled by a band chip
```

## Interactions

- Band button → the band's armed flag → the Tag reads the new state and the band's chips enable or disable.
- Band chip, enabled → the Composer at the foot, filled with the chip's text → the field takes the draft and focus.
- Composer submit → the field clears → the draft goes and the composer closes.
- Queue row Prompt → the record's running flag → the row gains a Tag and the Prompt goes disabled.
- Queue row button → the record's approved flag → the row leaves the waiting group and draws under the approved one, both counts moving.
- ItemSection heading → the group's open state → its rows fold, the heading and count staying.
- Search action → the SearchField draws → typing narrows both queue groups; onClear restores them.
- ActionBarTitle menu → the queue sort key → the rows reorder inside both groups.
- Setting action → the setting in force → the armed band's header reads the value picked.

## Checklist

- Each band draws a Tag saying whether it is armed, and arming one enables its chips in place.
- Pressing an enabled chip draws its text in the composer at the foot; submitting clears the field.
- Starting a queue record draws a Tag on that row and leaves its Prompt disabled.
- Approving a record moves it out of the waiting group, drops that count by one and lifts the other.
- Folding a queue group hides its rows, and the title menu's sort key reorders both groups.
- Clearing the search field restores every queue record.
- Changing the setting redraws the armed band's header with the new value.
- At 349 a queue row keeps its identifier, title and date; at 1200 the note bodies stop at their
  own measure rather than running the container's width.
- Sample: eight notes over two bands, eight queue records, none approved.
