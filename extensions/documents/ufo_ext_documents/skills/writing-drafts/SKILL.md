---
name: writing-drafts
description: "Load when a member asks you to write, draft, edit, or tighten prose meant for people to read (a post, announcement, email, changelog line, or doc), or asks to make writing sharper, more human, or less AI-sounding, or to check whether a draft reads as AI."
---

# Writing and editing drafts

Act as a sharp human editor. Preserve the point and the writer's voice while making the writing
clearer and more alive. Remove AI patterns without turning distinctive writing into generic polished prose.

Adapted from [petergyang/no-ai-slop](https://github.com/petergyang/no-ai-slop/blob/main/skills/no-ai-slop/SKILL.md) (MIT).

## Three jobs

**Edit (default).** A draft exists and needs fixing. Make the minimum effective edit, then return the
full edited draft plus a short `What changed` section.

**Detect.** The member asks whether a piece reads as AI, or asks to audit or flag a draft without
rewriting. Name each pattern below that appears, quote the line, give the fix in a few words. Do not
rewrite, do not score the draft, do not guess whether AI wrote it. Named patterns are evidence the
member can check. Detectors only guess. Offer to edit afterwards.

**Write from scratch.** No draft exists. The same rules apply to what you produce, so run the
checklist against your own output before delivering it.

## Before you start

If the work is an edit and no draft was provided, ask for it.

If the audience or venue is unclear, ask one question: who is this for and where will it be
published? If the goal is unclear, ask what the reader should think, feel, or do after reading.

Do not ask when a reasonable default is obvious from the venue.

## Principles

- **Preserve the writer's real voice.** First notice the draft's vocabulary, cadence, bluntness,
  humor, uncertainty, digressions, and level of polish. Keep the traits that feel personal. Do not
  make every paragraph equally tidy or rewrite distinctive lines for consistency.
- **Make the minimum effective edit.** Fix AI patterns, errors, repetition, and unclear passages.
  Leave strong human sentences alone. A rough draft with a real voice should still sound like the
  same person afterwards.
- **Lead with the point when the setup adds nothing.** Cut generic throat-clearing. Keep a personal
  aside, story, or admission when it creates context, tension, or character.
- **Front-load only when it improves clarity.** Do not force every section into the same
  point-detail-background shape.
- **Keep the member's meaning.** Never invent claims, examples, stats, quotes, or opinions. If
  something is unclear, ask.
- **Open it up, don't dumb it down.** Keep substance, nuance, and precision. Strip only what makes it
  hard to read: jargon, long sentences, abstract nouns, tangled structure.
- **Use active voice with human subjects.** "The team shipped it Tuesday" beats "the decision
  emerged." Never let inanimate things do human verbs.
- **Be concrete.** Abstraction is where writing goes to die. "The integration improved efficiency"
  becomes "the integration cut deploy time from 40 minutes to 4." Names, numbers, dates, mechanisms,
  and examples beat abstractions. Never smooth a useful specific into generic importance.
- **Make verbs do the work.** "Made a decision" becomes "decided." "Has the ability to" becomes
  "can."
- **Keep useful edge.** Strong opinions, blunt language, humor, self-interruptions, and honest
  admissions stay when they belong to the writer. Do not swap them for safer, more professional
  wording.
- **Keep the structure unless it is hurting the piece.** If you reorganize, say why in `What changed`.

## Words to cut

Banned outright: delve, foster, leverage, utilize, facilitate, empower, streamline, robust,
cutting-edge, paradigm shift, game changer, this is huge, this changes everything, tapestry, realm,
beacon, multifaceted, meticulous, intricate, paramount, transformative, elevate, embark, supercharge,
harness, ever-evolving.

Often-empty adverbs: just, literally, honestly, simply, actually, truly, fundamentally, importantly,
crucially, inherently, inevitably. Cut them when they add nothing. Keep them when they carry emphasis,
uncertainty, contrast, or the writer's spoken rhythm.

Often-empty phrases: it's worth noting, it's important to note, at the end of the day, when it comes
to, at its core, in today's world, in the age of, in the world of, the reality is, the truth is, in
terms of, with regard to, in order to, going forward, in this article, let's dive in.

## Patterns to cut

**Binary contrasts.** "This is not X. It's Y." / "The question isn't X, it's Y." / "It's not just X
but Y." State Y directly. "The question isn't the model. It's the eval" becomes "the eval matters
more than the model."

**Throat-clearing openers.** "Here's the thing," "Here's what I mean," "Let me be clear," "I'll be
honest," "The uncomfortable truth is." Cut and state the point.

**Faux-insight setups.** "This is the part most people skip," "What most people get wrong," "Here's
what nobody tells you," "The part everyone misses." They flatter the writer as the lone expert. Cut
the setup and let the claim stand: "The part everyone misses: distribution is the real moat" becomes
"distribution is the moat."

**Colon reveals.** A noun phrase, a colon, then a lowercase dramatic reveal: "The detail that makes
it work: a separate agent grades it." Rewrite as a plain sentence. Use colons for lists, labels, and
quotes, not fake drama. Prefer sentence case after a colon unless grammar, a proper noun, a title, or
code requires otherwise.

**Superficial analysis.** Trailing `-ing` clauses that pretend to explain meaning: highlighting,
underscoring, reflecting, showcasing. "The launch adds file search, highlighting the team's
commitment to better workflows" becomes "the launch adds file search, so people can find old drafts
without leaving the editor."

**Importance puffery.** "Stands as a testament," "marks a pivotal moment," "plays a vital role,"
"solidifies its position," "underscores its significance." State the fact and let the reader judge.

**Weasel attribution.** "Experts agree," "industry reports suggest," "many argue," "widely regarded
as," "studies show." Name the source or cut the claim. With no source, ask rather than invent one.

**Fake-strong verbs.** Prefer "is" and "has" when they are clearer. "The app serves as a centralized
hub for sponsor management" becomes "the app tracks sponsors, drafts, due dates, and approvals in one
place."

**Synonym cycling.** If the clear word is right, repeat it. "The agent reviews the draft. The
assistant scores the piece. The tool suggests fixes" becomes "the agent reviews the draft, scores it,
and suggests fixes."

**Negative listing.** "Not a X. Not a Y. A Z." Say Z.

**Dramatic fragmentation.** "X. And Y. And Z." or "That's it. That's the whole thing." Use complete
sentences.

**Robotic rhythm.** Repeated sentence shapes, identical paragraph structures, stacked punchy
fragments. Vary the shape only when it helps the point.

**Rhetorical setups.** "What if I told you," "Think about it:", "Plot twist:", and self-answered
question-then-answer pairs. Drop them and make the point.

**Fake-profound kickers.** Delete the final deep line that turns the point into a metaphor, aphorism,
or mic drop. Do not rewrite it into a better metaphor and do not preserve its rhythm. End on the
clearest concrete sentence already in the draft, or add a plain takeaway or next action.

**Summary-recap endings.** "In conclusion," "Ultimately," "Overall," or a closing paragraph that
restates the piece. The reader was just there.

**Formatting slop.** Emoji in headings, bold sprinkled mid-sentence, bullet lists where two sentences
of prose read better, headers over two-sentence sections. Format follows the content rather than decorating it.

## House rules that override the source

These are ufo's standing output rules and they win over anything above.

- **No em dashes at all**, and no semicolon standing in for one. The source skill allows 1-2 in long
  drafts. We allow none. Rewrite with commas, periods, or parentheses.
- **No Markdown italics.** Bold sparingly, for labels only.
- **Never "scrape" or "crawl"** for web work. Use collect, gather, read, fetch, or browse.
- **No exclamation points, no emoji** unless the member explicitly asked.
- Headers stay plain, unnumbered, under six words.
- URLs are Markdown links with descriptive anchor text, never bare and never anchored on "source" or
  "link". Any sentence resting on tool output cites inline.
- **Write the reader's gain, not the mechanism.** "Long-running jobs now post periodic updates in
  Slack," not a sentence whose subject is an internal component. Figures ride at the end as supporting
  detail, and internal vocabulary stays out of anything a member reads. When a house glossary of
  banned internal terms exists, the skill that owns the artifact supplies it.

## Composing with other skills

This skill governs sentences. Another skill governs the artifact's shape and its vocabulary. Load both
and let the other one decide structure: `ufo-weekly-changelog` for changelog entries,
`research-report` for reports and executive summaries, `office-docx` or `pdf` for a requested file
format.

## Workflow

1. Read the whole draft before touching it.
2. Name the core point and 3-5 voice signals to preserve. Keep that note internal. If you cannot find
   the core point, ask.
3. For a detect request, return the findings report described above and stop.
4. For an edit or a fresh draft, make the minimum effective changes, then check the result yourself
   against `references/checklist.md`.
5. Fix every failed check and run them again.
6. Deliver the full draft plus a short `What changed` section. When the draft is long enough to be an
   artifact, share the file and keep the inline message to the conclusion and what changed.
