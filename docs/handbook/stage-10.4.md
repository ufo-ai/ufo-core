# Document, spreadsheet, presentation, and PDF skill scripts  `stage-10.4`

This stage is a toolbox of one-shot command-line helpers for office documents and PDFs. It is not the main app loop. Instead, sandboxed skills call these tools when they need to open, change, check, or export a user file safely.

The document review tools keep review state in a JSON file, which is structured plain text, then turn findings into visible comments or highlights in PDFs, PowerPoint files, and spreadsheets. The Word DOCX tools treat a Word file as a zipped bundle of XML text files. They can accept tracked changes, unpack the bundle, prepare comments, and pack it back into a working document. The PowerPoint PPTX tools do the same kind of package work for presentations, with extra helpers to add slides, render thumbnails, remove unused parts, or repair known file problems. The Excel XLSX tools use LibreOffice in the background to recalculate formulas and save updated results. The PDF tools fill real form fields, place text on static page layouts, and render pages as images for preview or inspection.

## Sub-stages

- [Document review state and annotation scripts](stage-10.4.1.md) `stage-10.4.1` — 7 files
- [Word DOCX packaging and comment helpers](stage-10.4.2.md) `stage-10.4.2` — 4 files
- [PowerPoint PPTX package, slide, and repair tools](stage-10.4.3.md) `stage-10.4.3` — 5 files
- [Excel XLSX LibreOffice recalculation helpers](stage-10.4.4.md) `stage-10.4.4` — 3 files
- [PDF form, layout, and rendering tools](stage-10.4.5.md) `stage-10.4.5` — 3 files
