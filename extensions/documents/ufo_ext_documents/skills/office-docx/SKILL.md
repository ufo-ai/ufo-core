---
name: office-docx
description: Load when producing or changing a Word document (.docx) — writing one from source notes, styling it, applying edits as tracked changes or comments, flattening a redline, or exporting its pages.
metadata:
  depends:
  - design-foundations
---
# Word Document Skill

Under the hood, .docx is a ZIP container holding XML parts. Creation, reading, and modification all operate on this XML structure.

**Visual and typographic standards:** Single accent color with neutral tones, no decorative graphics, WCAG-compliant contrast. Consult `$UFO_HOME/skills/design-foundations/references/color.md` for the palette and `$UFO_HOME/skills/design-foundations/references/typography.md` for typeface selection. Use widely available sans-serif typefaces like Arial or Calibri as your baseline.

## Choosing an approach

| Objective                      | Technique                                           | Reference                                        |
| ------------------------------ | --------------------------------------------------- | ------------------------------------------------ |
| Create a document from scratch | `docx` npm module (JavaScript)                      | See CREATION.md                                  |
| Edit an existing file          | Unpack to XML, modify, repack                       | See EDITING.md                                   |
| Pull out text                  | `pandoc document.docx -o output.md`                 | Append `--track-changes=all` for redline content |
| Handle legacy .doc format      | `soffice --headless --convert-to docx file.doc`     | Convert before any XML work                      |
| Rebuild from a PDF             | Run `pdf2docx`, then patch issues                   | See below                                        |
| Export pages as images         | `soffice` to PDF, then `pdftoppm`                   | See below                                        |
| Flatten tracked changes        | `python scripts/accept_changes.py in.docx out.docx` | Requires LibreOffice                             |

All tools referenced above (`pandoc`, `soffice`, `pdftoppm`, `docx` npm module, `pdf2docx`) are pre-installed in the sandbox.

## PDF to Word

Start by running `pdf2docx` to get a baseline .docx, then correct any artifacts. Never skip the automated conversion and attempt to rebuild manually.

```python
from pdf2docx import Converter

parser = Converter("source.pdf")
parser.convert("converted.docx")
parser.close()
```

Once you have the converted file, address any problems (misaligned tables, broken hyperlinks, shifted images) by unpacking and editing the XML directly (see EDITING.md).

## Image rendering

```bash
soffice --headless --convert-to pdf document.docx
pdftoppm -jpeg -r 150 document.pdf page
ls page-*.jpg   # always ls to discover actual filenames — zero-padding varies by page count
```

Validation runs automatically when the document is shared. If issues are found, fix the XML and re-share.
