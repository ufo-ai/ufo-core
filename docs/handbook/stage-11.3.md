# Domain-specific extension tools  `stage-11.3`

This stage is the agent’s toolbox for specialized jobs beyond ordinary chat. It sits mostly in the main work loop and shared support layer: when the agent must use the web, manage a long task, edit a document, or support testing, these tools turn broad requests into careful actions.

The web tools act like a browser remote control, search desk, and publishing station. Planning, todos, scheduling, and monitors help the agent track goals, wait for replies or timers, and watch for outside changes. Document review tools store findings and write comments back into PDFs, PowerPoints, and spreadsheets. The Word and PowerPoint packaging scripts open Office files as zipped sets of XML text files, make precise edits or repairs, then rebuild valid documents. Spreadsheet helpers use LibreOffice in the background to refresh formulas. PDF utilities fill forms, place text on pages, and render pages as images. Finally, debugger reporting alerts engineers to serious workspace problems, while fake evaluation connectors give tests a safe, predictable version of services like email and calendar.

## Sub-stages

- [Web browsing, research, and site publishing tools](stage-11.3.1.md) `stage-11.3.1` — 3 files
- [Planning, todos, scheduling, and monitors](stage-11.3.2.md) `stage-11.3.2` — 4 files
- [Document review state and annotation scripts](stage-11.3.3.md) `stage-11.3.3` — 5 files
- [Word DOCX editing and packaging scripts](stage-11.3.4.md) `stage-11.3.4` — 4 files
- [PowerPoint PPTX repair, slide, and packaging scripts](stage-11.3.5.md) `stage-11.3.5` — 4 files
- [Spreadsheet recalculation helpers](stage-11.3.6.md) `stage-11.3.6` — 2 files
- [PDF form, layout, and rendering utilities](stage-11.3.7.md) `stage-11.3.7` — 3 files
- [Debugger reporting and evaluation connectors](stage-11.3.8.md) `stage-11.3.8` — 2 files
