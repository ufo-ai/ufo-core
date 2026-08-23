---
name: report-digest
description: Load when a member asks what their scheduled reports said, or asks to catch up on, skim, or roll up recent reports. Not for writing a report and not for scheduling one.
---
# Report digest

One report in, one entry out: a title, one short sentence, and at most three lines. The entry is
read beside other entries by a member deciding which report to open, so it is written to be
skipped — earn the press or cost nothing.

An entry is not a small report. It is the shortest thing that makes a reader open the real one.
When a line and the report both say it, the line is too long.

## Title the findings, never the genre

The title names the two or three largest findings, the way a changelog titles a release by what
landed rather than by its version. The report's own title usually names the genre and the date;
both are drawn beside your entry already, so repeating them spends the widest line on nothing.

Bad, the genre and a date the page already shows:

    MetalCraft competitive intel — Thursday 20 August 2026

Good, what the report found:

    Slack ships a coding agent free, OneCLI pivots to harnesses

## Name who did it

When the report says a person did a thing, that person's name goes in that line's `actor`. This is
the first thing a reader looks for — their own name, and their colleagues'. A report attributes in
whatever shape its author chose: a name in parentheses at the end of a bullet, or a name opening
the sentence. Read both.

Take the person out of the line when you put them in `actor` — the entry draws the name itself,
and a line repeating it wastes the words.

    report:  **Folder sync reads markdown notes**, a repository syncs nightly (Priya Raman)
    text:    Folder sync reads markdown notes, syncing nightly
    actor:   Priya Raman

Never guess an actor from surrounding context, never put a company there, and leave it empty
rather than write "the team". A report about other companies' moves has no actor on any line.

## One line, carrying its own number

Each point is one line. Not a clause plus a qualifying clause — one line, ten words or fewer,
naming what moved and carrying the magnitude the report stated.

Bad, a second clause the reader did not need:

    Deploy time nearly doubled — median merge-to-production rose from 26 to 41 minutes
    this quarter, all of it in one stage

Good:

    Deploys slowed from 26 to 41 minutes

A report that states a magnitude has done the work; carry the number, drop the explanation. The
explanation is why the reader opens the report.

## Rank before you write

Score every finding the report carries, then write them in that order:

| What the finding is | Score |
| --- | --- |
| The reader must do something about it | 4 |
| It reached a terminal state — failed, expired, shipped, closed | 3 |
| It moved, and the report states by how much | 2 |
| It is true and unchanged | 0 |

The highest score is the title, and the title's first three words carry it: a reader deciding
which report to open sees those and stops. A finding scoring 0 is dropped, however prominently the
report leads with it. The reader is given, so a finding they own outranks a larger one they do not.

## Budgets

Count words, and stay under. A field over its budget is cut where it runs out, so the words past
the budget are words no reader sees.

| Field | Budget |
| --- | --- |
| `title` | 12 words |
| `summary` | 18 words, one sentence |
| `text` | 10 words |
| `actor` | the person's name, nothing else |

The summary says what the title could not fit, never what the title already said. When the title
carries the findings and nothing is left to add, a shorter summary is the right answer.

## Every field is words, not markup

A field carries prose and nothing else. A markdown link, a heading mark, a code span or a bullet
character written into one is not a mark the reader sees applied — it is a value the entry holds,
and wherever the entry is drawn those characters are what arrives. Carry the words and drop the
marks, including the links the report cites its facts with: the reader reaches the report itself,
so the entry never has to cite it.

## Bounds

At most three lines, fewer when the report holds fewer. A report supporting none — a short
all-clear, a table with no stated finding — gets none, and the summary carries it alone. Every
claim traces to the report: an entry saying more than the report does is a defect, and a thin
report is correctly given a thin entry.
