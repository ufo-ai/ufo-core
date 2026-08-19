# Document, office, PDF, spreadsheet, and coding workspace automation  `stage-15`

This stage is shared behind-the-scenes support for working with files inside the agent’s workspace. It gives the system practical tools for documents, spreadsheets, presentations, PDFs, and coding tasks, so the agent can inspect and change real project files during a turn instead of only talking about them.

Its main part is the Office and PDF command script toolbox. These scripts act like a workbench of small specialized tools. Some keep track of a document review: they store the review state, define what counts as an issue, and record outlines, claims, checks, findings, and summaries. Other tools turn those findings into visible comments, highlights, or annotations in PDFs, PowerPoint slides, and Excel sheets.

The file-format tools open up Office documents into editable pieces, change the needed parts, and package them back into usable files. Word, PowerPoint, and Excel each have helpers for repair, cleanup, comments, tracked changes, previews, or formula recalculation. PDF helpers fill forms, inspect pages, write marks, and render pages as images.

## Sub-stages

- [Office and PDF command scripts](stage-15.1.md) `stage-15.1` — 19 files

## 📊 State Registers Touched

- `reg-tool-catalog` — The shared catalog of tools the model can call, including built-in tools, extension tools, connector tools, and their safety labels.
- `reg-tool-execution-context` — The per-turn but shared rulebook passed through tools, saying who the tool acts for, what files, accounts, sandboxes, and subagents it may use.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-artifact-registry` — The saved list of files deliberately shared with users, including ownership, access checks, preview metadata, and download links.
- `reg-workspace-change-log` — The saved record of file changes made during a conversation, used to explain later what the agent changed in the workspace.
- `reg-extension-workflow-state` — Extension-owned durable workflow records that are not just UI slots, such as code-review inboxes, evaluation runs, objectives, pauses, briefs, notes, monitors, triggers, and web-chat state.
- `reg-conversation-workspace-files` — The mutable per-conversation working file tree that tools, skills, document automation, site building, artifacts, and cleanup read or modify before changes are snapshotted or shared.
