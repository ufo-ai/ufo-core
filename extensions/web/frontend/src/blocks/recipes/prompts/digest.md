# digest

## Intent

One period's record summary: a period is picked, its measures and records follow, questions clear.

## Data

`data/digest.json` is one instance.

- Required — period list: `days` (label `date`, measures `quiet`, `words`); record list: `channels`
  (period key `date`, primary text `channel`, secondary `summary`); questions: `blockers` (primary
  text `question`, flag `resolved`).
- Optional — filters: `filters`; people: `lead`; measure: `messages`; detail: `detail`; source:
  `channel`; date: `waiting`; setting: `hour`.

## Flowchart

```
Layout → fills the container, no width and no max width; the measures → StatGrid columns={4},
         collapsing as it narrows; every list → ItemGroup rows, one column at every width

Header → ActionBar variant="header"
├── ActionBarTitle → the app name, and a menu of the periods
└── ActionBarActions → IconButton for the setting, for the list-wide menu, and to search, which
    draws a SearchField over the record name and summary, with onClear

the selected period → StatGrid of Stat: the records drawn, their counts summed, and its own two

the record list → the selected period's records
└── Card variant="outline" → CardHeader of CardTitle from the period, CardAction of Tag
    └── CardContent → the filter list as Prompts of Prompt, active on the one in force
        └── ItemGroup → Item variant="outline", selected while open
            ├── ItemContent → ItemTitle of the name, ItemDescription of the summary,
            │   ItemActions → ItemMeta of the lead and count
            └── ItemFooter → the detail, only while the row is open

the period list → Card variant="plain" → CardTitle → ItemGroup → Item size="sm", selected on the
chosen period, ItemTitle of the label, ItemActions of ItemMeta of its measures

the question list → Card variant="plain", CardTitle, CardAction of CardButton clearing them all
└── CardContent → ItemGroup → Item variant="outline", state past once resolved
    ├── ItemMedia variant="checkbox" → Checkbox on the flag, ItemContent → ItemTitle of the text
    │   and ItemMeta of the source and the wait
    └── empty → Prose with one line naming what will appear here
```

## Interactions

- Filter chip → the filter in force, chip active → only the records it names draw; the whole-list chip clears it.
- A record row → its open state, Item selected → the detail draws under the secondary text; pressing it again folds it.
- A period row, or an item of the ActionBarTitle menu → the selected period, Item selected → the record card, its heading and every measure swap to it.
- Question Checkbox → the resolved flag → the row goes past and the questions count drops.
- Clear-all button → the resolved flag on every question → every row goes past and the empty line draws.
- Search action → the SearchField draws → typing narrows the records; onClear restores the period's.
- Setting action → the setting in force → the header reads the value picked.
- List-wide menu, a MenuCheckboxItem for resolved questions → whether they draw → the past rows go.

## Checklist

- The measures read the selected period, the record card's heading names it, and picking another
  period from either the list or the title menu swaps every row and every measure.
- The filter chip in force draws filled, and every record drawn answers it.
- Opening a record draws its detail beneath the secondary text and closes it on a second press.
- Ticking a question mutes that row and drops the questions count by one.
- Clearing them all leaves one line naming what appears there; onClear restores the period's rows.
- Changing the setting redraws the header, and hiding resolved questions leaves only open ones.
- At 349 the StatGrid draws one column; at 1200 it draws four.
- Sample: four periods, fourteen records over them, four questions, none resolved.
