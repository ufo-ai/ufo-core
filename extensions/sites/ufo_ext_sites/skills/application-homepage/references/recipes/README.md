# Recipes

A recipe directs one class of app. It names the roles that class of page needs, the region order
that carries them, the controls that must change something, and what a correct build shows. It
describes every app of its kind, never one instance of it: `tasks` covers personal chores, a
recruiting pipeline and a release plan alike.

Read the recipe for the class your objective describes, then draw. Where no recipe fits the app,
`designing-a-homepage.md` alone is the direction — a recipe from a neighbouring class is worse than
none, because it names regions the objective never asked for.

A recipe adds to `designing-a-homepage.md`; it replaces nothing. The fold budget, the act ladder,
the copy rules and the region order are that reference's, and a recipe assumes them.

## Roles

A recipe speaks in roles and maps them to the objective's own facts. Name each role once against
what the objective gave you, then walk the regions.

| Role | What fills it |
| --- | --- |
| record list | the set the page is about |
| primary text | what names a record |
| secondary text | one line that explains it |
| status field | a small set of states a record moves through |
| group key | the field the records group by, often the status field |
| date | when a record happens or happened |
| people | who is on a record |
| measure | a number with a label |
| delta | how a measure moved |
| series | a measure over a period |
| tags | short labels on a record |
| acts | what the member can do about a record |

A role with nothing to fill it is a region you do not draw. A required role with nothing to fill it
means the recipe is the wrong class.

## What a homepage does instead

A recipe's shapes are a member surface, and this page is one pane of the portal. Four of them
resolve differently here than they would in a page of its own:

| The shape | The homepage's answer |
| --- | --- |
| a bar carrying the app's name and its acts | `Header` with `acts`, at most 3 |
| a field the member types into | nothing — `ApplicationAction` stages the write, and a `Button` reading `Review in chat` hands the rest to chat |
| a control that writes a record (send, archive, delete, assign) | `ApplicationAction` alone, or a row's `Prepare <noun>` staging one |
| a control that narrows or reorders the records | `Segmented`, or `DropdownMenuRadioGroup` for an order |

Everything else maps straight onto the kit: a group of records is a `Section` carrying its own
count, a row that has more to say is a `PressRow` into a `Sheet`, a state is a `Badge`, a measure is
a `Stat` in one row above the records, a series is a `Chart`, a set of records with more than five
fields is a `DataTable`, and a whole that divides is a `Meter` or a `Breakdown`.

## The eight

| The app is about | Recipe |
| --- | --- |
| dated records with people, read one at a time | `meetings` |
| work whose state a member advances | `tasks` |
| entries grouped by what shipped them, read and dismissed | `radar` |
| measures with deltas over a window the member switches | `metrics` |
| runs scored over a series, the newest one's rows opened | `evals` |
| a tracker's records a member owns and plans | `issues` |
| one period's records and measures, questions cleared | `digest` |
| a queue the member approves or skips one at a time | `triage` |
