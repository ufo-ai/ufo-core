# Packaged skills and document automation utilities  `stage-17`

This stage is a toolbox of packaged “skills,” meaning add-on abilities, and small command-line scripts that agents can run when documents need extra work. It is mostly shared behind-the-scenes support, not the main work loop. Think of it as a workshop beside the main system.

The document review scripts keep review findings in a saved state file, then turn those findings into visible comments or highlights in PDFs, PowerPoint files, and spreadsheets. The DOCX, PPTX, and XLSX utilities work with Microsoft Office files without opening the normal apps for a user. They can unpack files into editable parts, add comments, repair presentations, recalculate spreadsheet formulas, and pack files back up. The PDF helpers cover common PDF jobs: filling real form fields, placing text on form-like pages, and rendering pages as images.

Finally, the skill discovery and sample probe pieces help the system recognize skill packages, list community skills, fetch their descriptions, and check that a sample skill is installed and runnable. Together, these parts let agents inspect, modify, validate, and present documents safely inside the sandbox.

## Sub-stages

- [Document review state and annotation scripts](stage-17.1.md) `stage-17.1` — 7 files
- [Office DOCX document utilities](stage-17.2.md) `stage-17.2` — 4 files
- [Office PPTX presentation utilities](stage-17.3.md) `stage-17.3` — 5 files
- [Office XLSX spreadsheet utilities](stage-17.4.md) `stage-17.4` — 3 files
- [PDF form, layout, and rendering helpers](stage-17.5.md) `stage-17.5` — 3 files
- [Skill discovery, package markers, and sample probes](stage-17.6.md) `stage-17.6` — 3 files

## 📊 State Registers Touched

- `reg-pack-composition` — The selected bundle of built-in extensions, prompts, skills, jobs, and setup steps for this deployment.
- `reg-host-environment` — The assembled per-turn world given to the agent: prompts, skills, files, model choice, tools, and extension context.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-sandbox-state` — The remembered sandbox handles and execution environments where commands, files, and risky work run safely.
- `reg-skill-library-cache` — Cached skill-package metadata, community skill listings, fetched descriptions, and probe results used when resolving skills for agents.
- `reg-document-review-state` — Saved document-review findings and annotation state files used by PDF, PowerPoint, Word, and spreadsheet automation utilities.
