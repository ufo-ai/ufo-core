# tasks

## Intent

A record list whose ordered status the member advances, grouped by that status, edited in place.

## Data

`data/tasks.json` is one instance.

- Required — record list: `tasks`; primary text: `title`; status field and group key: `status`.
- Optional — tag: `project`; people: `assignees`; date: `due`.

## Flowchart

```
Layout → fills the container, no width and no max width
├── the record list → Table, scrolling sideways inside its own box
├── tag and people columns → TableHead and TableCell hideBelow, gone at the narrow width
└── status, primary text and date hold at every width

Header → ActionBar variant="header"
├── ActionBarTitle → the app name, and a menu of the sort keys
└── ActionBarActions → IconButton to add a record, to search, and for the list-wide menu

Prompts → up to three tag values and one for the whole list, wrap, active on the one in force

Search → SearchField over the primary text, drawn by the search action, with onClear

the record list → records group by the status field, and the groups fold
└── Table → TableSection per status value, in status order, counting the records under it
    └── TableRow, state done on the last status value, editing while its text is renamed
        ├── TableCell → StatusIcon on the status field, onClick
        ├── TableCell → the primary text, or an input while the row is editing
        ├── TableCell hideBelow → Tag of the tag, TableCell hideBelow → Count of the people
        └── TableCell align="right" → TableMeta of the date, then Menu of the row verbs

Foot → Composer, opened by the add action, appending a record to the first group
```

## Interactions

- StatusIcon → the status advances one value, wrapping at the last → the row redraws under the group of the new value, both counts moving.
- Row Menu, move up or down → the record's order in its group → the row swaps with its neighbour.
- Row Menu, move to a group → the status field → the row draws under the named group.
- Row Menu, rename → the row's editing state → the primary text becomes an input; Enter keeps it, Escape restores it.
- Row Menu, delete → the record leaves the list → the row goes and its group's count drops.
- Add action → the Composer at the foot → submitting appends a record at the top of the first group.
- Search action → the SearchField draws → typing narrows every group; onClear restores the list.
- Prompt chip → the tag filter, chip active → only records carrying that tag draw.
- ActionBarTitle menu → the sort key → rows reorder inside every group.
- List-wide menu, a MenuCheckboxItem per status → which groups draw → an unchecked group goes.
- TableSection heading → the group's open state → its rows fold, heading and count staying.

## Checklist

- Each group heading reads its status name and the count of the records under it.
- Clicking a row's status circle draws it under the next group, wrapping at the last, and moves both counts.
- Move up on the second row of a group draws it first; move to a group redraws the row there.
- A renaming row draws an input in place of its primary text and keeps its other cells drawn.
- Deleting a row drops its group's count by one and leaves the other groups untouched.
- Submitting the composer draws a new row at the top of the first group.
- The tag chip in force draws filled, and every row drawn carries that tag.
- Clearing the search field restores every record and every group heading.
- The sort key from the title menu reorders the rows inside every group.
- Unchecking a status leaves one fewer group heading drawn.
- Folding a group hides its rows and leaves the other headings drawn.
- At 349 the tag and people columns are gone and the table does not scroll; at 1200 all five draw.
- Sample: the four groups open at 4, 3, 3 and 2 records.
