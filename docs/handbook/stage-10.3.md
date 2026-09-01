# Browser, Research, Sites, and Document Automation  `stage-10.3`

This stage is the system’s outside-world workbench. It is used mostly during the main work loop, with some shared support behind the scenes, whenever an agent needs to browse the web, research information, publish a site, or inspect and modify documents. The browser pieces form a full remote-control stack: entry-point files define allowed actions, DevTools session code talks to Chrome through its control channel, and page-reading/input code finds buttons, fields, and text before clicking, typing, scrolling, or downloading. Cloud browser and research providers add hosted browsing, web search, and page fetching when a local browser is not the right tool.

The site tools are like a small publishing shop. They copy source files into a safe workspace, track site records, then build, preview, deploy, and publish pages. The document tools cover both viewing and editing. Renderers turn documents or PDFs into page images and text, PDF utilities fill or place form content, and review scripts save issues and write comments back into PDFs, PowerPoints, or spreadsheets. Separate DOCX, PPTX, and XLSX command-line tools unpack, repair, recalculate, annotate, and rebuild Office files. A writing subagent supplies focused prose help when needed.

## Sub-stages

- [Browser Extension Entry Points and Action Contracts](stage-10.3.1.md) `stage-10.3.1` — 5 files
- [Browser DevTools Connection and Session Lifecycle](stage-10.3.2.md) `stage-10.3.2` — 9 files
- [Browser Page Reading, Targeting, and Input Execution](stage-10.3.3.md) `stage-10.3.3` — 8 files
- [Cloud Browser and Web Research Providers](stage-10.3.4.md) `stage-10.3.4` — 4 files
- [Hosted Site Source, Registry, and Publishing Tools](stage-10.3.5.md) `stage-10.3.5` — 4 files
- [Document Rendering and PDF Form/Layout Utilities](stage-10.3.6.md) `stage-10.3.6` — 4 files
- [Document Review State and Annotation Scripts](stage-10.3.7.md) `stage-10.3.7` — 7 files
- [Document Writing Subagent Definition](stage-10.3.8.md) `stage-10.3.8` — 2 files
- [Office DOCX Command-Line Utilities](stage-10.3.9.md) `stage-10.3.9` — 4 files
- [Office PPTX Command-Line Utilities](stage-10.3.10.md) `stage-10.3.10` — 5 files
- [Office XLSX LibreOffice Utilities](stage-10.3.11.md) `stage-10.3.11` — 3 files
