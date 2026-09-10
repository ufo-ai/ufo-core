# metrics

## Intent

Sets of measures with deltas, banded by section, over a window the member switches.

## Data

`data/metrics.json` is one instance.

- Required — section list: `sections` (name `name`, on flag `on`, columns `columns`); measure list:
  `stats` (group key `section`, label `label`, measure `value`); window labels: `windows`.
- Optional — section note: `scope`; measure source: `source`; delta: `delta`, `direction`; note:
  `counted`; prior set: `priorValue`, `priorDelta`, `priorDirection`; asks: `asks` (flag `asked`).

## Flowchart

```
Layout → fills the container, no width and no max width
├── the measures → StatGrid at the section's column count, collapsing as the container narrows
└── the section notes and ask bodies → Prose, capping their own measure; every band is full width

Header → ActionBar variant="header"
├── ActionBarTitle → the app name, and a menu of the sections to run
└── ActionBarActions → IconButton to ask, to pick the sources, and for the list-wide menu

Prompts → one Prompt chip per window label, active on the window in force

the section list → one band each, in list order
└── Card variant="plain" → CardHeader
    ├── CardTitle of the name, CardDescription of the note and the window label
    └── CardAction → Menu of the band verbs while it is on, CardButton variant="secondary" to
        turn it on while it is off

the measure list → measures matched on the section key, several side by side
└── StatGrid → Stat per measure: source and label, value, delta, note

the ask list → records with secondary text and one act
└── Card variant="plain" → CardHeader of CardTitle
    └── ItemGroup → Item, state past once asked
        ├── ItemContent → ItemTitle of the primary text, ItemDescription of the secondary text
        └── ItemActions → CardButton variant="secondary", disabled once asked

Foot → Composer, filled by an ask and by the ask action
```

## Interactions

- Window chip → the window in force, chip active → every value, delta and note swaps to that set, and each band's note reads the new window label.
- Band Menu, turn off → the section's on flag → its StatGrid goes and the turn-on button takes its place.
- Turn-on button → the section's on flag → its measures draw and the button goes.
- ActionBarTitle menu, a MenuCheckboxItem per section → the same on flag → the band swaps between its measures and its button.
- Source action → the picked sources → measures whose source is unpicked leave their grid and the grid closes up.
- Ask row button → the Composer at the foot, filled with that row's title → the field holds the draft and takes focus.
- Composer submit → the row's asked flag → the row goes past, its button disabled, and the field clears.
- Ask action → an empty Composer at the foot → the field opens with no draft.
- List-wide menu, a MenuCheckboxItem for empty measures → whether measures with no value draw → those tiles go.

## Checklist

- Each band reads its name, its note with the window label, and one grid at the section's columns.
- Switching the window swaps every value and delta on the page and moves the filled chip.
- Turning a section off from its band menu or the title menu replaces its grid with one secondary button; turning it on restores the grid.
- Unpicking a source drops every measure from that source and leaves the grid's remaining columns even.
- Pressing an ask draws its title in the composer at the foot; submitting mutes the row and disables its button.
- The ask action opens the composer empty.
- Hiding empty measures drops the tiles that read no value.
- At 349 every StatGrid draws one column; at 1200 each draws its own column count.
- Sample: four bands, twelve measures, four asks.
