# meetings

## Intent

Dated records with people and prep notes, grouped by day, opened one at a time and marked read.

## Roles

- Required — record list, group key (the day), primary text, status field (read or unread).
- Optional — secondary text, date and place, people, one agent line over the nearest group.

## Regions

```
Header → the app name, and up to 3 acts: Review in chat, mark the drawn records read, setup
Stat row → the records ahead and the unread among them, 2 across
Segmented → the days, active on the nearest one
the record list → one Section per group, the nearest group first, its heading carrying its
                  unread count
└── RowLines → one row per record
    ├── the primary text, cut at its column, and a Badge while the record is unread
    ├── the date and place in --font-mono, and the people as an AvatarStack
    └── PressRow → Sheet holding the secondary text and the agent line in full
Empty → one line per Section naming what will appear there
```

## Controls

- Segmented, a day → only that day's records draw, and the Stat row counts what is drawn.
- A row → its Sheet opens on the record, holding what the row's line could not.
- Mark read → every drawn record loses its Badge, and the unread Stat reads zero.

## Checklist

- Each Section heading reads its day and the count of its unread records.
- Picking a day leaves only that day's rows drawn and both Stats answering it.
- A row's Sheet holds the secondary text the row cut.
- Marking the drawn records read clears every Badge and leaves the unread Stat at zero.
- Every row keeps its primary text, its date and its people at the narrow lane.
