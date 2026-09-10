# triage

## Intent

A queue the member clears one record at a time: each record carries a drafted act the member
approves or skips.

## Roles

- Required — record list, primary text, status field (waiting or handled), the drafted act per
  record.
- Optional — people, secondary text, date, a priority flag the queue groups by.

## Regions

```
Header → the app name, and up to 3 acts: Review in chat, prepare the top record, setup
Stat row → the records waiting and those handled, 2 across, each StatDescription naming what
           it counts
Segmented → the groups, and one member for the whole list, active on the one in force
the record list → one Section per group, the priority group first, its heading carrying its count
└── RowLines → one row per record
    ├── the person as an AvatarStack, the primary text cut at its column, a Badge of the state
    ├── the secondary text on the same line and the date in --font-mono
    ├── the row's own controls: Approve and Skip, variant="row"
    └── PressRow → Sheet holding the drafted act in full, with one line saying the act is sent
        only after approval in chat
Empty → one line per Section naming what will appear there
```

## Controls

- Segmented, a group → only that group's records draw, and both Stats answer what is drawn.
- Approve on a row → its Badge reads handled, the row leaves the waiting Section, and the waiting
  Stat drops by one as the handled Stat lifts.
- Skip on a row → the row leaves the queue and the waiting Stat drops by one.
- A row → its Sheet opens on the record, holding the draft the row could not.

## Checklist

- Each Stat states what it counts, and each Section heading reads its own count.
- Approving a record moves it between the Sections and moves both Stats.
- Skipping a record drops the waiting count and leaves the other rows in place.
- A row's Sheet holds the drafted act, and the row itself stays one line.
- A group in force draws selected, and the whole-list member restores every record.
