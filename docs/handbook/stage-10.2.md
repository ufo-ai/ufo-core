# Document, office, PDF, and skill helper execution  `stage-10.2`

This stage is a shared workshop for document jobs inside the sandbox. It is not the main chat loop itself. Instead, agents or manual workflows call these helpers when they need to inspect, fix, mark up, or rebuild office files and PDFs.

For review work, the annotation tools keep a simple saved record of findings, then write those findings back as visible comments in PDFs, PowerPoint files, and spreadsheets. For Word files, the DOCX helpers treat the file like a zipped box of structured text: they unpack it, optionally accept tracked changes, add comments, and pack it again. The PowerPoint helpers do the same kind of unpack-and-repack work for PPTX files, with extra tools to add slides, make preview sheets, and repair common package problems. The Excel helpers use LibreOffice in the background to reopen spreadsheets, recalculate formulas, save fresh results, and report obvious errors. The PDF tools render pages as images, inspect page layout, and fill real PDF form fields. Together, these parts act like a document repair bench.

## Sub-stages

- [Document review state and annotation scripts](stage-10.2.1.md) `stage-10.2.1` — 6 files
- [Word DOCX package and comment helpers](stage-10.2.2.md) `stage-10.2.2` — 4 files
- [PowerPoint PPTX package repair and slide helpers](stage-10.2.3.md) `stage-10.2.3` — 4 files
- [Excel XLSX recalculation and LibreOffice helpers](stage-10.2.4.md) `stage-10.2.4` — 2 files
- [PDF rendering, layout, and form filling tools](stage-10.2.5.md) `stage-10.2.5` — 3 files
