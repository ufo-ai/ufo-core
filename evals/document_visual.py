"""Visual-quality cases: the agent builds a real document with the document skills, rasterizes it
to page images, and shares both. A deterministic floor confirms the delivered file is a well-formed
document of its type and that rendered pages were shared; a vision judge then grades those pages for
visual coherence — clipping, overflow, overlap, legibility, and coverage of the request. The images
the judge sees are the skills' own `soffice`/`pdftoppm` render of the exact file, so a visually
broken document (text spilling a cell, slides colliding, a chart cut off) fails on the pixels."""

from evals.harness.capability import CapabilityCase
from evals.harness.scorers import (
    combine,
    office_document_scorer,
    pdf_document_scorer,
    png_image_scorer,
    rendered_pages_scorer,
)

WORKFLOW_WAIT_SECONDS = 900.0

VISUAL_CRITERIA = (
    "No text or graphic is clipped or truncated — including a partial word, address, or value cut "
    "off at a cell, column, box, or page edge; nothing important runs off the visible area.",
    "No line, rule, border, or shape cuts across, overlaps, or is superimposed on the glyphs of "
    "any text, and no two elements collide — a clean underline or a section rule with clear "
    "separation from its text is fine; the failure is a stroke running through letters or elements "
    "touching each other.",
    "Alignment and spacing are precise and consistent: elements sit on a shared grid, columns and "
    "baselines line up, numeric columns are right/decimal-aligned (never centered or ragged), each "
    "column header aligns with the alignment of its data, gaps between comparable elements are "
    "equal, and fills, boxes, and highlight bars have even internal padding with text neither "
    "cramped against an edge nor floating in the fill.",
    "Colors form a restrained, harmonious palette — roughly one accent plus neutrals — with no "
    "clashing, garish, muddy, or arbitrary combinations, and fills or highlights do not fight the "
    "text or each other.",
    "Typefaces are appropriate and professional for the document type: consistent, conventional "
    "fonts at sensible sizes — no gimmicky, decorative, novelty, or wildly mismatched font "
    "choices.",
    "All text is legible — adequate size and clear contrast against its background, with no "
    "garbled or unreadable characters.",
    "The layout reads as finished, professional work: coherent visual hierarchy, deliberate "
    "composition, and clean structure — not amateur, sloppy, cluttered, or thrown together, and "
    "free of placeholder text or rendering artifacts.",
    "The visible content covers what the request asked for — the requested sections, columns, "
    "slides, table, or chart are present and correctly labeled.",
)
CHART_CRITERION = (
    "The chart's axes, category labels, and any legend are readable and fully visible; bars or "
    "shapes are proportioned to their values and not cut off.",
)


def _office(
    name: str, suffix: str, spec: str, extra: tuple[str, ...] = (), max_pages: int = 1
) -> CapabilityCase:
    filename = f"{name}{suffix}"
    stem = name
    fit = (
        (
            " Before converting, set the sheet's print scaling to fit every column on one page — "
            "in openpyxl, `ws.page_setup.fitToWidth = 1`, `ws.page_setup.fitToHeight = 0`, and "
            "`ws.sheet_properties.pageSetUpPr = openpyxl.worksheet.properties.PageSetupProperties"
            "(fitToPage=True)` — so the rendered page shows the whole table as a viewer sees it."
        )
        if suffix == ".xlsx"
        else ""
    )
    message = (
        f"{spec} Create the file at /workspace/{filename}. Then render it to page images so its "
        f"visual quality can be checked: convert it to PDF with `soffice --headless --convert-to "
        f"pdf {filename}`, rasterize with `pdftoppm -png -r 150 {stem}.pdf page`, then run "
        f"`ls page-*.png` to find the generated files.{fit} Share the {suffix} file and every "
        f"page-*.png image with separate share_file calls, then reply 'ANSWER: shared'."
    )
    return CapabilityCase(
        name=name,
        message=message,
        grader=combine(office_document_scorer(suffix), rendered_pages_scorer(1, max_pages)),
        visual_rubric=VISUAL_CRITERIA + extra,
        digest_tag=f"visual:{suffix.lstrip('.')}:{name}",
    )


def _pdf(name: str, spec: str, max_pages: int = 1) -> CapabilityCase:
    filename = f"{name}.pdf"
    message = (
        f"{spec} Create the file at /workspace/{filename}. Then rasterize every page to PNG images "
        f"with `pdftoppm -png -r 150 {filename} page` and run `ls page-*.png` to find them. Share "
        f"the PDF and every page-*.png image with separate share_file calls, then reply "
        f"'ANSWER: shared'."
    )
    return CapabilityCase(
        name=name,
        message=message,
        grader=combine(pdf_document_scorer(), rendered_pages_scorer(1, max_pages)),
        visual_rubric=VISUAL_CRITERIA,
        digest_tag=f"visual:pdf:{name}",
    )


def _image(name: str, spec: str, extra: tuple[str, ...] = ()) -> CapabilityCase:
    filename = f"{name}.png"
    message = (
        f"{spec} Using the tools available in your sandbox (for example Python with Pillow, or "
        f"an SVG rendered to PNG), save it as a PNG image at /workspace/{filename}. Share the "
        f"PNG with share_file, then reply 'ANSWER: shared'."
    )
    return CapabilityCase(
        name=name,
        message=message,
        grader=png_image_scorer(),
        visual_rubric=VISUAL_CRITERIA + extra,
        digest_tag=f"visual:image:{name}",
    )


CASES = (
    _office(
        "memo",
        ".docx",
        "Write a one-page internal memo announcing that every TPS report now requires a new cover "
        "sheet. Include a bold title, a To / From / Date / Re header block, three short body "
        "paragraphs explaining the new cover-sheet requirement in dry corporate prose, and a "
        "closing signature line.",
    ),
    _office(
        "newsletter",
        ".docx",
        "Design a one-page company newsletter titled 'The Office Insider'. Include a title banner "
        "across the top and clearly laid-out sections for: a short staff quiz question (\"What's "
        "your favorite snack?\"), a birthday shout-out for Alex on May 20, and a 'Quote of the "
        "Week'. Lay it out in two columns with a boxed sidebar.",
    ),
    _office(
        "resume",
        ".docx",
        "Write a one-page professional resume for a fictional software engineer. Include a name "
        "header with a contact line, then Experience, Education, and Skills sections with bulleted "
        "entries under each.",
    ),
    _office(
        "kickoff",
        ".pptx",
        "Create a 3-slide project-kickoff deck in the deadpan corporate style of the movie Office "
        "Space: a title slide for the 'TPS Report Cover Sheet Rollout' with a date, an agenda "
        "slide with four dry bullets (the new cover-sheet policy, the memo everyone already got, "
        "minimum flair requirements, the printer situation), and a next-steps slide with three "
        "straight-faced action items. Keep it looking like a real corporate deck, just quietly "
        "absurd.",
        max_pages=3,
    ),
    _office(
        "quarterly",
        ".pptx",
        "Create a 3-slide satirical quarterly-review deck with real, amusing business charts: a "
        "title slide, a slide with a labeled bar/column chart of a tongue-in-cheek metric (e.g. "
        "'Hours Lost to Meetings About Meetings' by quarter), and a slide with a second chart "
        "(e.g. a pie chart of 'How the Workday Is Actually Spent'). Charts must have clear titles, "
        "axes, and category labels.",
        extra=CHART_CRITERION,
        max_pages=3,
    ),
    _office(
        "budget",
        ".xlsx",
        "Build a monthly department budget workbook: a header row of the four quarters, a column "
        "of six spending categories, values in each cell, a bold Total row summing each quarter, "
        "and a Total column summing each category.",
    ),
    _office(
        "pricing",
        ".xlsx",
        "Build a product pricing sheet: columns Product, Unit Price, Quantity, and Subtotal for "
        "eight products, where each Subtotal is a formula multiplying unit price by quantity, plus "
        "a bold grand-total row.",
    ),
    _pdf(
        "invoice",
        "Create a one-page invoice PDF: a company name header, an invoice number and date, a "
        "bill-to address block, a line-item table with description / quantity / unit price / "
        "amount for five items, and a totals section (subtotal, tax, total).",
    ),
    _pdf(
        "flyer",
        "Create a one-page event flyer PDF for a Metalcraft recruiting event in Fresno, CA: a "
        "large title, a date / time / location block naming a plausible Fresno venue, a two-"
        "sentence pitch on who Metalcraft is and the roles they're hiring, and a bold call-to-"
        "action to RSVP or apply.",
    ),
    _image(
        "bar-chart",
        "Create a bar chart titled 'The Fibonacci Sequence' showing the first eight Fibonacci "
        "numbers (1, 1, 2, 3, 5, 8, 13, 21) as bars, with each bar labeled by its position on the "
        "x-axis, the value on the y-axis, axis titles, and a value label on each bar.",
        extra=CHART_CRITERION,
    ),
    _image(
        "org-chart",
        "Create an organization chart: one top box (CEO), three boxes reporting to it "
        "(Engineering, Sales, Operations), and two boxes under Engineering (Frontend, Backend), "
        "with labeled boxes connected by lines.",
        extra=CHART_CRITERION,
    ),
)
