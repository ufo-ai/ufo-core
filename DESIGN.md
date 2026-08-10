# ufo — portal design

The visual and structural system of the member portal (`extensions/web/frontend`). Copy register
lives in CLAUDE.md ("User-facing copy") and is not restated here. Two surfaces are exceptions:
the landing page (`infra/modules/edge/landing.html`) is its own world, pinned by
`infra/modules/edge/page.test.mjs`; the debugger (`extensions/debugger/frontend`) is an operator
surface outside this system.

## Already enforced — cite, never restate

| Rule | Held by |
|---|---|
| The theme is the only stylesheet; no `<style>`, no selector in a view | `gates.py` `_portal_style_failures` |
| No raw measurement or colour in a bracket class — resolve through `var(--…)` | `gates.py` `_portal_style_failures` |
| A class with a short spelling is written short — `truncate`, `size-`, `gap-`, `cn()` | `gates.py` `_portal_style_failures` |
| No `dark:` variant — `color-scheme` carries the scheme | `gates.py` `_portal_style_failures` |
| Every colour resolves through the system-colour tokens | `theme.test.tsx` |
| Reduced motion, `100dvh` + safe-area insets, table overflow | `theme.test.tsx` |
| Wordmark is `ufo`, one word, no tracking | `theme.test.tsx` |
| Hash grammar, place keys, push/replace/back history | `route.ts` types + `urlstate.test.tsx` |
| A workspace tab or a section without a view is a compile error | `registry.tsx` totality |
| Only the field primitives draw a field surface (`bg-field`) | `theme.test.tsx` |
| Font smoothing, balanced headings, pretty body wrap | `theme.test.tsx` |
| Press scale, hover fill, reduced-motion cutoff | `theme.test.tsx` |

## Tokens

`theme.css` is the one token source. Tokens are named by role, never by appearance: colours
(`surface`, `ink`, `edge`, `fill`, `attention`), spacing (`hair`…`6xl`), type (`mono`…`title`),
radius (`sm`, `control`, `panel`, `bubble`), containers (`max-w-control`, `max-w-form`, …). A new
value is a new token in `theme.css`, consumed as its utility or as `var(--…)` — the gate refuses
the inline alternative.

## Navigation

The shell is a fixed left sidebar (`--container-sidebar`) and one pane; under
`--breakpoint-narrow` it flips to a top strip. The sidebar holds, in order: wordmark, new
conversation, the conversation rail, section buttons — Agents, Scheduled, Artifacts, Sites,
Customize, Workspace — footer (member email, admin entry). No icons. Selection is the 2px left
marker (`border-l-ink`) plus `aria-current`.

A destination the sidebar reaches directly is a top-level section: the hash is `#/<section>`, the
name is one member of `SECTIONS` in `lib/route.ts` and one entry in `SECTION_VIEWS`, and it holds
one view, so the shell draws no tab strip — the pane is the page, and a strip of one names what the
`<h1>` above it already said. The section's own body is a `Section` headed by that same label, as a
workspace tab's listing is headed by the tab's. Section and tab alike take their place bookkeeping
from `usePlaceRecorder` (`kernel/place.ts`) — one owner, so a section's hash carries the same place
keys a workspace tab's does and a row opened in a section answers Back exactly the way a row opened
under a tab does.

A screen earns the sidebar when the member comes to it for what the agents produced rather than to
change how the workspace behaves. Scheduled, Artifacts and Sites are that work, read as often as a
conversation is, and a record two clicks deep behind a strip of settings tabs is a record the
member does not know is there; Workspace keeps what governs the workspace — team, sources,
credentials, memory, usage. Scheduled could not have stayed a tab in any case: it lists one kind
across the whole audience, which no single agent's tab can head.

A section and an agent tab may hold the same kind, and Scheduled does: the section is every
agent's, the tab is that agent's own. They carry the same label, because the label names the kind
and not the scope — the pane the member is standing in already states the scope, and a tab reading
`This Agent's Scheduled` would say twice what the agent's own `<h1>` says once. Nothing else is
duplicated: one `ObjectPane` draws both, taking the agent to read in or null to read across the
audience, which is the same choice the index route itself offers.

A rail row states the conversation's title, and nothing else the member did not ask for. The rail
is already ordered by recency and already grouped under `Today` / `Yesterday`, so a stamp on every
row restates the group it sits in, in the one place with the least width to spare. A second line
appears only where the row carries a fact the group cannot: a conversation held by an agent other
than the main one names that agent. The group label is the smaller, lighter type
(`text-small opacity-(--muted-strong)`, no bold) so the eye reads titles first and the labels as
the scaffolding between them.

Tabs are `kernel/tabs.tsx` — `TabStrip` and `TabPanel`, always as a pair, sharing one `group`
name that derives both ids. The strip carries `role="tablist"`, `aria-selected` and
`aria-controls` per tab, the bottom marker on the selected one, and the roving tabindex the role
requires: one press reaches the strip, then left/right move the selection and carry the focus,
wrapping at each end. The panel carries `role="tabpanel"` and `aria-labelledby`. A view passes its
tab array, the current tab, a label function, and a pick handler; it never writes the markup.

A new view is one registry entry — label, renderer, and tab together — plus its member in the
`lib/route.ts` array its shell reads (`WORKSPACE_TABS` for a tab, `SECTIONS` for a section). No
second list: a label map or dispatch chain beside a registry is the shape to refuse, and the
sidebar draws its section buttons by mapping `SECTIONS` rather than naming them.

A destination the sidebar reaches is `TabbedPane`: a title, a tab array, and one registry. Workspace,
Customize and each section differ in nothing else, so they are one shell and one place recorder
(`kernel/place.ts`) — a second copy would be a second answer to what opening a row does to history,
and a strip is drawn only where the array holds more than one view. Each destination keeps its own
route kind, its own tab type, and its own hash builder, so a tab without a view stays a compile
error rather than a string the router fails to match. Workspace holds what the workspace *is* — its
people, its inputs, its output, its spend. Customize holds what the member shapes the agents *with*:
connectors, skills, memory.

## Settings views

Anatomy, top to bottom: `<h1>` title (`text-title font-strong`) → tab strip → scrolling body
(`flex-1 overflow-y-auto p-2xl`). The pane owns that `<h1>` and a page holds exactly one: a record
opened inside the pane — an object detail, a conversation — heads itself at `text-title` as an
`<h2>`, because the pane it opened in is still the page and a second `<h1>` states there are two.
The outcome notice renders first in the body,
at `text-ui` and under `role="status"`, so a result the member did not scroll to is still announced
and still legible.
The body is `Canvas`, like every other surface. Sections run from the left at `max-w-section`,
never centred: the body and the title share `px-2xl`, so a section's left edge lands on the same
line as the `<h1>` and the tab strip, and the eye returns to one margin down the whole page.

Sections are headed by the shared `Section` component — never hand-written heading markup. A section stacks three bands: the heading, then `Section bar`, then the records. The bar
runs left with the rest — the search over the section's records at `max-w-control-row`, then the act
that makes one directly beside it — so the member reads what the section is, how to narrow it, and
how to add to it before reaching a single row.

`Section` is the only owner of vertical rhythm: `gap-lg` between its three bands and between the
records and whatever follows them, `mb-6xl` to the next section, and the scrolling body sets no gap
of its own. No block inside carries a bottom margin — not `Table`, `CardGrid`, `PanelBlank`,
`PanelSkeleton`, the figures grid, or `Pager`. A block that spaces itself *adds* to the container's
gap instead of sitting in it, so the distance between two sections becomes a sum of whatever bands
they happen to hold — a table-and-pager section drifting further from its neighbour than a
figures-only one. One number, set once, in the component that stacks them.

Records render as tables (`Table`/`Th`/`Td`); options render as forms (`FormFromSchema`, `Label`,
`Input`, `Select`, `Hint`). There is no setting-row pattern. Table headers and all labels are
Title Case. A schema field is labelled by the schema's own `title`, falling back to the property
key only where there is none: `internet_access_allowed` is the wire's word for it and
`Internet Access Allowed` is the member's. The schema's `description` is written for the agent that
reads the spec — it names `status` reads and governed paths — so it never reaches a member, and
`SchemaProperty` does not carry it. A boolean field puts its label beside the box on one line; a
bold label stacked over a lone checkbox reads as a field whose input went missing. Row actions sit in a trailing unlabeled column as `Button variant="row"`. An empty
state is the blank card `DataTable` draws from its `empty` string: one full sentence, at most one
action.

A form is a card the way a table is a card. `FieldGroup` draws the bordered surface, stacks its
`Field`s at one gap, and takes the act that commits it as `submit` — set right on a footer row
under the card's own rule, where the last field ends. A submit floating loose beneath the final
input belongs to nothing on the page and reads as the opening of whatever follows. `Field` sets its
label over its control, never beside it: a settings sheet holds names as long as
`Internet Access Allowed` beside boxes as narrow as a model id, and a label column sized for both
is a column of whitespace on every other row. Every control in a form carries a label — a bare
placeholder is not one, since it leaves as soon as the member types.

A record whose fields are all short values is a table; one carrying a sentence chooses by whether
that sentence is scanned past or read. Scanned past, it is a `RowLine` — name on top, the short
fields and the prose beneath as one truncated muted meta line. Read, it is a `CardFace` — a
two-column grid of bordered cards drawn by `CardGrid` (`kernel/cards.tsx`), one column under
`--breakpoint-narrow` where two would clip the name off every card, carrying a mark, the
name, the status opposite it, the sentence wrapping at full width, and the row's act at the card's
foot; backtick-quoted literals in the sentence render as code spans, not raw backticks. The mark
takes one of two shapes. A record with no picture of its own takes `shape: "square"` — a
`size-6xl rounded-panel bg-fill-subtle` block inset top-left, the status on its line (Credentials).
A record that has or will have one takes `shape: "band"` — a full-bleed `h-(--size-band)` strip
above the card body, filled by the record's own image where it has one and left as the subtle fill
where it does not (Artifacts, Sites); the status then rides the name's line. A band that fails to
load falls back to the same fill rather than to a broken image.

Row lines are `RowLines` (`kernel/rows.tsx`) — the list both the declared listings and a bespoke
section draw, as `CardGrid` is for cards. Where the record is a thing to open, the whole row is the
control, and `rowControl` (`kernel/row.ts`) is the one thing in the portal that makes it one, on
whatever element the presentation makes a row out of — a `<tr>`, a row line's `<li>`, a card. The
target is the row, not a word inside it, which is why this is not the name-as-control the cards
refuse: there is nothing to hunt for and nothing that reads differently at rest than under the
pointer. The hover fill states the extent of what is being pressed.

The row takes `role` and `tabIndex` rather than being a `<button>`, because a row holds its own
acts and a button inside a button is not markup a browser will keep. `rowControl` therefore guards
every press: one that lands on the row's own control — a `ConfirmButton`, a download link, a
field — belongs to that control and never also opens the row, so opening cannot ride along with a
delete. Eligibility is decided in one place, by whether a row is handed a control at all: a row the
member may not open takes no role, no tab stop and no pointer cursor, and the state it is in
(`Private`, `Not shared with you`) is a meta part rather than a control offering an act the row does
not carry. One record is named one way on every surface that lists it: a conversation's row states
the same string its rail row does, falling back to whose it is where the words that would name it
are content the member may not read.

A card's name is text, never a link and never a button. The act is a `Button variant="row"` at the
card's foot saying what it does — `View` where it opens the record. A name that is also the control
makes the card's one target invisible until hover and reads as a different affordance on every
screen; a labelled button reads the same everywhere. Where the row carries an act the member came
for rather than an act on the record — Agents' `New conversation`, Sites' `Open` — that one takes
`send` and leads the foot, with `View` beside it: a card grid is a launcher as much as a directory,
and the filled button says which of the two the screen expects.

An act that leaves the portal is an `<a>`, and it is drawn only where the row can answer it. Sites'
`Open` carries the site's own link and so takes `target="_blank"` and `rel="noopener noreferrer"`,
while `View` beside it opens the record in the pane — one foot, two destinations, each named by the
word for where it goes. A site whose row carries no link draws no `Open` at all rather than a dead
control: the field is absent exactly when the deploy configures no public base URL, and a button
that cannot do its one thing is worse than its absence.

A card's text is cut to a fixed number of lines — one for the name (`truncate`), two for the
description (`line-clamp-2`) — and `CardGrid` imposes both, so no row can opt out. A grid reads as a
grid only while every card is the same height: one 60-word credential description beside a
seven-word one drags its whole row down, floats the two feet apart, and turns a scannable field of
records into a ragged column. Two lines is enough to choose by, and the record's own screen carries
the rest. A narrow column cannot hold a paragraph: give one prose and it towers, and
every column beside it is squeezed to pay for it. Credentials is the case that settles the
tiers — 54 slots, each with a sentence the member must read to know what to paste, render as a
table 5921px tall, as a list whose one distinguishing sentence is cut mid-word, and as cards that
carry it whole. The width is what decides, not the prose: a record whose sentence is flanked by
two short values (Memory — class, date) is a table, the sentence taking the column that is left
over, which is nearly the whole width.

A card grid with one dominant family sorts itself; it never offers a sort control or divides
itself with sub-headings — a settings directory has one right order, imposed, and the view's
`rows` imposes it: distinct small families ahead of the homogeneous directory, filled before
empty within a family, alphabetical within that. The clustering reads as order, not chrome. An
internal id (`keyed_connectors`) never renders to a member. Narrowing is the search and one
segmented filter tablist — `All` first, then each state (`Filled`, `Not set` — the empty state
named by the verb that fills it), single-select with arrow-key movement — never a sort picker.

A table is a card: `rounded-panel border border-edge bg-surface`, bounded by its border rather than
by a fill or a shadow. A cell is `px-xl py-lg` (`py-md` on a header) and carries its
rule on top, not underneath — collapsed borders fold the first row's rule into the header's, and the
last row meets the card edge with no second line beside it. No vertical rule, no zebra, no cell
border. The card is drawn by `Table`, never by a view.

A pane's facts about its own subject are a column, not a line. `Facts` (`components/ui/facts.tsx`)
draws the card `Table` draws — one row a fact, the label in a `w-(--size-fact)` column at the size
and weight a `Th` takes, the value beside it. A subagent's model, round limit, and untrusted wall,
and an agent's role, installations, last update, prompt digest, and web audience each take a row.
Joined into one `·`-separated mono line — which is how both panes read before this — they are a
sentence the member parses before finding the one fact they came for; stacked, they are scanned,
and each carries a label the member can name back. Only a value that is literally an identifier —
a model id, a digest — stays mono. A typed object states its spec and its status through the same
column, each row labelled by the schema's `title` — never by the wire's field name, which is what a
`·`-separated status line has no room to expand.

A wire identifier is never a member's word. The kind the wire calls `scheduled_task` heads both of
its screens as `Scheduled` — the sidebar button and the agent tab the member pressed to arrive, the
two differing in scope and not in name — and names itself in a sentence as `scheduled task`. An
object screen's own copy is written around the
singular, so no screen has to guess a plural: `No scheduled task has been created yet.`,
`No scheduled task matches this search.`

An index read across every agent states the owner on the row; one read in a single namespace does
not, because the pane already named it. So the Agent column appears only in the section, second and
right of the name — the member reads which record and then whose, before the summary they scan
past — and carries that agent's name as a link to it. It is the one head that does not order: the
order the heads carry is the kind's own vocabulary, and the owner is the projection's. The section
also offers no pager, because a page merged out of one walk per agent has no single walk to
continue, while the tab pages on the cursor its one agent returned. Every act goes to the row's own
agent either way — the detail opens under it, the row's delete posts to its lane — and the create
act asks which agent runs the new row, drawn only in the section and only where the audience holds
more than one, since a select with one option states a choice the member does not have.

Content whose length the member cannot predict is held at the fold. `Reveal`
(`components/ui/reveal.tsx`) draws the same card, clips to `--size-reveal`, fades the last line
into the surface, and puts one `Show more`/`Show less` in a footer row under the card's rule. The
control is drawn only where the content is actually taller than the fold, and while open the
measurement is held rather than retaken — an expanded block always fits its own height, so
re-measuring would take its own control away. An agent's prompt runs for screens: left unheld it
buries every section under it, and the member who came for what is below never learns it is there.

A screen whose subject is one number leads with that number as a figure, not as a clause in a
heading: the card takes the same `rounded-panel border border-edge bg-surface` as a table, holding
a muted label, the value at `text-title font-strong`, and a muted note beneath. Figures sit in the
first section, the breakdown tables in their own headed sections below — so the member reads the
total, then where it went. A figure's label names its scope (`You`, `Workspace`, `This agent`) and
a section's title names its content (`Your spend`, `Your caps`); a label and a heading never say
the same words twice. Every screen worth copying — Laravel Cloud, Cohere, OpenAI — leads its usage
page this way.

A date reads one way in this portal: `Aug 7 2026`, from `day()` in `lib/moments.ts`, everywhere a
calendar day is shown — memories, artifacts, connections, conversations, files, an agent's last
update. It reads the parts off the ISO string rather than through `Date`, so no reader's zone shifts
a stamp across midnight and no locale reorders the parts. `2026-08-07 14:32` is the wire's format
and a clock time the member did not ask for; `8/7/2026` is a different date in half the world. The
chat rail shows no date at all — its groups carry the recency. The one moment that is not a calendar
day is the one a member is waiting on — a scheduled task's next run — which reads as the wait
(`relativeMoment`, `in 3h`): the date it falls on is the fact the member already knows.

Every value a member reads is a member's word, never the wire's. `tokens` is `Model tokens`,
`egress` is `Sandbox requests`, a cap's `park` is `Suspend the turn` — the map lives beside the
table that renders it and falls back to the raw value, so a dimension the deploy adds still reads
rather than crashing the screen. A test that feeds the table an invented value proves nothing about
this; the fixtures carry the real vocabulary.

`ring-*` is not available for that border. Tailwind emits a literal `#fff` ring default, which the
zero-authored-colour test refuses; `border` reaches the same hairline through the tokens.

A listing is a section like any other. `Listing` wraps itself in `Section` at `max-w-section`,
headed by the tab's own label — the string the member clicked to arrive — with the search, the
filter tablist, and `Refresh` in the `bar` slot, left with the heading. A tab that renders one listing therefore
sits on the same left edge and the same width as Team's members table, and the tab strip names the
section rather than leaving the records unheaded.

Every screen that lists records shares one bar, in one order and on one line: the search at
`max-w-control-row`, the `Filter` tablist, the `send` act that adds a record, then `Refresh`. The
bar does not wrap — a control that drops to a second line reads as an orphan of whatever is under
it, and the search is the item that has to give, since a name is short and the table beneath it
carries the whole record anyway. `Section` draws the row `items-stretch`, so every control in it
resolves to the height of the tallest and no button floats short beside the search.

A Customize screen whose records belong to one agent leads that bar with the agent, as the shared
`AgentPicker` (`kernel/agentpick.tsx`) — one `Select` at `max-w-control-row` carrying its own
`aria-label`, never a second spelling per tab. It sits ahead of the search because it chooses the
set and the search narrows within it; a picker after the act would read as an act. It is drawn only
where there is more than one agent to pick, the way the sidebar's new-conversation control is — a
picker of one is chrome. The pick rides the place (`#/customize/skills?agent=<id>`), so a reload
lands on the agent the member was reading and a link opens the screen they were looking at; it is
the one place the agent is named, and a link is the only way to reach one that is not the default.
Picking remounts the screen on the agent, which discards the read left behind and clears the search
and the filter that narrowed the one before it. Every fact naming the agent follows the pick: the
read, the empty line, and the lane a mutation is admitted into. An agent the place names and this
member cannot reach reports `No such agent.` rather than falling back to a different agent's
records.

A bar that outlives its records is drawn outside `Panel`, never inside it. A first read replaces
what `Panel` holds with the skeleton, so a bar drawn within it takes the picker out from under the
member the moment they use it — the read the pick starts is exactly the read that erases the
control that started it.

One record set can be stated twice, to two audiences, from one read. Customize's Connectors states
every grant the member holds on the agent they picked; the agent's own Connectors tab states the
subset that agent can actually reach, since a grant kept private is the member's and not the
agent's. The two differ in their rows, their picker, and the line they show when empty — never in
their table, their acts, or the endpoint behind them, and a second read for the narrower audience
would be a second answer to the same question.

The order sits on the head of the column it orders, never in a picker beside the search. A picker
states the field names a second time, in a control wide enough to push the act off the bar, and it
asks the member to name a column they are already pointing at. A pressable head carries `aria-sort`
and, on the active column only, a caret drawn inline in `kernel/table.tsx` — the third glyph in the
portal, and still not an icon package. Pressing the head the order already sits on reverses it.

An intent can answer with a credential request instead of a result. `Listing` holds that request
and `ListingSpec.credentials` renders it, so the state machine stays in the kernel and the form
stays in the view — the kernel imports no view, and a listing that never asks for a secret
declares nothing. The prompt is a dialog in the same shape as Team's Add Members — header carrying
the request's reason, one form holding every prompt, one send-variant submit in the footer — and
dismissing it clears the held request so the act can be raised again.

A section with no records yet takes the card its records would have taken. `PanelBlank` draws the
same `rounded-panel border border-edge bg-surface` as `Table`, on the section's own edge. A centred note *between two filled cards* breaks the column — the reader's eye
has nothing to run down — which is why the blank is a card and not a paragraph. Inside that card the
line and its act are centred: the card holds one sentence and one button, there is no column of
records for them to align to, and a left-set line under a full-width border reads as the first row
of a table that never arrives. The heading already names what is absent, so the card holds only a
muted line of body and the act that fills it; a title inside it would say the heading's word twice.
`PanelEmpty` stays the centred note, and only for a passing condition: a failed read, or a search
that matched nothing where no table stands.

A read in flight draws what is coming, at its own size. `PanelSkeleton` takes the panel's `shape` —
`table`, `cards`, or `form` — and lays out bars in the card, column, and row rhythm the payload will
take, so the answer lands into the space already held for it instead of shoving a centred
`Loading…` aside. The skeleton is invisible for its first ~190ms (the `hold` keyframe leading
`--animate-skeleton`), then pulses: a local read that returns in 40ms would otherwise flash a full
page of grey bars, which reads as a fault rather than as speed. The delay is CSS on an animation
already running, never a timer holding the render back.

A read whose path changes — a filter picked, a page turned — keeps the answer it has until the next
one lands (`usePanelRead`). Only a first read, which has nothing to hold, shows the skeleton.
Dropping to the skeleton on every filter press would unmount the tablist the member is pressing,
since the tabs are drawn from the payload.

The blank carries the act that would fill it, and it is the only copy of that act on screen: a
listing whose records are absent drops its whole filter bar, so the `Refresh` it would have offered
in the bar moves into the card as an `outline`. It stays `outline` and never `send`: re-reading is
not the act the screen is for, and the one filled button on a settings screen is reserved for the
act that commits something. Where records are registered in chat rather than in the portal,
`Refresh` is still the right act — the body states where the record comes from, and the button
re-reads for one that has landed since.

A filter that matches nothing leaves the table standing and says so in a row. `TableNote` spans
every column with the reason (`Nothing matches.`, `That filter is not available.`), so the header,
the column edges, and the filter tablist hold their positions while the member toggles. Swapping the
whole table for a centred note collapses the page under the control being pressed and moves the
control itself; a table is a shape the member is reading down, and a filter only ever changes what
is in it. Cards and rows, having no header to hold, keep `PanelBlank`. `DataTable` divides the two
cases by which string it was given: `note` is a table narrowed to nothing, `empty` is records that
were never there.

A record is acted on from the row it stands on. A table's last column is unheaded and holds the
acts — the destructive one as a `ConfirmButton` — so the member neither opens a record to delete it
nor hunts for a control that names the row in its own words. A row the member may not act on leaves
the cell empty rather than dropping the column, since the column is the table's shape.

The blank draws no icon. Seven settings screens would want seven glyphs, which is the vocabulary
that earns an icon set — and the portal has none by decision. Midday's own settings blanks carry
title, body, and one button with no glyph, so the pattern is complete without it.

An act that needs more than one field opens a `Dialog` rather than sitting under the records — the
act's own control lives in the section bar, and a typed object is created and changed through one
`SpecDialog`, never a form stacked beneath the index it writes into. The
two overlays divide by what they hold, not by size: a `Dialog` is centred, takes focus, and holds a
short form the member either commits or cancels; a `Sheet` is the right-hand drawer that shows a
record while the view stays live behind it. `DialogFooter` supplies `Cancel` and takes the
committing act as its child, so leaving is always the first control and never a corner glyph.
`Cancel` carries the padding of `send`, so the two footer controls measure the same: the member is
choosing between two ways out, and a smaller `Cancel` would state that leaving is the lesser one.
A form's outcome notice renders inside the dialog and only when it has words — the dialog
re-centres itself, so there is no layout to reserve against. A refusal therefore leaves the dialog
standing with what the member typed still in it; only an applied act closes it.

A form asks for the values, never the file. A skill is stored as `SKILL.md` with frontmatter whose
`name` the lane requires to equal the name the skill is filed under, so a box holding the raw file
asks the member to state the name twice and makes a refusal reachable by typo. The dialog takes
name, description, and instructions once each and composes the document, which is the general rule:
where a field is derivable from another field, the form derives it.

A dialog form that repeats takes one row per record — the fields side by side, the widest flexing —
with an `Add more` outline button beneath the rows and outside them, so the act that grows the form
is never mistaken for a field of it. The commit reads the rows it has (`Add member`, `Add members`)
and fires one intent per row; every row that applied leaves the form, and only the refused rows
stay behind with their refusals, so a second press can never re-commit what already landed. The
commit is `disabled` until every row carries a value: an act that cannot yet succeed says so before
the press rather than after it.

Once the dialog has gone, its result is a `Toast` — fixed bottom-left, `role="status"`,
self-dismissing. A toast reports only what applied; a refusal stays beside the control that can
correct it, because a message that dismisses itself cannot be read back.

## Controls

One control, one component. `Button` carries the four variants (`send`, `outline`, `row`,
`option`); `Input`, `Textarea`, `Checkbox`, `Label`, `Hint` carry the form, and `Select` composes
from `SelectTrigger` / `SelectValue` / `SelectContent` / `SelectItem`. A view composes them and
never retypes their markup — a hand-drawn border or field surface beside the primitive is the shape
to refuse.

Every control answers the pointer the same way: a hover fill (`hover:bg-fill-hover`, or
`hover:bg-ink-hover` on `send`), `active:scale-[0.96]`, over 100ms on `ease-control`. Reduced
motion cuts the transition, and the global `:focus-visible` outline is the only focus ring. A
disabled control is dimmed to `--disabled` and takes `pointer-events-none`, so it never answers a
hover it cannot honour — both live on the shared base, never on a variant, so no variant can drift
out of the pair.

A form states what it requires: `FormFromSchema` reads the schema's `required` list onto the
control, so the field carries `required` and the browser's own validity applies.

The schema states the shape of every field, and the form draws what the schema states. `examples[0]`
is the placeholder, so a cron line shows `0 9 * * 1-5` rather than describing itself; `format:
date-time` takes the browser's own date control instead of a box that accepts any prose; a string
the schema bounds is a value and gets one line, and a string it declines to bound is prose and gets
the taller box. Every facet is read the way the type is — off the property, else off the one `anyOf`
alternative that is not `null` — because a nullable field states its shape inside `anyOf`. The
schema's `description` is written for the agent and never renders: it names update semantics the
member never asked about. A field a form should hint at is a field whose model has to declare the
hint.

`Select` is the shadcn composite over `@radix-ui/react-select`, not a native `<select>`. A native
one takes its menu, its chevron, and its metrics from the browser: the chevron cannot be moved off
the padding edge, the popup ignores the theme, and the control never reaches the height of the
`Input` beside it. `SelectTrigger` draws itself with `CONTROL` — the same surface string `Input`
and `Textarea` use — so the two are one object to the eye, and the popup is portal markup the theme
reaches. The content caps at `--radix-select-content-available-height` and scrolls; it grows from
`--radix-select-content-transform-origin`, so it opens out of the trigger rather than in place.
Only the parts a view uses ship: no group, label, or separator exists until something selects with
one.

The chevron and the tick are drawn inline in `select.tsx`. The portal carries no icon set and does
not earn one at two glyphs — both take `currentColor`, so neither needs a token, and an icon
package would be a dependency with a vocabulary of one.

A row that pairs controls is `items-stretch`, never `items-center`. Two controls on the same
surface still resolve their heights from their own content, and centring unequal boxes leaves the
shorter one floating with its chevron off the line the row reads along. Stretching makes the row's
height the one both agree to.

A listing filter is `Filter` (`components/ui/filter.tsx`) — one component, every screen. It is a
segmented `role="tablist"`: a `rounded-panel bg-fill-subtle p-hair` container holding `All` plus one
tab per declared state, the active tab a raised `rounded-control border-edge bg-surface` pill inside
it — never `aria-current`, never an underline, never a count in the label. The component supplies
its own `All`, so a caller passes only the narrowings and no screen re-derives the empty value.

Selection is drawn in colour and surface, never in weight — neither here nor on the `TabStrip`
above. Bolding the active tab remeasures its text, so every tab after it slides sideways as the
member moves along the strip and the strip they are aiming at is not where they saw it. A raised
pill and full opacity against `--muted-soft` say the same thing at a fixed width. The section bar
stretches its controls
(`items-stretch`), so the search, the tablist, and `Refresh` share one height instead of three. A
search box carries no button beside it, whether it narrows what is loaded or asks the server: the
box is the control, `Enter` submits it, and a second control saying the word again is chrome.
Numbers are tabular: `Td` and `Th` carry
`tabular-nums`, so a column of figures never shifts under a re-read.

An anchor that must read as a button takes `buttonVariants({ variant })` and stays an `<a>`. This
is the shape, not a concession: a `Button` rendered as a link keeps `role="button"` and loses the
link semantics, so the variants travel and the element does not.

A field states its own condition. `disabled` dims to `--disabled`, takes `cursor-not-allowed`, and
drops the hover border, so a dead control never answers the pointer. A required field the member
left empty takes `user-invalid:border-ink user-invalid:border-dashed` — the browser's own validity
decides when. The border is drawn, not tinted: `Mark` measures 1.07:1 against light `Canvas`, so an
`attention` border states nothing there; the dash carries the signal in either scheme and without
colour.

An act already in flight takes `Button busy`. It sets `aria-disabled` rather than `disabled`, so
the button stays in the accessibility tree and the keyboard keeps its place across the submit; the
handler guards the act instead. `animate-working` pulses it and `opacity-(--muted)` states the same
thing under `motion-reduce`. `disabled` remains the shape for an act the member may not take at
all.

Nested radii are concentric: outer radius = inner radius + padding. `--radius-card` (28px) is
`--radius-panel` plus `p-4xl`, which is why the sign-in card and the button inside it agree.

Four divergences from shadcn/ui are deliberate. shadcn now distributes its visual rules as
`cn-*` classes resolved by an installed stylesheet, across a base × style matrix; the portal has
one theme and no `components.json`, so it takes the conventions and not the distribution.

| Divergence | Why |
|---|---|
| A destructive act confirms in place, not in an `AlertDialog` | The confirm guards the slip; the audited intent lane guards the act |
| One global `:focus-visible` outline, not a per-control `ring-3` + `border-ring` | One ring cannot drift out of step with another |
| Radii and spacing named by role, not derived from one `--radius` by a numeric ladder | A role survives a redesign; a rung does not |
| No `data-slot` on the primitives | shadcn's style CSS selects on them; nothing here would read them, and the gate forbids the selector that would |

The portal has no destructive colour and cannot have one: the palette is system colours, which
name no error hue. `attention` (`Mark`) carries every wrong-state signal instead.

## Destructive actions

`ConfirmButton verb="Remove"` — the first click arms the button and relabels it
`Confirm remove`; the second click fires; leaving the button disarms. No modal, no danger zone. The confirm guards against the
accidental click — authorization and reversibility live in the audited intent lane.

## Golden references

Copy a known-good implementation instead of composing from rules:

- `views/Sources.tsx` — a declared listing (columns, search, chips, empty, actions).
- `views/Team.tsx` — a searchable table whose act opens a repeating dialog form and reports by toast.
- `views/Memory.tsx` — a bespoke section (server-side search, dynamic filters) wearing the same
  bar, tablist, table, and dialog as the declared listings.
- `views/Artifacts.tsx` — a declared listing whose face is cards led by an image band.
- `views/Sites.tsx` — a bespoke section over a typed object kind, reading the same `CardGrid` the
  declared listings do, with a leaving-the-portal anchor beside the row's `View`.
- `kernel/objects.tsx` — one kind's index and detail, read in one agent's namespace or across the
  audience: the owner as a linked column where the scope needs it, and every act addressed to the
  row's own agent.
- `views/Agents.tsx` — two card sections over a prop, the row's own primary act on the card.
- `views/Skills.tsx` — a Customize tab whose records belong to an agent the member picks from
  the bar, the pick riding the place.
- `views/Connectors.tsx` — one read stated twice: the member's grants under Customize, and what
  the agent can reach on its own tab.
- `kernel/listing.tsx` — the listing renderer the declarations feed.
- `views/Usage.tsx` — figures over headed breakdown tables, every wire value labelled.
- `kernel/cards.tsx` — the card grid both the declared and the bespoke card sections draw.
- `kernel/panel.tsx` — the section's rhythm, the shaped skeleton, and the blank card.
- `components/ui/filter.tsx` — the one segmented filter every listing narrows with.
- `views/SubagentPane.tsx` — a subject's own pane: its facts as a column, its prompt behind the
  fold, its runs and skills in headed sections.
- `views/Conversations.tsx` — an index of row lines whose row is itself the control that opens a
  record inside the pane: one way back above the record, the record's own heading, and its
  sections beneath.
- `kernel/rows.tsx` — the row-line list both the declared listings and the bespoke sections draw.
- `kernel/row.ts` — the one way a record's whole row becomes the control that opens it.
- `components/ui/facts.tsx` — the labelled column a pane states its subject's facts in.
- `components/ui/reveal.tsx` — the fold long content is held at.
- `views/Overview.tsx` — an agent's facts, its schema-driven settings as a form card, its prompt
  behind the fold.
- `components/ui/field.tsx` — the labelled field and the card its form is set in.

## Conformance

A touched view leaves conformant with every rule above. Divergence is never copied forward, and
no rule change ships without updating the view or gate that holds it.
