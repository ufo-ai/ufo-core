# metrics

## Intent

Sets of measures with deltas, banded by section, over a window the member switches.

## Roles

- Required — section list with a name, measure list with a group key on the section, a label and a
  measure, window labels.
- Optional — a delta and its direction, a note saying what the figure was counted by, the measure's
  source, a prior set the window switches to, asks the app needs from members.

## Regions

```
Header → the app name, and up to 3 acts: Review in chat, prepare an ask, setup
Segmented → the window labels, active on the one in force
the section list → one Section per section, in list order, its heading naming it and the window
└── Stat row → one Stat per measure matched on the section key, 2 to 4 across
    ├── StatLabel of the label, StatMedia of the source's mark where one is carried
    ├── StatValue of the figure, StatDelta of the move where one is carried
    └── StatDescription of the rule the figure was counted by
what the app still needs → one line under the title where it is a sentence; a Section of rows
                           carrying Prepare <the ask> where the member acts on each
Empty → one line per Section naming what will appear there
```

A figure is a `Stat` and a period is a `Chart`: a single number drawn as a chart says less than the
number, and a series drawn as a Stat loses the shape. A Stat carries no border.

## Controls

- Segmented, a window → every figure, delta and note swaps to that window, and each Section
  heading reads the new window.
- DropdownMenuCheckboxItem per section → an unchecked section leaves the page.
- An ask row's Prepare control → its ApplicationAction stages the ask and says it completes in chat.

## Checklist

- Each Section reads its name, the window in force, and one Stat row at 2 to 4 across.
- Switching the window swaps every figure and delta on the page and moves the selection.
- Unchecking a section leaves one fewer Section drawn and the rest evenly divided.
- A measure with no figure states so; it does not draw an empty tile.
- Every Stat row collapses to one column at the narrow lane.
