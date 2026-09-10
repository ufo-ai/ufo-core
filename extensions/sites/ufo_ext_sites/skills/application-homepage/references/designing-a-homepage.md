# Designing an application homepage

A homepage fills the pane a member opens it in. It is drawn and read at 1440 x 900, in light and
dark, against the wireframe you drew and the facts the objective named. A page that lays out
cleanly but lost a fact is not done. You open the page yourself and see both, so these rules are
how you know what you are looking at.

## Region order

Walk this tree once per region, then sort. The first scan of a page is the reader deciding what to
do next, so what needs a person leads and what merely describes follows.

```
Does the region hold something that needs a decision or is overdue?
├── Yes → first band
└── No
    ├── Does it hold the primary workflow's rows (the thing the reader filters, sorts, picks)?
    │   └── Yes → second band
    ├── Does it summarise counts or totals of those rows?
    │   └── Yes → above the rows, as one Stat row — never a band of its own
    ├── Is it what the app is for, or what it still needs from the workspace?
    │   └── Yes → one line under the title, never a band above the rows
    └── Reference the reader consults after acting (setup, status, recent activity)?
        └── Yes → last band, below the fold is fine
```

Draw 2 to 6 regions. Fewer than 2 is a list, not a screen; more than 6 cannot fit a fold.

## The fold budget

The wireframe is 1440 px wide and the fold is y=900. Every required fact and the primary workflow
sit above it.

**The content box is 1392 px, not 1440.** `Page` holds a 24 px gutter each side. Everything you
lay out divides 1392; nothing you write sets its own width. Draw the wireframe's bands inside
x=24..1416 so the drawing and the page agree.

| Across | Each is | With `gap-2xl` between |
| --- | --- | --- |
| 2 | 696 px | 688 px |
| 3 | 464 px | 453 px |
| 4 | 348 px | 336 px |
| 5 | 278 px | 266 px |

Five across leaves each column 266 px, where a title line truncates to about 31 characters. Four is
the practical ceiling for cards and board columns; a fifth belongs in a second row or a menu. A
board with more columns than fit is a `Segmented` that picks one, never a row that runs off the
right edge — a recorded build shipped a 1530 px document into this box and its last column, its
primary act and two row controls landed outside the pane.

| Band | Height | What it holds |
| --- | --- | --- |
| Title + one purpose line | 72 px | `--text-title` once, `--text-small` line under it |
| Stat row | 96 px | 2 to 4 Stats across, each 1392/N wide |
| Attention band | 120 px | at most 2 rows, each one line |
| Primary rows | 468 px | 9 one-line rows, or a DataTable of the same height |
| Controls row | 44 px | Segmented, or one Button per control |
| Gap between bands | 16 px x 5 | `gap-2xl` |

That sums to 880 px against a 900 px fold, so a page taking all six bands has nothing spare. A page
never gets a hero, a description paragraph, an illustration, or a second title: every one of them
is 60-200 px of fold that a required fact then falls below.

Width is what buys the rows. A row is one line here, not a stacked pair: the title and its facts
sit side by side in columns that repeat down the band, so the reader compares straight down each
column. Give every band `w-full` and `min-w-0` on the flex child holding text, so a long title
shrinks its column instead of widening the page. A fact that will not fit the line is a `Badge` on
it or a press detail, never a second line, because a two-line row halves the rows above the fold.

## Component choice

The kit already answers to both colour schemes and to the width it is given. A `div` composed to
look like a kit component answers to neither. Pick by what the information *is*:

```
What is this?
├── A number the reader compares over time or against a peer → Stat (StatLabel + StatValue)
├── A state a record is in → Badge, on the record's title line
├── A set of comparable records the reader scans down → RowLines (≤ 5 fields) or DataTable (> 5)
├── One record the reader acts on → Card, one per record, never one per fact
├── How a whole divides → Meter (one bar) or Breakdown (named parts with shares)
├── A series the reader compares across a period → Chart; a single figure is a Stat, not a Chart
├── A choice that narrows or reorders the records → Segmented
├── A prepared write the member confirms → ApplicationAction
├── A hand-off to chat → Button, label starting "Review"
└── Explanation of the app or its debts → one sentence of prose, not a component
```

Never a card, chip, badge, or metric tile for a plain fact. A row with two `Badge`s and a `Stat`
has turned three words into three components, and the page reads as decoration.

## Hierarchy of acts

A reader finds an act by where it stands and how heavy it is drawn. Two acts drawn the same
weight in two places are a page with no primary act, so every act on the page takes exactly one
place and one weight from this ladder.

```
What does the act do?
├── Acts on the whole page — hands it to chat, redraws it, opens setup → Header `acts`, max 3
├── Acts on one record → the row's own control, variant="row"
├── Narrows or reorders the records → PageToolbar, Segmented, never a Button
├── Opens more about one record → PressRow → Sheet; the row itself is the control
└── Is rare, destructive, or the fourth page act → DropdownMenu behind IconDots, in Header `acts`
```

The Header holds at most 3 acts. A fourth goes into the menu, because a bar of five equal buttons
is five acts with no order and the reader scans it every time instead of once.

One variant per level, top to bottom:

| Level | Variant | Where | Count on screen |
| --- | --- | --- | --- |
| Primary | `send` | Header `acts`, the one act the page exists for | exactly 1 |
| Secondary | `outline` | Header `acts`, a Card foot | ≤ 2 in the header |
| Row | `row` | inside a row or Card, acting on that record | 1 per row, 2 when the row approves or skips |
| Quiet | `quiet` | See more, Dismiss, Show all N | wherever a band is cut |
| Glyph | `mark` | the IconDots menu trigger, close | 1 per band at most |
| Choice | `option` | a Segmented member | never an act |

`send` is the dark fill and the eye lands on it first, so a second one means neither is first.
`Review in chat` is the primary act on any page whose workflow ends in chat; `Prepare <noun>` is
then the row act that feeds it.

Content is repeatable. Each band draws one row shape, and every row carries the same fields in
the same order, so the reader learns the shape once. A row shows at most 2 lines and 5 fields:

```
Does the record have more than 2 lines or 5 fields to show?
├── Yes → the row shows its title line and one fact line; PressRow opens a Sheet with the rest
└── No → RowLines, nothing opens
Does the band have more rows than the fold budget allows?
├── Yes → draw the budgeted rows and one quiet "Show all N" that reveals the rest in place
└── No → draw them all
```

A Sheet is a drawer over the pane, not a page, so the reader keeps their place in the list. A
"Show all N" reveals in place, so the region grows below the fold rather than pushing the next
region down before the reader asks.

Type and colour take the same ladder, one step per level and no step skipped inside a band:

| Level | Type | Colour |
| --- | --- | --- |
| Screen title | `--text-title` | `--color-ink` |
| Band heading | `--text-label`, `font-weight-strong` | `--color-ink-soft` |
| Row title | `--text-ui` | `--color-ink` |
| Row fact | `--text-small`, `--font-mono` for figures and dates | `--color-ink-soft` |
| Link or act | `--text-ui` | `--color-link` on a link, ink on a Button |
| Needs attention now | the level it is at, unchanged | `--color-attention-ink` once per band |

A band heading set in `--text-title` competes with the screen title, and a row fact set in ink
competes with the row title. The ladder is what lets the reader skim by weight and stop at the
level they need.

## Copy

The reader is a member who knows their work, not the connector's schema. Every string on the page
is written for them, so a field name never renders — not as a label, not in a tooltip, not in an
empty line.

| Connector gives | Page renders |
| --- | --- |
| `age_hours: 19` | opened 19 hours ago |
| `open_issues: 2` | 2 open |
| `assignee: null` | nobody assigned |
| `state: blocked` | Badge "Blocked" |
| `created_at: 2026-08-28T…` | Friday 28 August |
| `login: alex` | alex |

Rules that hold on every page:

- A count renders as a digit and a noun: `2 open`, `41 seats`, `3 invoices`. A digit alone is not
  a fact and a noun alone is not a count.
- A date renders as day and month: `28 August`, never an ISO string, never a relative word alone
  when the objective named the date.
- A person renders by the name the connector's member roster gives, unchanged, in `--font-mono`
  when it is a handle.
- A title of a record is copied whole from the source — `Webhook retries lose delivery order` —
  because that is how the reader finds it in the other tool.
- Source prose is rewritten. A sentence that shares 5 or more words with the transcript, email,
  or note body is a copy, and the page is graded as copying. Write the task, the decision, the
  ask — never the filler around it.
- A control that hands the page to chat is labelled `Review in chat`, `Open chat to review`, or
  `Review <noun> in chat`. Every member act happens in chat, so the label says where the act is.
- A control that stages a chat act on the page is labelled `Prepare <noun>` — `Prepare
  assignment`, `Prepare note for review`. It stages; it does not do.
- A row a member can approve or skip carries the words `Approve` and `Skip` on its controls and
  a sentence saying the act completes in chat: `Created only after approval in chat.`
- Every region has an empty line, in reader words: `No decisions recorded.`, `Nothing needs a
  person right now.` A region that renders nothing is measured as missing.

## Facts survive layout

What you see wrong is a layout fault. The repair is always to the layout, never to the facts: the
facts are why the page exists and the layout is only how they fit.

```
You see…
├── a horizontal scrollbar, or content past the right edge
│   └── the text child in that flex row lacks min-w-0 → add it; then truncate the identifier
├── text cut off mid-word or mid-row
│   └── the row is one line and the text is not → let it wrap (drop truncate) or cut a field
│       from that line to the press detail — never cut the field from the page
├── two things printed over each other
│   └── they share a line with no gap → put them in one flex row with gap-sm
├── a region you drew above the fold sitting below 900 px on the real page
│   └── the bands above it grew → cut rows above it to the fold-budget count; never delete the
│       region and never move it in the design
├── a region that renders nothing
│   └── it needs its container and its empty line
└── a control that changes nothing you can see
    └── a filter must hide rows, a sort must reorder them
```

Deleting a region to win back fold space removes the facts it held. Moving a region below the fold
in the SVG to match a page that grew makes the drawing a record of the mistake. The one repair that
holds both is fewer rows above the fold.

## Filling the width

A page drawn for one narrow column and rendered at 1440 px reads as a stripe of content in a field
of grey. Lay bands across the full width and let their content divide it:

- A row is columns, not a stack. Give each field its own column, the same column in every row, so
  the reader scans down one of them.
- `min-w-0` on the flex child holding text, and `truncate` on identifiers and record titles. Wide
  does not mean unbounded, and one long title still pushes a row.
- No fixed widths. `w-full`, `flex-1`, or a grid the kit's Page holds; a `w-[…]` is a guess about a
  pane you cannot measure.
- Bands may sit side by side when neither needs the full width — a Stat row beside an attention
  band buys back 96 px of fold.

## Controls

The page has at least 2 accessible controls, and each visibly changes the rows on the page. A
control that opens nothing, or changes a colour, is not counted.

```
Which two?
├── The rows have a category (area, status, stage, role) → Segmented that filters them
├── The rows have an orderable fact (age, load, value, date) → Segmented or DropdownMenuRadioGroup
│   that sorts them; the label names the order ("Oldest first", "Lowest load")
├── The rows are approvable → Approve and Skip buttons per row, changing that row's Badge
└── One row is the workflow's subject → selecting it stages a Prepare control's detail
```

A filter that empties the list renders the region's empty line, in the filter's words: `No
platform issues open.` The reader learns the filter worked; the deploy measures a region that
still renders.

## Prepared actions

An `ApplicationAction` is the one write a page may carry, and it carries it verbatim:

- The `action` prop is the contract entry unchanged — every field, every string, including
  `label`, `success_text`, `read`, and `render`.
- Its read runs on mount, so the result is on the page after a reload. The component does this
  when the `read` field is present; do not add a second fetch.
- The result renders as text on the page, next to the control, in `success_text`'s words.
- The chat hand-off beside it calls `navigate` with the contract's route and reads `Review …
  in chat`.

No other write exists on the page. A GitHub call from page code, a form that posts, a button that
creates a record — each is a member act taken without the member, and the deploy refuses it.

## Draw within the box

A wireframe whose text leaves the viewBox is a drawing of a page that cannot exist. Regions here
are columns of their own width, so count characters against the column a line sits in, not the
page. Inter averages this much per character:

| Font size | Average character width |
| --- | --- |
| 11 px | 5.9 px |
| 12 px | 6.3 px |
| 13 px | 6.8 px |
| 14 px | 7.5 px |
| 16 px | 8.6 px |
| 24 px | 12.6 px |

A column 400 px wide fits 53 characters of 14 px text, and 640 px fits 85. Cut every `<text>` to
what its own column holds as you draw. A line that must say more is two
`<text>` elements on two rows, and the region's height grows by the line height, which is the
font size × 1.5.

## Type and colour

- `--text-title` (24 px) once, for the screen's title. Every other heading is `--text-subtitle`
  or a `--text-label` in `--color-ink-soft`. Two titles compete, and the reader's first scan
  loses its anchor.
- `--font-mono` for identifiers, handles, counts, dates, hashes. The reader compares them down a
  column, and monospace lines the digits up.
- Blue is a fill, a marker, a link. Orange is only where something wants attention right now.
  Body text is never an accent colour, and a pane is never an accent fill: a whole blue band
  says nothing because everything in it is equally loud.
- Green and yellow are the kit's live and blocked states. Red, purple, and any decorative hue
  fail the house criteria.

## Deploy, then look

The deploy builds the page, holds it inside `ufo/kit` and refuses one that never mounts. Whether it
is any good is read off the page, and you are the only one who reads it: a page you have not looked
at is a page nobody has.

**Deploy first, then look, then repair, then deploy again.** The first deploy is what puts
`vite.config.ts`, `preview.html` and `sdk/` into the project — before it, the project is your
`app.tsx` and an `index.html`, `vite build` cannot resolve `ufo/kit`, and `preview.html` is not
there to open. Do not try to look before the first deploy; there is nothing yet to serve.

Two things make the look fail if you improvise them. The page is a module build, so `file://` is
refused by CORS and mounts nothing — it must be served over http. And the site link the deploy
returns is not reachable from in here. `start_server` is the way: it serves the folder, waits for
the port, and hands back the URL.

```
start_server(project_path="/workspace/ufo-app")        -> http://localhost:<port>
```

Open `<url>/preview.html`, which is the shell a member sees, and take the app out of its frame:

```js
const { chromium } = await import('playwright');
const browser = await chromium.launch();
const page = await (await browser.newContext({
  viewport: { width: 1440, height: 900 }, colorScheme: 'light',
})).newPage();
page.on('pageerror', error => console.log('PAGE ERROR:', error.message));
await page.goto('http://localhost:<port>/preview.html', { waitUntil: 'networkidle' });
const app = page.frame({ name: 'ufo-app' });
emitImage(await page.screenshot());
console.log(await app.$$eval('[data-app-region]', nodes => nodes.map(n => n.dataset.appRegion)));
await browser.close();
```

`emitImage` is what puts the picture in front of you — a screenshot you do not emit, you have not
seen. Every `js_repl` call passes `reset: true` and closes its browser in a `finally`: state
commits on exit 0, so a second call redeclaring `chromium` fails to compile against the first.

Look at it light and dark. Click every control and screenshot what it did. Read the required facts
off the rendered text, not off your source.

**One pass. Then answer with the site unless the page is broken.** Broken is a short list:

```
Did the look find any of these?
├── the page did not mount, or the console shows an error
├── text cut off, printed over other text, or running past the right edge
├── a required fact from the objective is not on the page
├── a control that changes nothing you can see
└── a region that renders nothing at all
   → repair those, deploy once more, and answer
Nothing on that list?
   → answer with the site now
```

Spacing you would nudge, a colour you would pick differently, a heading you would word again: none
of these is broken. A page that clears the list is done, and a second pass looking for something to
improve costs the member a build and finds nothing they would have noticed.
