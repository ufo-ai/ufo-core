---
name: research-report
description: Load when delivering research findings as a report or markdown document. The default research output format unless the user explicitly requests another.
---
# Markdown Report Instructions

Markdown report artifacts produce research reports in standard GitHub-Flavored Markdown (GFM) format with inline citations.

### Output File

**Always write the report to a file with a `.md` extension.**

- Derive the filename from the query topic: `<topic>.md`
- Use lowercase kebab-case for filenames
- Write the file to the workspace directory using the file writing tool
- After writing, share the file with the user so they can view the rendered report
- The chat response should contain a brief summary — the full report lives in the `.md` file

### Content Format

**Reports use standard GitHub-Flavored Markdown (GFM) supporting:**

- Standard Markdown (headings, paragraphs, lists, emphasis, links, code blocks)
- Markdown tables for comparisons and structured data
- Inline citations as markdown links matching search result URLs
- Embedded images and charts (see **Embedding Images** below)
- No Mathpix Markdown — plain GFM only (LaTeX math expressions are allowed; see `<mathematical_expressions>`)

---

### Embedding Images

**When your research includes generated charts, plots, or other images, embed them directly in the report using relative paths:**

```markdown
![descriptive-name.png](./descriptive-name.png)
```

**Image embedding rules:**

- First, generate and save the image file to the workspace (e.g., using `bash` (python) to create a chart with matplotlib/plotly and save it as a `.png` file)
- Reference the image in the `.md` report using the **relative path** `./filename.ext` — the system will replace these with permanent URLs when the report is shared
- Use descriptive filenames that reflect the content (e.g., `revenue-growth-chart.png`, `market-share-comparison.png`)
- Supported image formats: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.svg`
- Place images at contextually appropriate locations in the report — after the paragraph that discusses the data the image visualizes
- Always include meaningful alt text as the image label: `![Revenue Growth 2020-2025](./revenue-growth.png)`
- Do NOT use absolute paths or URLs for workspace images — always use the `./filename` format

**Example workflow:**

1. Generate a chart: use `bash` (python) to create `revenue-chart.png` in the workspace
2. In the `.md` report, embed it:

   ```markdown
   ## Revenue Analysis

   The company's revenue has grown steadily over the past five years, with a notable acceleration in Q3 2025.

   ![Revenue Growth 2020-2025](./revenue-chart.png)

   As shown above, the year-over-year growth rate increased from 12% to 23%...
   ```

3. When the report is shared via `share_file`, the image path is automatically resolved to a permanent URL

---

### Content Separation

**The report contains ONLY research findings, analysis, and evidence.**

- Direct answers to the user's question go in the chat response, NOT in the report
- The report is a standalone reference document — it should be comprehensible without the chat context
- Think of the chat response as the executive summary and the report as the full analysis

---

### Report Structure

**The model determines appropriate structure based on topic, purpose, and complexity.**

**Common report components:**

- **Title** (H1 heading)
- **Executive Summary / Overview** (brief synthesis of key findings)
- **Body sections** (organized by topic, theme, or argument — use H2/H3 headings)
- **Analysis / Discussion** (interpretation, trade-offs, implications)
- **Conclusion** (summary of findings and actionable takeaways)

**Structure guidelines:**

- Use H1 (`#`) for the report title only
- Use H2 (`##`) for major sections
- Use H3 (`###`) for subsections within major sections
- Do not skip heading levels (e.g., H1 directly to H3)
- Structure emerges from content and purpose — do not force a rigid template

---

### Citation System (MANDATORY FOR RESEARCHED TOPICS)

#### When to Cite

**ALWAYS include citations when:**

- Topic was researched (tool calls made)
- Report contains factual claims from sources
- Making claims about data, statistics, research findings
- Describing events, discoveries, or developments

**Optional/no citations when:**

- Personal writing or opinion pieces
- Creative writing
- Templates or blank forms
- User explicitly states "no citations needed"

#### Citation Format

**Use inline markdown links where the anchor text is the source name, publication, or a natural descriptive phrase — never a generic word like "source" or "link", and never a raw URL.**

To ensure accuracy and avoid hallucinations, only use URLs that are present in your tool outputs. Your text must read naturally even if all URLs were removed.

**Inline citations:**

```markdown
Recent research shows significant AI advances ([Nature](https://...)). Multiple studies confirm this trend ([MIT Technology Review](https://...)), consistent with [Stanford HAI findings](https://...).
```

**In tables:**

```markdown
| Method   | Accuracy | Source                      |
| -------- | -------- | --------------------------- |
| Method A | 95.2%    | [Paper Title](https://...)  |
| Method B | 93.8%    | [Journal Name](https://...) |
```

**Citation rules:**

- Place citations immediately after the claim or fact as inline markdown links
- Multiple sources: cite each naturally within the sentence
- Aim for 1-3 citations per substantive claim
- Distribute citations throughout — maintain consistent citation density from beginning to end
- All citations are inline — never include a bibliography or references section
- Only cite actual sources from search results — never fabricate citations or URLs

---

### Markdown Tables

**Use markdown tables for comparisons and structured data:**

```markdown
| Feature | Option A | Option B | Option C   |
| ------- | -------- | -------- | ---------- |
| Price   | $10/mo   | $25/mo   | $50/mo     |
| Storage | 10 GB    | 50 GB    | 200 GB     |
| Support | Email    | Chat     | 24/7 Phone |
```

**Table guidelines:**

- Use tables when comparing 2+ entities across shared attributes
- Use tables for structured data with clear rows and columns
- Prefer tables over long prose when readers need to compare values side-by-side
- Keep tables focused — if a table exceeds 6-7 columns, consider splitting
- Include citations in table cells where data comes from sources

---

### Writing Principles

**STRUCTURE:**

- Lead with the direct answer, then provide supporting context
- Use paragraphs of 3-8 sentences for most content
- Never use first-person pronouns ("I," "my," "we," "our") or self-referential phrases

**LISTS:**

- Use bullet points when information is naturally list-like: options, features, pros/cons, steps, recommendations, or any set of 3+ parallel items
- Lists improve scannability—use them when readers may want to skim or reference specific points
- For extended analysis or argumentation where logical flow matters, prose is clearer

**TABLES:**

- Use tables when comparing 2+ entities across shared attributes (e.g., products, companies, plans, tools)
- Use tables for structured data with clear rows and columns (specs, pricing tiers, feature matrices)
- Prefer tables over long prose when the reader needs to compare values side-by-side
- Keep tables focused—if a table exceeds 6-7 columns, consider splitting or simplifying

**HEADINGS:**

- Use headings to signal major topic shifts
- Not every section needs a heading—use judgment based on length and complexity
- Simple answers may need no headings at all

**BREVITY:**

- Match length to query complexity—simple questions get short answers
- Avoid restating information in different words
- Omit introductory preamble when you can lead with the answer directly

**LOGICAL FLOW:**

- Introduce concepts before building on them
- Make transitions explicit ("Building on this...", "This raises the question of...", "In contrast...")
- Ensure conclusions synthesize the analysis, drawing key threads into actionable insights

**ANALYSIS:**

- Lead with conclusions, then support with evidence
- Analyze rather than summarize: explain causation, trade-offs, and what makes information actionable
- When sources conflict, state the disagreement, evaluate source quality, and justify your conclusion
- Apply analytical frameworks when relevant (e.g., Porter's Five Forces, SWOT)
- Anticipate the "so what?"—help users understand why information matters and how to apply it

**RELEVANCE:**

- Keep the user's core question as your north star throughout
- When exploring related topics, connect them back to the main question
- Anticipate follow-up questions and address them proactively

---

### Mathematical Expressions

<mathematical_expressions>
Wrap mathematical expressions such as \(x^4 = x - 3\) in LaTeX using \( \) for inline and \[ \] for block formulas. When citing a formula to reference the equation later in your answer, add equation number at the end instead of using \label. For example \(\sin(x)\) [1] or \(x^2-2\) [4]. Never use dollar signs ($ or $), even if present in the input. Never include citations inside \( \) or \[ \] blocks. Do not use Unicode characters to display math symbols.
</mathematical_expressions>
Treat prices, percentages, dates, and similar numeric text as regular text, not LaTeX.

---

### Vocabulary Calibration

Before writing, assess the user's knowledge level from their query vocabulary and sophistication, then calibrate:

- **Expert users**: Use precise domain language without explanation
- **Intermediate users**: Use technical terms with brief inline context
- **General users**: Define jargon on first use

---

### Length Calibration

The research process is always comprehensive. The output length adapts to user intent:

- **Concise/summary requests** ("Brief overview of..." / "Summarize..." / "Quick answer..."): Concise output (5-10 paragraphs) despite thorough underlying research. Distill findings into the most essential points.
- **Fact-seeking queries** ("What is X?" / "When did Y happen?"): Direct answer with rich context, 5-10 paragraphs.
- **Comparison/ranking requests** ("Compare the top 5..." / "Best options for..."): Structured analysis, 20-40+ paragraphs. Prefer tables over lengthy prose.
- **Open-ended research** ("Analyze..." / "Explain the history and implications of..."): 20-40+ paragraphs.
- **Explicit depth requests** ("Comprehensive report..." / "Deep dive..."): Length determined by topic scope with no upper limit.
- **All other queries**: Default to comprehensive output. When in doubt, provide more depth rather than less.

---

### Source Depth

Prioritize primary and authoritative sources. When citing, prefer reputable sources first: official documentation, peer-reviewed research, established news outlets, government sources, and recognized industry experts over blogs, forums, or unverified sources. Conduct thorough research for all queries:

- Simple factual queries: Search until you find consistent, authoritative answers from multiple sources — do not stop at the first result
- Moderate research: Search until you can provide substantive analysis with multiple perspectives and supporting evidence from 3+ independent sources per key claim
- Complex research (reports, competitive analysis, literature reviews): Search until you have covered all major viewpoints and sub-topics, can support recommendations with evidence, can identify limitations or areas of uncertainty, and have traced key claims to their original sources

Cross-validate important claims across multiple sources. When you find conflicting information, investigate further rather than arbitrarily choosing one source. Identify knowledge gaps explicitly — state what information you could not find or verify.

---

### Domain Structure

Follow conventional structures for the domain when applicable:

- **Academic**: Introduction, Literature Review (if applicable), Methodology, Analysis, Discussion, Conclusion
- **Investment/Market**: Executive Summary, Industry Overview, Competitive Landscape, Financial Analysis, Risks, Conclusion
- **Technical**: Overview, Architecture/Methodology, Analysis/Results, Discussion
- **Policy/Legal**: Summary, Context, Stakeholder Analysis, Evidence Review, Implications, Recommendations

Adapt structure to what the query actually requires—do not force a template onto a simple query.

---

### Quality Checklist

- [ ] Report written to a `<topic>.md` file and shared with user
- [ ] Valid GFM syntax, appropriate heading hierarchy
- [ ] Markdown tables for comparisons and structured data
- [ ] No MMD syntax — plain GFM only (LaTeX math allowed per `<mathematical_expressions>`)
- [ ] **CITATION CHECK (if researched):**
  - [ ] Inline markdown link citations present for factual claims
  - [ ] No bibliography or References section
  - [ ] Citations use source names as anchor text, not generic words
  - [ ] 1-3 citations per substantive claim
  - [ ] Consistent citation density throughout
  - [ ] All URLs come from actual search results (not invented)
- [ ] **IMAGE CHECK (if charts/plots generated):**
  - [ ] Images saved to workspace before referencing in report
  - [ ] Images embedded using `![alt text](./filename.png)` relative path format
  - [ ] Alt text is descriptive and meaningful
  - [ ] Images placed at contextually appropriate locations
- [ ] No first-person pronouns
- [ ] Report is standalone — comprehensible without chat context
- [ ] Report structure appropriate for topic and purpose
- [ ] Appropriate length — matches query complexity per calibration guidelines
- [ ] No TODOs or placeholders — all sections fully written
- [ ] Real data only — never fabricate citations or data
- [ ] Direct answer in chat, detailed analysis in report
