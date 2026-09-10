# Recipes

A recipe directs an agent to build one app from blocks. The agent reads this file, the recipe, its
sample JSON, and the block API tables in `src/blocks/docs/*Page.tsx`. It writes one file,
`src/blocks/recipes/out/<name>.tsx`, with a default export `function App({ data }: { data: Data })`
where `Data` is the JSON shape. Imports come only from `react`, `@tabler/icons-react`, `recharts` (chart
primitives inside a `ChartContainer` only) and `@/blocks/*`. No `className`, no Tailwind, no inline `style`.

The app is interactive. It holds only what the member changes in `useState` (ticks, edits, the filter
in force, folded groups, order), keyed by record `id`, and derives everything else from `data` on every
render, because the host may swap `data` under a mounted app. Every control has a
handler that changes that state: a task checks off, a row moves between groups, a filter narrows a
list, a section folds, a reply chip fills the composer. A control with no effect does not ship. The
blocks carry the visual states (`checked`, `selected`, `active`, `open`, `disabled`, `past`,
`dragging`, `editing`); the app decides when each applies.

The app fills its container. It declares no width, no max width and no fixed column count; the
container decides the width and the app reflows: grids collapse from four columns to two to one,
tables scroll sideways inside their own box, rows truncate text before they wrap, and a lane and a
page are the same app at two container widths.

## A recipe is a class of app

A recipe describes every app of its kind, not the sample beside it. Tasks covers personal chores, a
recruiting pipeline and a release plan; meetings covers any dated list with people; metrics covers
any set of measures with deltas. The sample JSON is one instance. A recipe therefore speaks in roles
and maps roles to fields only in its Data section:

| Role | Meaning |
|---|---|
| record list | the array the app is about |
| primary text | what names a record |
| secondary text | one line that explains it |
| status field | a small enum a record moves through |
| group key | the field records are grouped by, often the status field |
| date | when a record happens or happened |
| people | who is on a record, as a count, initials or avatars |
| measure | a number with a label |
| delta | how a measure moved |
| series | numbers over time |
| tags | short labels on a record |
| actions | what the member can do to a record |

A flowchart or interaction line never names a sample value, field, count, icon or copy string. It
names a block and a role, one line each.

## Conventions

- A recipe writes `icon={IconName}` as shorthand; the build passes `<IconName size={16} stroke={1.5} />`
  (`Count` takes 14).
- A number shown by `Stat` or `Delta` is passed as a string, formatted by the build.
- Records key on their `id`.

## Layout flowchart

```
How does the app use its container width?
├── Always → fill it; the container sets the width and the app reflows at 349, 640 and 1200
├── Reading copy? → Prose caps its own measure at 640 and centers when the container is wider
├── Several numbers? → StatGrid, which collapses its columns as the container narrows
└── A table wider than the container? → Table scrolls sideways inside its own box

Does the app have a name and actions?
└── Always → ActionBar variant="header" with ActionBarTitle (icon, name, menu) and ActionBarActions of IconButtons

Does the agent suggest things a member can ask for?
├── Yes → Prompts row directly under the header: 2 to 4 suggestion chips, or one chip per filter value with wrap
└── No → nothing under the header

Does the member type into the app?
├── Yes → Composer at the foot
└── No → no foot, or one primary CardButton at the foot when a single act closes the flow
```

## Interaction flowchart

Walk it once per control the app shows.

```
What does the control change?
├── A record's state (done, read, approved) → the row's StatusIcon or Checkbox toggles it; the row moves to the group that state belongs to
├── A record's place (order, group, priority) → a Menu on the row's actions offers Move up, Move down, Move to <group>
├── A record's text → an editing state on the row swaps the title for an input; Enter saves, Escape cancels
├── Which records show (filter, search, sort) → Prompt active, SearchField value, or TableHead sorted drive a derived list
├── Whether a group shows its rows → ItemHeader or TableSection open state
├── What the member sends → Composer value; a reply chip fills the composer, submit appends to the list
└── Nothing → the control does not ship
```

## Section flowchart

Walk it once per top-level key of the JSON.

```
Is the value a list of records?
├── Yes
│   ├── More than 5 fields shown per record, or more than 10 rows? → Table
│   │   ├── Member sorts, filters or pages it? → useDataTable + DataTable + DataTableToolbar
│   │   └── Default → Table with TableHeader, TableBody, TableRow, TableCell
│   ├── Records group by a date or a status? → ItemGroup with an ItemHeader per group
│   │   └── Groups fold? → TableSection inside a Table instead
│   └── Default → ItemGroup of Item rows
│       ├── Row has a picture or a person? → ItemMedia variant="avatar" or "image"
│       ├── Row has a state? → ItemMedia variant="status" holding StatusIcon
│       ├── Row is upcoming versus past? → Item accent="primary" versus accent="muted" state="past"
│       └── Row carries a count, a tag or a date? → Tag, Count, ItemMeta in ItemActions
└── No
    ├── One number?
    │   ├── With a target? → ProgressStat
    │   ├── With a delta? → Stat (page) or StatTile (inside a Card or StatGrid)
    │   └── Several side by side? → StatGrid of StatTile, columns 2 to 4
    ├── A series over time?
    │   ├── Fits in one row beside a label? → Sparkline inside StatRow
    │   ├── One series? → ChartContainer with AreaChart or LineChart
    │   └── Categories? → ChartContainer with BarChart
    ├── A paragraph or markdown? → Prose, inside a Card when it stands apart from the rest
    ├── Code or a log? → CodeBlock variant="plain" wrap inside Prose
    └── A message someone wrote? → Card variant="muted" inline align="end" for the member, plain Prose for the agent
```

## Copy flowchart

```
Is the text a heading?
├── Section over a list or chart → CardTitle or an ItemHeader label
└── Page name → only in the ActionBarTitle, never repeated below

Is the text explaining a number?
└── Yes → the Stat sub line, one sentence, no period

Is the text a state the member cannot act on?
└── Yes → it does not ship

Is the list empty?
└── Yes → TableEmpty or one Prose line naming what will appear and what fills it
```

## Checklist

A build passes when every line holds.

- Header first: one ActionBar with the app name and 2 to 4 IconButtons.
- At most one primary action per lane or page; none is fine.
- Every control in the recipe's Interactions list changes state when used, and the change is visible.
- Renders at container widths 349, 640 and 1200 with no horizontal overflow outside a Table's own box.
- Every section maps to a leaf of the section flowchart, named in a comment-free way by the block it uses.
- Renders with the sample JSON and with `{}` for every key emptied.
- No `className`, no `style`, no import outside `react`, `@tabler/icons-react`, `recharts`, `@/blocks/*`.
- Copy is spartan: no exclamation, no greeting, standard capitalization.
