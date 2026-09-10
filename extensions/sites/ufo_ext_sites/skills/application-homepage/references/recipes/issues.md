# issues

## Intent

A tracker's records a member owns and plans: a queue that groups by what it is waiting on, each
record started and approved.

## Roles

- Required — record list, primary text, identifier, status field, which is also the group key.
- Optional — secondary text, people, date, tags, a measure of what is open, the setting the queue
  runs under.

## Regions

```
Header → the app name, and up to 3 acts: Review in chat, prepare the next record, setup
Attention band → the records waiting on a person, at most 2 rows, one line each
Stat row → the records open, those waiting on a person, and those in flight, 3 across
Segmented → the status values, and one member for the whole list, active on the one in force
the record list → one Section per status value, its heading carrying its own count
└── RowLines → one row per record
    ├── the identifier in --font-mono, the primary text cut at its column, a Badge of the status
    ├── the date in --font-mono and the people as an AvatarStack
    └── the row's own controls: Approve and Skip, variant="row", with one line saying the record
        is created only after approval in chat
Empty → one line per Section naming what will appear there
what the app still needs → one sentence under the title, never a band above the rows
```

## Controls

- Segmented, a status → only that status's records draw, and every Stat answers what is drawn.
- Approve on a row → its Badge reads the new state, the row moves to the Section that state
  belongs to, and both counts move.
- Skip on a row → the row leaves the queue's Section and its count drops.

## Checklist

- Each Section heading reads its status and the count of its records.
- Approving a record moves it out of its Section, drops that count by one and lifts the other.
- A status in force draws selected, and every row drawn carries it.
- Approve and Skip each say the act completes in chat, and neither writes from the page.
- The identifier, the primary text and the date hold at the narrow lane.
