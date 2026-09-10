# triage

## Intent

A queue of messages carrying drafted replies: a draft is picked and sent, the rest starred or archived.

## Data

`data/triage.json` is one instance.

- Required — record list: `messages`; primary text: `subject`; drafts: `replies`; group: `starred`.
- Optional — people: `sender`, `initials`; secondary text: `summary`; date: `received`.

## Flowchart

```
Layout → fills the container, no width and no max width
├── the count → Stat, one number with no target and no delta
└── the record list → ItemGroup rows, one column at every width, drafts wrapping under the text

Header → ActionBar variant="header"
├── ActionBarTitle → the app name, and a menu of the sort keys
└── ActionBarActions → IconButton to write and to search

Prompts → a Prompt chip per group and one for the whole list, active on the one in force

Search → SearchField over the person and the primary text, drawn by the search action, with onClear

the count → Stat of the records still waiting, with a sub naming what it counts

the record list → records group by the priority flag, the priority group first
└── ItemSection per group, counting its records, folding its rows
    └── Item, accent primary in the priority group, selected while a draft of its is held
        ├── ItemMedia variant="avatar" → Avatar of the person
        ├── ItemContent → ItemTitle of the primary text, ItemDescription of the secondary text,
        │   ItemMeta of the person and the date
        ├── ItemActions → the priority toggle, and Menu of the row verbs
        └── ItemFooter → Prompts wrap of one Prompt per draft, active on the one held

Foot → Composer, drawn once a draft is held or the write action opens it, busy while it sends
```

## Interactions

- A draft chip → the held draft and its record, Item selected → the Composer at the foot fills with the draft and the chip draws active.
- Composer submit → the record leaves the list → the composer goes busy, then the row goes and the count drops by one.
- Row priority toggle → the priority flag → the row moves between the two groups and both counts change.
- Row Menu, archive → the record leaves the list → the row goes and the count drops by one.
- ItemSection heading → the group's open state → its rows fold, the heading and count staying.
- Write action → an empty Composer at the foot → the field opens with no draft held.
- Search action → the SearchField draws → typing narrows both groups; onClear restores them.
- Prompt chip → the group filter, chip active → only that group draws; the whole-list chip clears it.
- ActionBarTitle menu → the sort key → rows reorder inside both groups.

## Checklist

- The count reads the records still in the list, and each group heading reads its own count.
- Pressing a draft fills the composer at the foot, draws that chip active and that row selected.
- The composer draws only once a draft is held or the write action opens it; submitting draws it busy, then removes the row.
- Sending drops the count by one and leaves the other rows in place.
- The priority toggle moves a row to the other group and swaps both counts.
- Archiving a row drops the count by one without opening the composer.
- The sort key from the title menu reorders the rows inside both groups.
- Folding a group hides its rows and leaves the other heading drawn.
- The write action opens an empty composer with no chip active.
- Clearing the search field restores every record, and a group chip in force draws filled with only
  that group's rows drawn.
- At 349 a row's drafts wrap onto their own lines inside the container; at 1200 they sit on one.
- Sample: three records in the priority group and three in the other.
