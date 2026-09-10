# tasks

## Intent

A record list whose ordered state a member advances, grouped by that state.

## Roles

- Required — record list, primary text, status field, which is also the group key.
- Optional — tags, people, date, an owner per record.

## Regions

```
Header → the app name, and up to 3 acts: Review in chat, prepare the next state change, setup
Stat row → one Stat per state, or the count in each of the first 3 states
Segmented → the states, and one member for the whole list, active on the one in force
the record list → one Section per state, in state order, its heading carrying its own count
└── RowLines when a row shows 5 fields or fewer, DataTable when it shows more
    ├── a Badge of the state, the primary text cut at its column, a Badge per tag
    ├── the date in --font-mono and the people as an AvatarStack
    └── the row's own control: Prepare <the next state>, variant="row"
Empty → one line per Section naming what will appear there
```

A board of states is a `Segmented` that picks one, never a row of columns: four columns is the
practical ceiling of the content box and a fifth lands outside the pane.

## Controls

- Segmented, a state → only that state's records draw, and every Stat answers what is drawn.
- DropdownMenuRadioGroup of the order — oldest first, soonest due → the rows reorder inside
  every Section.
- A row's Prepare control → its ApplicationAction stages the state change and says the act
  completes in chat.

## Checklist

- Each Section heading reads its state and the count of the records under it.
- Picking a state leaves one Section drawn and the Stats counting it.
- Changing the order reorders the rows inside every Section drawn.
- A row's Prepare control stages one write and creates nothing until chat approves it.
- The state, the primary text and the date hold at the narrow lane; tags and people may go.
