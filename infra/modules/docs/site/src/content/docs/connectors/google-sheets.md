---
title: Connecting Google Sheets
description: Connect Google Sheets to read and update spreadsheets.
---

## Connect Google Sheets

Ask the agent:

> Connect my Google Sheets account.

Open the private authorization control and approve the Google account that can open the target
spreadsheet.

## What ufo can do

- Read cells, ranges, sheets, and spreadsheet metadata.
- Analyze tables and compare them with connected systems.
- Update cells or add rows when you request a change.
- Prepare a corrected spreadsheet or a separate report.

Name the spreadsheet, sheet, range, key columns, and allowed changes.

> Check the `Forecast` sheet for formulas that differ from the rows above them. Report each cell
> first. Do not change the sheet.

For financial work, state the period, currency, and source of truth. Ask for a separate output when
the original must remain unchanged.

## Fix Sheets access

Confirm that the connected Google account can open the spreadsheet. Reconnect if Google removed
the grant. Connect [Google Drive](/connectors/google-drive/) when the task must also search folders
or read non-spreadsheet files.
