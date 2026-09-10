# meetings

## Intent

A dated record list of appointments with people and prep notes, grouped by day, opened one at a
time and marked read.

## Data

`data/meetings.json` is one instance.

- Required — record list: `meetings`; group key: `day`; primary text: `title`; read flag: `read`.
- Optional — secondary text: `notes`; date: `time`, `place`; people: `attendees`; agent line:
  `summary`.

## Flowchart

```
Layout → fills the container, no width and no max width
├── the agent line → Prose, capping its own measure and centring in a wider container
└── the record list → ItemGroup rows, one column at every width, text cut rather than wrapped

Header → ActionBar variant="header"
├── ActionBarTitle → the app name, and a menu of the groups to draw
└── ActionBarActions → IconButton to add a record, to search, and to mark the drawn records read

Prompts → chips under the header, wrap: one fills the agent line, the rest narrow the list, active
          on the one in force

Search → SearchField over the primary text, drawn by the search action, with onClear

the agent line → Prose over the first group, drawn only once a chip has filled it

the record list → records group by the group key, the current group first
└── ItemSection per group, badged with its unread count, folding its rows
    └── Item, accent primary in the current group, accent muted state past in the rest,
        selected on the opened record
        ├── ItemMedia variant="checkbox" → Checkbox on the read flag
        ├── ItemContent → ItemTitle of the primary text, editable while a record is being added,
        │   ItemDescription of the secondary text, ItemMeta of the date
        └── ItemActions → Count of the people
```

## Interactions

- A row → the opened record, Item selected → the row draws selected and its secondary text opens to full; the rest cut to one line.
- The row Checkbox → the read flag → the box fills, the primary text goes muted, the group badge drops one.
- ItemSection heading → the group's open state → its rows fold, the heading and badge staying.
- Add action → an editing row at the top of the current group → ItemTitle editable takes the text; Enter appends the record, Escape drops the row.
- Mark-read action → the read flag on every drawn record → every box fills and both badges read zero.
- Search action → the SearchField draws → typing narrows both groups; onClear restores the list.
- Fill chip, active → the agent line → Prose draws over the first group; pressing it again clears it.
- Narrowing chip, active → the list filter → only the records it names draw, the badges counting what is drawn.
- ActionBarTitle menu, a MenuCheckboxItem per group → which groups draw → an unchecked group goes.

## Checklist

- Each group heading reads its name and the count of its unread records.
- Opening a row draws it selected with its secondary text in full, and closes the one before it.
- Ticking a row's box mutes that row's title and drops its group badge by one.
- Marking the drawn records read leaves both group badges at zero and every box filled.
- Folding the current group leaves the other heading and its rows drawn.
- The add action draws an empty editable title at the top of the current group; Escape removes it.
- Clearing the search field restores every record.
- The fill chip draws the agent line between the header and the first group, only while active.
- A narrowing chip in force draws filled, and every row drawn answers it.
- Unchecking a group in the title menu leaves one heading drawn.
- At 349 a row keeps its primary text, one line of secondary text and its people count; at 1200 it runs wider on the same one column.
- Sample: the groups open at 3 unread of 5 and 2 unread of 7.
