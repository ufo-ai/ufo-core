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

## Two namespaces

A page imports from `ufo/kit` and from `ufo/blocks`, and from nothing else. Each recipe opens with
the two import lines it needs; copy them.

| Namespace | What it holds |
| --- | --- |
| `ufo/kit` | the page chrome (`Page`, `Header`), the one write (`ApplicationAction`), the chat hand-off (`Button`), the narrowing control (`Segmented`) |
| `ufo/blocks` | the record rows, measures, tables, charts and prose a recipe names, all of them inside one `BlockRoot` |

`BlockRoot` carries the type scale, the control resets and the surface the blocks draw against.
Blocks outside one are unstyled, so it wraps every band below the `Header`.

Eleven names are published by both namespaces as different components. Import each from the one
you mean; a bare name in a recipe names neither, and a gate refuses a recipe that leaves it bare.

| Name | `ufo/kit` is | `ufo/blocks` is |
| --- | --- | --- |
| `Avatar` | the portal's person mark | the blocks person mark, sized by `ItemMedia` |
| `AvatarStack` | the portal's overlapping set | the blocks overlapping set |
| `Card` | the portal's panel | the blocks panel, with its own variants |
| `CardAction` | the portal panel's act slot | the blocks panel's act slot |
| `CardContent` | the portal panel's body | the blocks panel's body |
| `CardDescription` | the portal panel's sub line | the blocks panel's sub line |
| `CardFooter` | the portal panel's foot | the blocks panel's foot |
| `CardHeader` | the portal panel's head | the blocks panel's head |
| `CardTitle` | the portal panel's title | the blocks panel's title |
| `DataTable` | the portal's row table | the blocks sortable table, driven by `useDataTable` |
| `Stat` | the portal's measure, with `StatValue` and `StatLabel` | the blocks page measure; inside a grid it is `StatTile` |

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

## What the contract keeps out

The blocks were drawn for a page of their own. This page is one pane of the portal, the product
supplies its chrome, and every member act happens in chat — so four of their shapes do not ship,
and each recipe says which it dropped and why under its own heading of this name.

| The block shape | Why it does not ship | What carries it instead |
| --- | --- | --- |
| `ActionBar` and its parts | the page's own bar is the kit's, and it holds at most three acts | `Header` with its `acts` |
| `Composer` | a member types into chat, never into a page | `Button` reading `Review in chat` |
| `Checkbox`, `CardButton`, `StatusIcon` used to write | a control that writes a record would change state no turn recorded | `ApplicationAction`, which chat approves |
| `Prompt` and `Prompts` | a chip that asks for something is a chat turn; one that narrows records is a control | `Segmented` for narrowing |

## The eight

| The app is about | Recipe |
| --- | --- |
| dated records with people, read one at a time | `meetings` |
| work whose state a member advances | `tasks` |
| entries grouped by what shipped them, read and narrowed | `radar` |
| measures with deltas over a window the member switches | `metrics` |
| runs scored over a series, the newest one's rows opened | `evals` |
| a tracker's records a member owns | `issues` |
| one period's records and measures, questions read | `digest` |
| a queue the member reads one record at a time | `triage` |
