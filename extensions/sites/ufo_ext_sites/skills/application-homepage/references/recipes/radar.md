# radar

## Intent

Subjects watched for movement: long-form entries grouped by what shipped them, narrowed by tag and
marked read.

## Roles

- Required — group list (the release, the week, the source), record list with a group key, primary
  text, secondary text, status field (read or unread), tags.
- Optional — a note per group, a date per group, a measure of what moved.

## Regions

```
Header → the app name, and up to 3 acts: Review in chat, mark the drawn records read, setup
Attention band → the records that moved and need a person, at most 2 rows, one line each
Stat row → the groups watched, the entries unread, and what moved in the newest group, 3 across
Segmented → the tags, and one member for the whole list, active on the one in force
the record list → one Section per group, the newest group first, its heading carrying its date
                  and its unread count
└── RowLines → one row per record
    ├── the primary text cut at its column, a Badge while unread, a Badge per tag
    ├── the group's note as one line under the heading, never a band of its own
    └── PressRow → Sheet holding the secondary text in full
Empty → one line per Section naming what will appear there
```

## Controls

- Segmented, a tag → only the records carrying it draw, every Section heading counting what is
  drawn; the whole-list member restores them.
- A row → its Sheet opens on the entry, holding the body the row cut to one line.
- Mark read → every drawn record loses its Badge and each Section's unread count reads zero.

## Checklist

- Each Section heading reads its group, its date and the count of its unread records.
- A tag in force draws selected, and every row drawn carries it.
- The whole-list member restores every record and every Section heading.
- A row's Sheet holds the body, and the row itself stays one line.
- Marking the drawn records read leaves every unread count at zero.
