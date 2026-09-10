# radar

## Intent

Long-form entries grouped by the release they shipped in, filtered by tag, folded by group and
marked read.

## Data

`data/radar.json` is one instance.

- Required — group list: `releases` (name `version`); record list: `entries` (group key `version`,
  primary text `title`, secondary text `body`, read flag `read`); filter list: `tags`.
- Optional — group note: `summary`; group date: `day`; tags: `tags`.

## Flowchart

```
Layout → fills the container, no width and no max width
├── the group notes and secondary text → Prose, capping their own measure
└── the record list → ItemGroup rows, one column at every width

Header → ActionBar variant="header"
├── ActionBarTitle → the app name, and a menu of the read records' visibility
└── ActionBarActions → IconButton to mark the drawn records read, to search, and for the list-wide
    menu

Prompts → a Prompt chip per filter and one for the whole list, wrap, active on the one in force

Search → SearchField over the primary and secondary text, drawn by the search action, with onClear

the record list → records group by the group key, the newest group first
└── ItemSection per group, badged with its unread count, folding its rows
    ├── the group note → Prose under the heading
    └── Item variant="outline", state past on a read record
        ├── ItemMedia variant="checkbox" → Checkbox on the read flag
        ├── ItemContent → ItemTitle of the primary text, ItemDescription of the secondary text
        └── ItemFooter → Tag per record tag, each one pressable
```

## Interactions

- The row Checkbox → the read flag → the box fills, the row goes muted, the group badge drops one.
- ItemSection heading → the group's open state → the note and rows under it fold, the badge staying.
- A record Tag → the tag filter, its chip going active → only records carrying that tag draw.
- Prompt chip → the tag filter, chip active → only records carrying it draw; the whole-list chip clears it.
- Mark-read action → the read flag on every drawn record → every box fills and each badge reads zero.
- Search action → the SearchField draws → typing narrows the groups; onClear restores the list.
- ActionBarTitle menu, a MenuCheckboxItem for read records → whether read records draw → the read rows go and the badges hold.
- List-wide menu, a MenuCheckboxItem per group → which groups draw → an unchecked group leaves the page.

## Checklist

- Each group heading reads its name, its date and the count of its unread records.
- Ticking a row's box mutes that row and drops its group badge by one.
- Marking the drawn records read leaves every badge at zero.
- Folding a group hides its note and its rows and leaves the other headings drawn.
- Pressing a tag on a row fills the matching chip and leaves only the records carrying that tag.
- A filter chip in force draws filled; the whole-list chip clears it and restores every record.
- Clearing the search field restores every record and every group heading.
- Hiding read records leaves only unread rows, and each group badge still counts them.
- Unchecking a group leaves one fewer heading drawn.
- At 349 the secondary text runs the full width of the row; at 1200 Prose holds it to its own
  measure rather than stretching it.
- Sample: five groups, ten records, two per group.
