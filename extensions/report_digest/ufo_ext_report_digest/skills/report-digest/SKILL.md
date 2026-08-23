---
name: report-digest
description: Load when a member asks what their scheduled reports said, or asks to catch up on, skim, or roll up recent reports. Not for writing a report and not for scheduling one.
---
# Report digest

One report in, one entry out: a title, up to two lines under it, and a summary when a fact is left
over. A member reads the entry beside other entries to decide which report to open, so it is
written to be skipped — earn the press or cost nothing.

An entry is not a small report. It is the shortest thing that makes a reader open the real one.

## An entry is a change

An entry is earned by one of four things in the report: a state that changed, a number that moved,
a name that is new, or a date that is coming. Set `holds_a_change` from that test, and set it
before writing a word — it is the question the rest of the entry answers.

A report that finds what yesterday's report found holds no change. Set `holds_a_change` false and
write nothing else. The feed still shows the run, names the task that fired it, and links the
report, so a reader who wants the all-clear has it. Silence is the right entry for a quiet report.

## The order is the ranking

Rank the findings, then place them. The title carries the first-ranked finding and carries that one
alone. Line one carries the next, line two the one after it. A report holding one finding gets a
title and no lines.

Good, one finding, and the reader has it at the first word:

    Assistants API shuts down in five days

Bad, three findings and no first:

    Slack enters ops, OneCLI monetizes, Assistants API shutdown nears

The last line is the first one dropped when the entry runs out of room, so the weakest finding
stands last.

## Say each fact once

A fact belongs to one line of the entry, and a number rides on the line that first states its fact.
Read the entry against itself before finishing it: the title against the summary, the summary
against every line, and every line against every other line.

Bad, one fact spread over three lines:

    title:    QuickBooks syncs blocked on missing realm
    summary:  Reconnect the affected QuickBooks account or apply its realm-specific binding
    line:     QuickBooks account needs reconnection or realm-specific binding

Good, the same report, each line carrying its own fact:

    title:    QuickBooks has not synced since Tuesday
    summary:  Nine data feeds are stalled. Reconnect the account.

A line that widens another line says what that line said: "Workspace agents face competition", under
a title that already named the competitor, is the title again in slower words.

## Every claim is the report's, said in the reader's words

Every claim in the entry is one the report makes. The words are yours to choose: say the finding the
way this reader would say it, and a plain word standing in for the report's own term leaves the
claim exactly as true.

## One line, one fact, its own number

Each line states one fact and the number the report gave for it, and stops there.

Bad, a second clause the reader did not need:

    Deploy time nearly doubled — median merge-to-production rose from 26 to 41 minutes
    this quarter, all of it in one stage

Good:

    Deploys slowed from 26 to 41 minutes

The report has done the work of stating the magnitude, so the line carries the number. The reason
behind it is why the reader opens the report.

## Name who did it

When the report says a person did a thing, that person's name goes in that line's `actor` and comes
out of the line itself: the entry draws the name itself, and a reader looks for their own name and
their colleagues' first. A report attributes in whatever shape its author chose — a name in
parentheses at the end of a line, a name opening a sentence — so read both.

    report:  **Folder sync reads markdown notes**, a repository syncs nightly (Priya Raman)
    text:    Folder sync reads markdown notes each night
    actor:   Priya Raman

Put a name there only where the report names a person for that finding. Leave it empty for a
company, for a team, and for a report about other companies' moves.

## The summary earns its place

Write a summary when a fact is left that the title and the lines do not carry, and where the reader
must do something, write the ask.

    Nine data feeds are stalled. Reconnect the account.

One finding. A summary joined by a semicolon or by "while" is two summaries, and the second one is a
line.

## Budgets

Count words, and stay under. A field over its budget is cut where it runs out, so words past the
budget are words no reader sees.

| Field | Budget |
| --- | --- |
| `title` | 10 words, one finding |
| `summary` | 15 words, or empty |
| `text` | 10 words |
| `actor` | the person's name, nothing else |

## Every field is words, not markup

A field carries prose. A markdown link, a heading mark, a code span or a bullet character written
into one arrives wherever the entry is drawn as those characters, never as a mark the reader sees
applied. Carry the words alone, links included: the reader reaches the report itself, so the entry
cites nothing.

## Bounds

At most two lines, and fewer when the report holds fewer. Every claim traces to a finding the report
states, so a thin report earns a thin entry.
