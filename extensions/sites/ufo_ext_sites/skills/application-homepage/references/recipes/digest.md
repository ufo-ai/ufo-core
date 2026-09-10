# digest

## Intent

One period's records and measures: a period is picked, its rows and figures follow, and the
questions it raised clear.

## Roles

- Required — period list with a label and its own measures, record list with a period key, primary
  text and secondary text, question list with primary text and a resolved flag.
- Optional — the person who leads a record, a measure per record, a detail per record, the date a
  question has waited, the setting the digest runs under.

## Regions

```
Header → the app name, and up to 3 acts: Review in chat, clear the questions, setup
Attention band → the questions still open, at most 2 rows, one line each, each carrying
                 Prepare <the answer>
Stat row → the selected period's measures — the records drawn, their counts summed, 2 to 4 across
Segmented → the period labels, active on the one in force
the record list → the selected period's records, one Section
└── RowLines → one row per record
    ├── the primary text cut at its column, the secondary text on the same line
    ├── the lead as an AvatarStack and the measure in --font-mono
    └── PressRow → Sheet holding the detail in full
the question list → one Section, its heading carrying the count still open
└── RowLines → the primary text, a Badge once resolved, the wait in --font-mono
Empty → one line per Section naming what will appear there
```

## Controls

- Segmented, a period → every figure and every row swaps to that period, and the record Section's
  heading names it.
- A row → its Sheet opens on the record, holding the detail the row cut.
- A question's Prepare control → its ApplicationAction stages the answer; the row's Badge reads
  resolved and the open count drops.

## Checklist

- The Stat row reads the selected period and the record Section names it.
- Picking another period swaps every row and every figure on the page.
- Resolving a question drops the open count by one and leaves the row drawn with its Badge.
- Clearing them all leaves the question Section's own empty line drawn.
- The Stat row collapses to one column at the narrow lane and no row runs past the pane.
