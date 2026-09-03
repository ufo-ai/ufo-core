# Prompt, skill, and environment-document resolution  `stage-7.1`

This stage is shared behind-the-scenes support for each agent turn. It prepares the “reference material” the model will see and makes sure it can be reproduced safely later. The prompt renderer fills in prompt templates, checks that every blank was filled, and records a fingerprint, like a version stamp, so prompt changes are traceable. The delivery register supplies common writing rules for messages sent to users or other agents.

Skills are reusable instruction cards, sometimes with files. The skills runtime defines how they are read, registered, connected to dependencies, and copied into the sandbox, which is the agent’s working area. Skill selection chooses which saved skill cards to show the model on a given turn, so useful abilities are visible without flooding the conversation. The catalog skill builds a live table of available AI models, costs, limits, and features.

Environment documents capture changes to a turn’s prompt, tools, skills, model, and files. They are stored by cryptographic digest, a unique content fingerprint, so the same document can be loaded exactly again, while safety checks prevent documents from adding broader powers than the turn already allowed.

## Files in this stage

### Skill presentation
These files decide which skills the model can see on a turn, including a generated catalog of available deployment models.

### `core/src/ufo/harness/models/catalog_skill.py`

`domain_logic` · `startup`

This file exists to prevent the model documentation from going stale. Instead of keeping a hand-written list of available models, it reads the same model records that the runtime uses when it chooses models, prices requests, and builds prompts. That means the catalog a user sees should match what the system can actually do.

The main idea is simple: at boot time, the system already has a model registry, which is like a current menu of all supported models. This file turns that menu into a readable Markdown table. Each row shows a model’s id, provider, knowledge cutoff, context window, price for input and output tokens, whether it supports reasoning, and which API surface it uses.

It then wraps that table in a RuntimeSkill. A RuntimeSkill is a piece of instructions or reference material the runtime can load and show to the model or user. Here, the skill is named “model-catalog” and its description tells users to load it when they want to choose or compare models.

A small helper formats prices into dollars per million tokens. Without this file, users might rely on outdated external notes or guesses about model availability, costs, and capabilities.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This helper turns a stored price value into a friendly dollar string, such as “$1.25”. It is used so the catalog table shows prices in a form people can quickly read.

**Data flow**: It receives a price stored as an integer number of micro-dollars per million tokens. It divides that by the constant that represents one full US dollar in micro-dollars, formats the result with two decimal places, and returns a string beginning with a dollar sign.

**Call relations**: When model_catalog_skill is building each model row, it calls _per_mtok for the input price and again for the output price. The formatted strings are then inserted into the Markdown table shown in the generated catalog skill.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function creates the actual model catalog skill from the live registry of models. Someone uses it at boot so the deployment can offer a current, trustworthy list of available models and their facts.

**Data flow**: It receives a ModelRegistry, which contains the model specifications known to the running system. It sorts those model specifications by id, turns each one into a Markdown table row, formats prices with _per_mtok, combines the rows with a table header and explanatory text, then returns a RuntimeSkill containing the final instructions and raw skill Markdown.

**Call relations**: This is the main builder in the file. During startup, code that has the live ModelRegistry calls model_catalog_skill to create the catalog. Inside that process it calls _per_mtok to make prices readable, then hands the completed name, description, instructions, and raw Markdown to RuntimeSkill.__init__ to create the skill object the rest of the runtime can load.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### `core/src/ufo/runtime/skills/selection.py`

`domain_logic` · `request handling`

Agents can have many saved skills, each with a name, description, and optional pin. The model needs to see these skills so it can use them, but prompts have limited space. This file is the rulebook for fitting those skill cards into that space.

It uses two places to show skills. If the member’s saved skills are small enough, they are folded directly into the normal system prompt beside built-in deployed skills. If they are too large, they move into a separate <saved_skills> block in the turn message. Think of it like packing for a trip: a small set fits in your pocket, but a larger set needs its own bag.

When the separate block is needed, the file fills it carefully. Pinned skills come first, because the user or system marked them as important. If the whole catalog fits, every skill gets a full name-and-description line. If not, the file chooses a small top group whose names or descriptions match the current query, gives those full descriptions, and still includes the remaining skills by name when possible. If even that is too much, it drops from the end and adds a note saying how many more skills exist and that skill_search can find them.

All of this is pure calculation. It does no disk or network work, and it is designed to be predictable and cheap enough to run every turn.

#### Function details

##### `_query_terms`  (lines 35–42)

```
def _query_terms(query: str) -> tuple[str, ...]
```

**Purpose**: Turns a user query into a clean list of searchable words. It ignores very short words and limits how much text it reads, so a huge pasted query cannot make skill selection expensive.

**Data flow**: It receives a query string. It lowercases it, looks only at the first allowed number of characters, splits it wherever there is punctuation or other non-word text, removes short terms, and keeps only the first copy of each term. It returns those distinct search terms as a tuple.

**Call relations**: This is the shared first step for matching skills to text. lexical_score uses it when scoring one card, and select_top_k uses it once before ranking many cards.

*Call graph*: called by 2 (lexical_score, select_top_k).


##### `_term_hits`  (lines 45–47)

```
def _term_hits(terms: Sequence[str], card: SkillCard) -> int
```

**Purpose**: Counts how many search terms appear in one skill card. This is the basic matching test used to decide whether a skill looks relevant to the current query.

**Data flow**: It receives already-prepared search terms and one SkillCard. It joins the card’s name and description into one lowercase text area, then counts how many terms are found inside it. It returns that count as an integer.

**Call relations**: lexical_score calls this after preparing query terms. It is the small comparison step behind the public scoring helper.

*Call graph*: called by 1 (lexical_score).


##### `lexical_score`  (lines 50–55)

```
def lexical_score(query: str, card: SkillCard) -> int
```

**Purpose**: Gives one skill card a simple relevance score for a query. The score is just how many distinct meaningful query words appear in the card’s name or description.

**Data flow**: It receives a query and a SkillCard. It turns the query into cleaned terms with _query_terms, then asks _term_hits how many of those terms appear in the card. It returns the resulting number.

**Call relations**: This function combines the two lower-level matching helpers into a convenient single-card score. It does not drive the main block rendering directly, but it expresses the same matching rule used by the selection logic.

*Call graph*: calls 2 internal fn (_query_terms, _term_hits).


##### `select_top_k`  (lines 58–66)

```
def select_top_k(query: str, cards: Sequence[SkillCard]) -> tuple[SkillCard, ...]
```

**Purpose**: Chooses the most relevant unpinned saved skills for the current query. It is used when there are too many skills to show every description, so only a few get full detail.

**Data flow**: It receives the query and a sequence of SkillCards. It prepares the query terms once, removes pinned cards from the candidate list, scores each remaining card by term matches, sorts higher-scoring cards first while preserving original order for ties, and returns only the configured top number of cards.

**Call relations**: member_visibility calls this only after deciding the whole catalog will not fit in the saved-skills block. The selected cards become the ones shown with full descriptions, while other unpinned skills may still be shown by name.

*Call graph*: calls 1 internal fn (_query_terms); called by 1 (member_visibility).


##### `skill_line`  (lines 69–71)

```
def skill_line(card: SkillCard) -> str
```

**Purpose**: Formats one skill card as a prompt-friendly line with its name and description. It also caps the line length so one long description cannot take too much room.

**Data flow**: It receives a SkillCard. It builds text in the form “- name: description”, then cuts it off at the configured maximum character count. It returns that one formatted string.

**Call relations**: This is the common formatter used by the prompt and block sizing decisions. folds_into_prompt, prompt_index, catalog_fits, and member_visibility all rely on it so that measuring and rendering use the same shape of text.

*Call graph*: called by 4 (catalog_fits, folds_into_prompt, member_visibility, prompt_index).


##### `folds_into_prompt`  (lines 74–78)

```
def folds_into_prompt(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Decides whether all saved member skills are small enough to go directly into the system prompt. This keeps small skill sets simple while preventing large ones from bloating the prompt.

**Data flow**: It receives a sequence of SkillCards. It formats each card with skill_line, measures the total joined text size with _joined_size, and compares that size to the prompt-fold budget. It returns true if the full set fits there, false otherwise.

**Call relations**: prompt_index calls this before deciding whether to include member skills beside deployed skills. It uses the same line formatter as the later rendering path, so the size check matches what would actually be shown.

*Call graph*: calls 2 internal fn (_joined_size, skill_line); called by 1 (prompt_index).


##### `prompt_index`  (lines 81–93)

```
def prompt_index(registry: SkillRegistry) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the skill index entries that belong in the system prompt for one turn. It always includes deployed skills, and it also includes member saved skills when that member list is small enough.

**Data flow**: It receives a SkillRegistry, which contains deployed skills and member saved cards. It reads the member cards, checks whether they fold into the prompt, and gets the deployed index from the registry. If member cards fit, it appends each member card as a name and capped description; if not, it returns only the deployed index.

**Call relations**: This function is the prompt-side partner to member_block. When saved skills are small, prompt_index carries them; when they are too large, prompt_index leaves them out so member_visibility can place them in the separate saved-skills block instead.

*Call graph*: calls 3 internal fn (index, folds_into_prompt, skill_line).


##### `catalog_fits`  (lines 96–99)

```
def catalog_fits(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Checks whether every saved skill can be shown as a full line inside the separate saved-skills block. If this is true, no relevance ranking is needed.

**Data flow**: It receives skill cards, formats each one with skill_line, measures how large the wrapped block would be with _block_size, and compares that to the member-block budget. It returns true if the whole catalog fits.

**Call relations**: This standalone check mirrors one of the decisions made inside member_visibility. It uses the same formatter and block-size helper so callers can ask the question without rendering the full visibility result.

*Call graph*: calls 2 internal fn (_block_size, skill_line).


##### `member_visibility`  (lines 113–146)

```
def member_visibility(query: str, cards: Sequence[SkillCard]) -> MemberVisibility
```

**Purpose**: Makes the full saved-skill visibility decision for one turn. It decides whether skills fold into the prompt, whether the whole catalog fits in a separate block, and what block text should be sent if needed.

**Data flow**: It receives the current query and the member skill cards. It formats each card once, measures whether the set fits the system prompt and the separate block, and returns an empty block if there are no cards or if they fold into the prompt. If a separate block is needed and the catalog fits, it renders pinned cards first and then the rest. If the catalog is too large, it uses select_top_k to choose described cards, adds remaining names where possible, trims from the end until the block fits, and returns a MemberVisibility record with the decisions and rendered text.

**Call relations**: member_block calls this as its single source of truth. Inside, it brings together the sizing helpers, the line formatter, the top-k selector, and the renderer so the fold decision, catalog decision, and final block all agree with one another.

*Call graph*: calls 5 internal fn (_block_size, _joined_size, _render, select_top_k, skill_line); called by 1 (member_block); 1 external calls (__init__).


##### `member_block`  (lines 149–157)

```
def member_block(query: str, cards: Sequence[SkillCard]) -> str
```

**Purpose**: Returns just the saved-skills text block for a member turn. It is the simple interface for code that only needs the text to attach to the message.

**Data flow**: It receives the current query and skill cards. It asks member_visibility to do the full decision and rendering work, then takes the block field from that result. It returns either an empty string or a complete <saved_skills> block.

**Call relations**: This function is a thin wrapper around member_visibility. It lets the rest of the turn-building flow get the rendered block without separately caring about the fold and catalog-fit flags.

*Call graph*: calls 1 internal fn (member_visibility).


##### `_joined_size`  (lines 163–164)

```
def _joined_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how many characters a group of lines will use when joined with newline characters. It is used for budget checks before text is placed in a prompt or block.

**Data flow**: It receives a sequence of strings. If there are lines, it adds each line length plus one newline character and subtracts the extra newline after the last line; if there are no lines, it returns zero. The output is the calculated character count.

**Call relations**: folds_into_prompt and member_visibility use this to test prompt-fold size. _block_size also builds on it when measuring a full wrapped saved-skills block.

*Call graph*: called by 3 (_block_size, folds_into_prompt, member_visibility).


##### `_block_size`  (lines 167–168)

```
def _block_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how large a saved-skills block would be after adding its opening and closing tags. This prevents the code from forgetting that the wrapper text also consumes prompt space.

**Data flow**: It receives the lines that would go inside the block. It first measures their joined size with _joined_size, then adds the fixed size of the <saved_skills> wrapper and the needed newline spacing. It returns the total character count.

**Call relations**: catalog_fits and member_visibility call this when deciding whether full skill lines fit in the separate block. It depends on _joined_size for the inner line measurement.

*Call graph*: calls 1 internal fn (_joined_size); called by 2 (catalog_fits, member_visibility).


##### `_render`  (lines 171–172)

```
def _render(lines: tuple[str, ...]) -> str
```

**Purpose**: Builds the final saved-skills block text from prepared lines. It wraps the lines between the fixed opening and closing tags that tell the model what this section is.

**Data flow**: It receives a tuple of already chosen text lines. It places the opening tag first, then the lines, then the closing tag, joining everything with newlines. It returns the final string.

**Call relations**: member_visibility calls this after it has decided which lines fit. This function does not choose or trim anything; it only turns the chosen contents into the exact block format.

*Call graph*: called by 1 (member_visibility).


### Environment documents
This file defines, validates, stores, and reloads digest-addressed environment documents and their associated sandbox files.

### `core/src/ufo/host/environment.py`

`domain_logic` · `turn setup and environment load`

An environment document is like a sealed instruction sheet for an experiment. It can say, for example, "use this prompt text," "hide this tool," "change this tool description," "add this sandbox command as a tool," or "replace this skill." The important rule is that these changes can narrow what the platform offers, but they cannot grant new outside powers. A command-based tool runs only inside the turn's own sandbox, so it gets no more access than the sandbox already had. This file describes those allowed changes using Pydantic models, which are Python data classes that also validate incoming data. The validation is strict: unknown fields are rejected, prompt overrides must use exactly one style, disabled tools cannot also be edited, skill replacements must still parse as real skills, file destinations must stay inside the workspace, and digests must look like proper SHA-256 identifiers. The file also turns YAML or JSON author input into a canonical JSON form before hashing it. That means the same document gets the same digest even if one person wrote it as YAML and another as JSON. Finally, it saves and retrieves both documents and referenced files from a workspace blob store, checking hashes on load so corrupted or mismatched stored data is caught instead of silently used.

#### Function details

##### `PromptOverride._one_form`  (lines 59–62)

```
def _one_form(self) -> 'PromptOverride'
```

**Purpose**: This validation step makes sure a prompt override has one clear meaning. It must either replace the whole prompt with new text or describe small text edits, but not both and not neither.

**Data flow**: It reads the already-filled fields of a PromptOverride object. If exactly one of text or replace is present, the object is accepted unchanged. If the object is ambiguous or empty, it raises an error before the document can be used.

**Call relations**: This runs automatically while an environment document is being validated. It protects later prompt assembly code from having to guess whether a prompt should be fully replaced or edited in place.


##### `ToolOverride._one_meaning`  (lines 91–104)

```
def _one_meaning(self) -> 'ToolOverride'
```

**Purpose**: This validation step makes sure a tool override says one sensible thing. A tool can be hidden, rewritten, or defined as a sandbox command, but invalid mixtures are rejected.

**Data flow**: It inspects the fields of a ToolOverride object: enabled, description, parameters, input, and run. It either returns the same object when the combination is allowed, or raises an error explaining the bad combination.

**Call relations**: This runs automatically when tool override data is parsed. It keeps the rest of the system from receiving unclear instructions such as a disabled tool that also has a new description, or a command tool that also tries to rewrite an existing parameter schema.


##### `EnvironmentDocument._entries_parse`  (lines 150–171)

```
def _entries_parse(self) -> 'EnvironmentDocument'
```

**Purpose**: This validation step checks the parts of an environment document that need deeper inspection: skill replacements and sandbox file entries. It ensures skills are valid and file paths and digests are safe before any turn uses them.

**Data flow**: It reads the document's skills and files. Full skill text is parsed as a real skill; each file destination is checked to be a relative path inside the workspace; each file digest is checked against the expected digest pattern. If everything is safe and well-formed, the document is returned unchanged; otherwise validation stops with an error.

**Call relations**: This is called automatically during EnvironmentDocument validation, including when documents are parsed from user-provided YAML or loaded from storage. It calls the skill parser to verify skill text, the containment helper to prevent paths escaping the workspace, and the digest pattern checker to reject malformed stored-file references.

*Call graph*: 3 external calls (contained_relative, parse_skill_content, fullmatch).


##### `parse_environment_document`  (lines 174–190)

```
def parse_environment_document(body: bytes) -> tuple[EnvironmentDocument, bytes, str]
```

**Purpose**: This function turns a user-written YAML or JSON environment document into a validated EnvironmentDocument, canonical stored bytes, and the SHA-256 digest that identifies those bytes. Use it when accepting a new document before saving or applying it.

**Data flow**: It takes raw bytes as input. First it rejects documents over the size limit, then parses the bytes as YAML, validates the resulting data as an EnvironmentDocument, converts that validated object into sorted compact JSON, hashes those canonical bytes, and returns the document, the canonical bytes, and a digest string.

**Call relations**: store_environment_document calls this before writing a document to the blob store. Inside, it relies on YAML parsing for input, JSON serialization for the canonical form, and SHA-256 hashing for the stable content address.

*Call graph*: called by 1 (store_environment_document); 3 external calls (sha256, dumps, safe_load).


##### `store_environment_document`  (lines 193–196)

```
async def store_environment_document(blob: WorkspaceBlobStore, body: bytes) -> str
```

**Purpose**: This function saves an environment document in the workspace blob store and returns the digest that can later pin it exactly. It is the write path for reusable environment documents.

**Data flow**: It receives a blob store and raw document bytes. It parses and canonicalizes the document, then writes the canonical bytes under a key based on the document's SHA-256 digest. Its output is the digest string callers can store on a turn configuration.

**Call relations**: It is a thin storage wrapper around parse_environment_document. After parsing succeeds, it hands the canonical bytes to WorkspaceBlobStore.put so later turns can load the exact same document by digest.

*Call graph*: calls 2 internal fn (put, parse_environment_document).


##### `load_environment_document`  (lines 199–205)

```
async def load_environment_document(blob: WorkspaceBlobStore, digest: str) -> EnvironmentDocument
```

**Purpose**: This function retrieves a previously stored environment document by digest and proves the stored bytes still match that digest. It prevents a turn from accidentally using the wrong or corrupted document.

**Data flow**: It takes a blob store and a digest string. It first checks that the digest is well-formed, fetches the stored canonical JSON bytes, hashes them again, compares that hash to the requested digest, and then validates the JSON as an EnvironmentDocument. The result is the usable document object.

**Call relations**: This is the read path that turn setup code can use when a turn points to an environment document digest. It calls WorkspaceBlobStore.get for the bytes, uses the shared digest pattern for input checking, and uses SHA-256 to verify content integrity before returning the document.

*Call graph*: calls 1 internal fn (get); 2 external calls (sha256, fullmatch).


##### `store_environment_file`  (lines 208–217)

```
async def store_environment_file(blob: WorkspaceBlobStore, body: bytes) -> str
```

**Purpose**: This function saves a raw file that an environment document may later place into a turn's sandbox. Unlike documents, it does not parse the file; it stores the exact bytes by digest.

**Data flow**: It receives a blob store and raw file bytes. It rejects files over the size limit, computes a SHA-256 digest of the bytes, stores those bytes under an environment-file key based on the digest, and returns the digest string.

**Call relations**: This is used when a document needs to reference an uploaded file. It hands the raw bytes to WorkspaceBlobStore.put and returns the digest that can be written into the document's files map.

*Call graph*: calls 1 internal fn (put); 1 external calls (sha256).


##### `load_environment_file`  (lines 220–224)

```
async def load_environment_file(blob: WorkspaceBlobStore, digest: str) -> bytes
```

**Purpose**: This function retrieves a raw environment file by digest and verifies that the bytes still match. It gives turn setup code the exact file contents to write into the sandbox.

**Data flow**: It takes a blob store and a digest string, fetches the stored bytes from the environment-file area, hashes those bytes, and compares the result with the requested digest. If they match, it returns the bytes; if not, it raises an error.

**Call relations**: This is the read side of store_environment_file. When a validated environment document names a file digest, later setup code can call this to fetch the content safely before placing it in the workspace sandbox.

*Call graph*: calls 1 internal fn (get); 1 external calls (sha256).


### Prompt rendering
These files make prompt code importable, render final system prompts from templates, fingerprint them, and centralize shared delivery rules.

### `core/src/ufo/runtime/prompts/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can contain an `__init__.py` file to show that the folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may hold useful things, but this label mainly tells Python where the drawer is and what name to use for it.

Here, the drawer is `ufo.runtime.prompts`, which likely contains code or data related to prompts used at runtime. Because this file is empty, it does not set up defaults, expose helper functions, or run any startup code. Its value is structural: it helps keep the project’s import paths predictable and makes this directory part of the larger `ufo.runtime` package layout.

Without this file, depending on the Python version and packaging setup, imports from this folder might be less explicit or could fail in environments that expect traditional package markers.


### `core/src/ufo/runtime/prompts/render.py`

`domain_logic` · `prompt construction before a model turn`

A system prompt is the instruction sheet the model reads before doing any work. In this project, that instruction sheet is assembled from several pieces: a core shell, the agent’s own instructions, optional skills, workspace-specific sections, citation rules, delivery rules, and the model’s knowledge cutoff date. This file is the prompt assembly station.

It reads shared prompt text files from disk when the module loads, then exposes functions that combine those pieces safely. The important safety rule is: placeholders must never accidentally reach the model. Placeholders look like {{name}}. If an agent prompt declares a variable, the caller must provide it. If the caller provides an extra variable, that is also an error. After the full prompt is assembled, the file checks again for any leftover {{...}} slots and fails loudly if it finds one.

This matters because a half-rendered prompt can confuse the model or hide a configuration bug. It is like printing a form letter that still says “Dear {{customer_name}}”: the mistake should be caught before it is mailed.

The file also formats optional blocks, such as available skills or workspace capabilities, so empty sections simply disappear instead of producing meaningless empty tags.

#### Function details

##### `rendered_prompt`  (lines 61–62)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This function wraps finished prompt text together with a stable digest, which is a short fingerprint of the exact content. The digest lets logs and observability tools tell when the prompt text changed, even if the prompt itself is large.

**Data flow**: It receives the final prompt text as a string. It turns that text into bytes, computes a SHA-256 hash, prefixes it with "sha256:", and returns a RenderedPrompt object containing both the digest and the original content. It does not change any outside state.

**Call relations**: After render_template has filled and checked the prompt, it calls rendered_prompt as the final packaging step. rendered_prompt then creates the RenderedPrompt value that the rest of the runtime can send to the model and record for tracing.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 65–82)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: This is the main entry point for building the primary agent’s system prompt. It combines the standard shell prompt with the agent instructions, contributed sections, skill list, and the model’s knowledge cutoff date.

**Data flow**: It receives the agent prompt text, section blocks, optional skill descriptions, and a knowledge cutoff like "2026-02". It converts that machine-readable date into a human-readable month and year, places it into the knowledge-cutoff wording, inserts that block into the shell template, and passes everything to render_template. The result is a RenderedPrompt containing the complete prompt and its digest.

**Call relations**: Code that needs the main agent prompt calls this function rather than assembling the shell by hand. render_system_prompt prepares the special knowledge cutoff piece, then hands the broader fill-and-validate work to render_template.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 85–103)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This function fills a prompt template and makes sure the result is complete. It is the central safety gate that prevents unresolved {{placeholder}} text from being sent to the model.

**Data flow**: It receives a template, an agent prompt, a mapping of variable names to values, a list of skills, and a list of section blocks. First it asks _substitute_vars to fill variables inside the agent prompt. Then it replaces the known template slots for skills, citations, sections, and the agent prompt. It checks for any leftover {{name}} placeholders, collapses overly large blank gaps, trims the end, and returns the packaged RenderedPrompt. If something is missing or misplaced, it raises an error instead of returning bad prompt text.

**Call relations**: render_system_prompt calls this function to do the actual assembly. Inside, render_template relies on _substitute_vars for agent-prompt variables, render_skill_index for the available-skills block, and rendered_prompt for the final prompt-plus-digest wrapper.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_workspace_facts`  (lines 115–128)

```
def render_workspace_facts(lines: Sequence[str]) -> str
```

**Purpose**: This function turns a list of already-available workspace capabilities into one prompt block. It helps the model know what is already set up so it does not suggest setting it up again.

**Data flow**: It receives a sequence of plain text lines, each describing one workspace capability. If the list is empty, it returns an empty string so no pointless section appears. Otherwise, it wraps the lines in a <workspace_capabilities> block and adds one shared closing sentence: "Already set up — do not offer again."

**Call relations**: This helper is used when workspace-related prompt sections are being prepared. It does not call other functions in this file; instead, it produces a ready-made section that can later be included among the sections passed into the prompt renderer.


##### `render_object_kinds`  (lines 134–151)

```
def render_object_kinds(kinds: Sequence[tuple[str, str, Sequence[str]]]) -> str
```

**Purpose**: This function describes the kinds of workspace objects the current turn can talk about, plus the actions available for each kind. It gives the model a compact menu of objects it may refer to or operate on.

**Data flow**: It receives object-kind entries made of a name, a description, and a list of actions. If there are no kinds, it returns an empty string. Otherwise, it writes one line per kind and, when actions exist, an indented action list below that kind. It wraps the whole result in a <workspace_objects> block.

**Call relations**: This helper prepares a prompt section for object-aware turns. The section it returns can be combined with other capability sections before render_template inserts them into the final system prompt.


##### `render_skill_index`  (lines 154–163)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This function formats the list of loadable skills into a prompt block. It tells the model which extra skills are available and what each one is for.

**Data flow**: It receives a sequence of skill name and description pairs. If the list is empty, it returns an empty string. Otherwise, it creates an <available_skills> block with one bullet per skill, using the name and description from each pair.

**Call relations**: render_template calls this when it reaches the {{skill_index}} slot in a prompt template. render_skill_index supplies the formatted block, and render_template places that block into the final prompt.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 166–173)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This function fills variables inside the agent prompt while checking both sides strictly. It exists to catch mistakes early, such as forgetting to provide a value or providing a value that the prompt never asked for.

**Data flow**: It receives a prompt template string and a mapping of variable names to replacement text. It scans the template for placeholders like {{user_name}}, compares those declared names with the supplied mapping keys, and raises an error if anything is missing or extra. If the sets match exactly, it replaces each placeholder with its supplied value and returns the filled text.

**Call relations**: render_template calls this before inserting the agent prompt into the larger shell. By doing this first, render_template can trust that the agent-specific instructions are complete before it checks the whole finished prompt for leftover slots.

*Call graph*: called by 1 (render_template).


### `core/src/ufo/runtime/turns/delivery_register.py`

`config` · `prompt assembly`

This file is a small but important source of house rules for agent output. Its main job is to read a Markdown file called `delivery_register.md` and expose that text as `DELIVERY_REGISTER_BLOCK`, so other parts of the system can insert the same instructions into prompts. Think of it like a shared style card pinned beside every workspace: each agent sees the same rules before handing over its final answer.

It also sets size limits and wording guidance for direct prose results and subagent results. A subagent is an agent working under another agent, like an assistant reporting back to a project lead. The `SUBAGENT_RESULT_DESCRIPTION` tells that subagent to provide exactly one parent-visible result, keep it short, use the shared delivery rules, and call `finish` once the work is done instead of writing a normal assistant message first.

Without this file, the system would likely duplicate these rules in several places, making them easier to drift out of sync. A change to the delivery style or result limits might then affect some agents but not others. This file keeps that behavior centralized and predictable.


### Skill runtime
These files make skill code importable and define the runtime machinery for reading, registering, resolving, and loading skills.

### `core/src/ufo/runtime/skills/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the label does not contain the tools, but it lets the rest of the workshop know the drawer exists and can be opened.

Here, the drawer is `ufo.runtime.skills`. The actual skill-related code, if any, lives in other files inside this folder. This file exists so imports can refer to that package cleanly and consistently. Without it, some Python setups or tools might not recognize the folder as a package, which could make imports fail or behave differently.

Because the file is empty, it does not run setup code, expose shortcut names, or change any state. Its value is structural: it helps organize the codebase and supports Python’s import system.


### `core/src/ufo/runtime/skills/runtime.py`

`domain_logic` · `startup and skill loading during agent turns`

A skill is a folder with a SKILL.md file and optional extra files. The SKILL.md starts with YAML frontmatter, which is a small metadata block, followed by markdown instructions for the agent. This file turns those folders into structured Python objects, checks that they are valid, finds nested child skills, and builds a registry of all skills available during a run.

The registry works like a library catalog. It stores stable deploy-time skills, such as core built-in skills, and can also include member-saved skills for the current agent. When the agent asks to load a skill, the registry expands that request to include anything listed in the skill’s depends field. It avoids loops and duplicates, so a circular dependency does not crash the system.

After a skill set is resolved, this file can build the text shown to the model: each newly loaded skill contributes a header and its workflow instructions, while already-present workflows are only named instead of repeated. It also creates a tree of loaded files so the model can see where assets live.

Finally, it prepares skill files for the sandbox. Deploy skills may come from a prebuilt bundle, while user/member skills are encoded and sent into the sandbox directly. In short, this file is the bridge between “a folder of reusable instructions” and “instructions and files safely available to the agent at runtime.”

#### Function details

##### `skill_root`  (lines 52–54)

```
def skill_root(name: str) -> str
```

**Purpose**: Builds the stable runtime folder path for a named skill. This gives every skill a predictable home under $UFO_HOME/skills.

**Data flow**: It receives a skill name, joins it onto the fixed skills root path, and returns that path as text. It does not read or change any files.

**Call relations**: RuntimeSkill.root calls this helper when other code needs to know where a particular skill will live inside the runtime.

*Call graph*: called by 1 (root).


##### `RuntimeSkill.all_files`  (lines 92–93)

```
def all_files(self) -> dict[str, bytes]
```

**Purpose**: Returns every file that belongs to a parsed skill, including SKILL.md itself. This is useful when the system needs to hash, package, or send the whole skill somewhere.

**Data flow**: It reads the skill’s stored raw SKILL.md text and its asset file list, turns SKILL.md back into bytes, combines them into one dictionary keyed by file path, and returns that dictionary.

**Call relations**: The digest builder and sandbox wiring both use this method so they work from the exact same view of a skill’s files.

*Call graph*: called by 2 (content_digest, _wire_skill).


##### `RuntimeSkill.root`  (lines 95–96)

```
def root(self) -> str
```

**Purpose**: Returns the runtime directory where this skill should appear. It keeps path construction in one place instead of scattering string formatting across the code.

**Data flow**: It reads the skill’s name, passes it to skill_root, and returns the resulting $UFO_HOME/skills/... path.

**Call relations**: The sandbox wiring code calls this when converting skill file paths into safe paths for loading.

*Call graph*: calls 1 internal fn (skill_root); called by 1 (_wire_skill).


##### `RuntimeSkill.card`  (lines 98–106)

```
def card(self) -> SkillCard
```

**Purpose**: Creates the lightweight catalog entry for a runtime skill. The card contains routing information, not the full instruction body.

**Data flow**: It reads the skill’s name, description, dependencies, and agent targeting, then returns a SkillCard with those fields.

**Call relations**: The registry uses these cards when it needs to search or resolve dependencies without reading or injecting full skill instructions.

*Call graph*: 1 external calls (__init__).


##### `RuntimeSkill.content_digest`  (lines 108–114)

```
def content_digest(self) -> str
```

**Purpose**: Creates a stable fingerprint for a skill’s full contents. This lets different parts of the system tell whether two skill copies are exactly the same.

**Data flow**: It gathers all skill files, sorts them, hashes each path and each file’s bytes, combines those hashes, and returns a sha256: digest string.

**Call relations**: Sandbox wiring and system bundle creation rely on this digest to cache and identify skill content consistently.

*Call graph*: calls 1 internal fn (all_files); called by 1 (_wire_skill); 1 external calls (sha256).


##### `SystemSkillBundle.from_skills`  (lines 126–151)

```
def from_skills(cls, skills: Iterable[RuntimeSkill]) -> 'SystemSkillBundle'
```

**Purpose**: Builds a deterministic ZIP archive for deploy-time system skills. Deterministic means the same inputs produce the same bytes, which is important for caching and verification.

**Data flow**: It receives runtime skills, rejects conflicting duplicate names, records each skill’s digest and file list in a manifest, writes the manifest and all skill files into an in-memory ZIP archive, and returns a SystemSkillBundle containing the bundle digest, archive bytes, and manifest bytes.

**Call relations**: Startup and serving code call this when preparing core or deploy-controlled skills for shared use by the runtime, terminal cache, or sandbox image.

*Call graph*: called by 4 (init_runtime, _mount_shared_surfaces, run, system_skill_bundle); 4 external calls (sha256, BytesIO, dumps, ZipFile).


##### `SystemSkillBundle._write`  (lines 154–157)

```
def _write(archive: zipfile.ZipFile, path: str, content: bytes) -> None
```

**Purpose**: Writes one file into a system skill ZIP archive in a repeatable way. It fixes file timestamps and permissions so archives do not change just because they were built at a different time.

**Data flow**: It receives an open ZIP archive, a path, and file bytes. It creates a ZIP entry with a fixed timestamp and normal file permissions, then writes the bytes into the archive.

**Call relations**: SystemSkillBundle.from_skills uses this helper while assembling the manifest and skill files into the archive.

*Call graph*: 2 external calls (writestr, ZipInfo).


##### `LoadedSkill.prompt_body`  (lines 170–180)

```
def prompt_body(self) -> str
```

**Purpose**: Builds the piece of prompt text contributed by one loaded skill. It labels whether the agent asked for the skill directly or it arrived as a dependency.

**Data flow**: It reads the loaded skill’s name, instructions, and dependency marker. It returns a markdown block with a header and the skill’s workflow instructions, but not asset file contents.

**Call relations**: loaded_context uses this when creating the text that is placed in front of the model for newly loaded skills.


##### `LoadedSkills.reseed`  (lines 206–225)

```
def reseed(self, loads: Iterable[tuple[LoadedRef, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the tracker of which skill instructions are already in the model’s context. This prevents the same workflow from being injected again and again.

**Data flow**: It receives prior resolved loads and optional preloaded skills, clears the old tracker, adds every skill name that is currently in context, and separately records which ones the agent directly asked for.

**Call relations**: It calls reset first so the tracker reflects the current conversation window rather than old memory. This matters after compaction, replay, or subagent preloading.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 227–232)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the skills the agent directly asked for, then clears the tracker. This is used when a boundary drops workflow text but wants to remember what should be reloadable later.

**Data flow**: It sorts the asked-for skill names, stores them as a tuple, resets both tracking sets, and returns the names.

**Call relations**: It calls reset after collecting the names, making it a handoff point between one context window and the next.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 234–236)

```
def reset(self) -> None
```

**Purpose**: Clears the record of loaded and directly requested skills. It is the simple “empty the notebook” operation for the skill context tracker.

**Data flow**: It takes no new data, empties the in_context set, and empties the asked_for set. It returns nothing.

**Call relations**: LoadedSkills.reseed calls it before rebuilding the tracker, and LoadedSkills.drain calls it after extracting the remembered direct requests.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 239–245)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates a SKILL.md file into its metadata block and instruction body. It enforces the expected frontmatter format so malformed skills fail early.

**Data flow**: It receives SKILL.md text, checks that it starts with the opening fence, finds the closing fence, and returns the metadata text and body text. If the fences are missing, it raises an error.

**Call relations**: parse_skill_content calls this before interpreting the YAML metadata and building a RuntimeSkill.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 248–253)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate subfolders that are themselves skills. A child skill is recognized by having its own SKILL.md file.

**Data flow**: It receives a directory path, looks at its direct children, keeps only directories containing SKILL.md, sorts them, and returns the list.

**Call relations**: parse_skill uses this to exclude child skill folders from the parent’s asset files, and discover_skills uses it to recursively register child skills.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 256–297)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Turns an in-memory set of skill files into a validated RuntimeSkill. This lets skills loaded from storage be checked the same way as skills read from disk.

**Data flow**: It receives a claimed directory name, a mapping of file paths to bytes, and optional registry naming information. It reads SKILL.md, splits and parses the YAML frontmatter, checks that the skill name matches the directory, validates fields such as agents and indexed, separates asset files, and returns a RuntimeSkill.

**Call relations**: parse_skill calls this after reading files from disk. It is the central validation step for skill metadata and instruction text.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 300–309)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads a skill folder from disk and parses it into a RuntimeSkill. It treats child skill folders as separate skills instead of bundling their files into the parent.

**Data flow**: It receives a filesystem directory, finds child skill directories, reads all ordinary files outside those child skill subtrees, and passes the collected bytes to parse_skill_content. It returns the parsed RuntimeSkill.

**Call relations**: discover_skills calls this for each skill directory it visits while flattening parent and child skills into the registry.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 312–330)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers one skill and all of its nested child skills, returning them as a flat name-to-skill map. This makes nested folders usable through path-like names such as parent/child.

**Data flow**: It receives a skill directory and optional registry naming information. It parses the current skill, records it, finds child skill directories, recursively discovers each child with a nested registry name, and returns the combined dictionary.

**Call relations**: _load_core_skills calls this when building the built-in skill set from the source tree.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 333–340)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the core skills shipped with this codebase. These are the built-in skills that teach the system’s baseline behavior.

**Data flow**: It receives a root directory, scans visible subdirectories, discovers skills under each one, and returns a dictionary keyed by skill name.

**Call relations**: The module calls this at import time to build CORE_SKILLS_BY_NAME, which then feeds the default core SkillRegistry.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.__post_init__`  (lines 367–369)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the default set of bundled skill names after a registry is created. If no explicit bundle list is supplied, all deploy-time skills are treated as bundled.

**Data flow**: It checks whether bundled_names is missing. If so, it sets bundled_names to the current by_name keys while keeping the frozen dataclass behavior intact.

**Call relations**: This runs automatically when a SkillRegistry is constructed, including the core registry and registries built by merge or member-attachment operations.


##### `SkillRegistry.named`  (lines 371–375)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up a deploy-time skill by name and gives a helpful error if it is unknown. It is for places that need the full RuntimeSkill, not just a routing card.

**Data flow**: It receives a name, tries to fetch it from the deploy skill dictionary, and returns the RuntimeSkill. If the name is absent, it raises the formatted unknown-skill error.

**Call relations**: When lookup fails, it calls _unknown so the error can include close-name suggestions.

*Call graph*: calls 1 internal fn (_unknown).


##### `SkillRegistry._unknown`  (lines 377–380)

```
def _unknown(self, name: str) -> ValueError
```

**Purpose**: Creates a clear error for an unknown skill name. It suggests close matches so a typo is easier to fix.

**Data flow**: It receives the bad name, asks known_names for all valid names, finds a few similar names, and returns a ValueError containing the unknown name and optional suggestions.

**Call relations**: named and _card use this whenever a requested skill cannot be found.

*Call graph*: calls 1 internal fn (known_names); called by 2 (_card, named); 1 external calls (get_close_matches).


##### `SkillRegistry._card`  (lines 382–389)

```
def _card(self, name: str) -> SkillCard
```

**Purpose**: Gets the routing card for a skill, whether it is a deploy skill or a member-saved skill. The card is the small record used for dependency resolution.

**Data flow**: It receives a skill name, first checks deploy skills and converts one to a card if found, then checks member cards. If neither exists, it raises the unknown-skill error.

**Call relations**: closure and its nested add step call this as they walk requested skills and dependencies.

*Call graph*: calls 1 internal fn (_unknown); called by 2 (closure, add).


##### `SkillRegistry.known_names`  (lines 391–394)

```
def known_names(self) -> frozenset[str]
```

**Purpose**: Returns every skill name this registry can resolve. This includes both deploy-time skills and member-saved skill cards.

**Data flow**: It reads the keys of the deploy skill dictionary and member card dictionary, combines them into one frozen set, and returns it.

**Call relations**: _unknown uses this set to produce better error messages, and save paths can use the same idea to avoid name collisions.

*Call graph*: called by 1 (_unknown).


##### `SkillRegistry.all_cards`  (lines 396–401)

```
def all_cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: Returns lightweight routing cards for every loadable skill. This is useful for search and selection without loading full instruction bodies.

**Data flow**: It converts each deploy RuntimeSkill into a SkillCard, appends the stored member cards, and returns all cards as a tuple.

**Call relations**: Skill search code can score these cards to decide which skills might be relevant to an agent’s request.


##### `SkillRegistry.bundled_skills`  (lines 403–406)

```
def bundled_skills(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Returns the deploy skills that are part of the static bundle. These are the skills expected to already be available through the shipped archive or sandbox image.

**Data flow**: It reads bundled_names, filters the deploy skill dictionary to those names, and returns the matching RuntimeSkill objects.

**Call relations**: Serving code calls this when mounting shared surfaces that expose the bundled system skills.

*Call graph*: called by 1 (_mount_shared_surfaces).


##### `SkillRegistry.closure`  (lines 408–432)

```
def closure(self, *names: str) -> tuple[LoadedRef, ...]
```

**Purpose**: Expands requested skill names into the full set that must be loaded, including dependencies. It preserves direct requests as direct, avoids duplicates, and is safe against dependency cycles.

**Data flow**: It receives one or more skill names, creates direct LoadedRef entries for the unique requested names, then walks each skill’s depends list. Each newly found dependency becomes a LoadedRef marked with the skill that pulled it. It returns the ordered tuple of references.

**Call relations**: The runtime engine calls this before materializing skills, so it can know the complete load plan without reading member skill bodies yet.

*Call graph*: calls 1 internal fn (_card); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 422–427)

```
def add(card: SkillCard, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency and recursively adds its dependencies during closure building. It is the small inner worker that makes dependency expansion happen.

**Data flow**: It receives a SkillCard and the name of the skill that depended on it. If that skill is already recorded, it stops; otherwise it records a LoadedRef and repeats the process for each dependency listed on the card.

**Call relations**: SkillRegistry.closure uses this helper while walking dependency chains, and the helper calls _card whenever it needs the routing card for a dependency name.

*Call graph*: calls 1 internal fn (_card); 1 external calls (__init__).


##### `SkillRegistry.materialize`  (lines 434–456)

```
async def materialize(self, refs: Sequence[LoadedRef]) -> tuple[LoadedSkill, ...]
```

**Purpose**: Turns a resolved load plan into actual loaded skills with instruction bodies and files. This is where member skill bytes may be read through the materializer.

**Data flow**: It receives LoadedRef entries, looks up each name in deploy skills or asks the materializer for a member skill, verifies the returned skill still has the expected name, wraps it as a LoadedSkill with dependency and bundled flags, and returns the tuple. If a skill disappeared, it raises an error.

**Call relations**: This runs after closure resolution. It hands later prompt-building and sandbox-loading code concrete RuntimeSkill objects instead of lightweight cards.

*Call graph*: 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 458–467)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the visible skill index for the system prompt. It includes top-level deploy skills and child skills that explicitly opted into indexing.

**Data flow**: It reads deploy skills in registry order, keeps skills with no parent or with indexed set to true, and returns name-description pairs. Member skills are intentionally left out.

**Call relations**: Prompt-building code calls this to fill the {{skill_index}} area shown to the model.

*Call graph*: called by 2 (_prompt_skill_index, prompt_index).


##### `SkillRegistry.merged_with`  (lines 469–490)

```
def merged_with(self, generated: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that includes generated deploy-controlled skills. If a generated skill tries to reuse an existing deploy name, the existing skill wins.

**Data flow**: It copies the deploy skill dictionary, appends non-conflicting generated skills, logs any refused generated shadowing, removes member cards that now collide with deploy names, and returns a new SkillRegistry with the same materializer and bundle settings.

**Call relations**: This is used when runtime-generated skills, such as setup or spawn catalog skills, need to be added without letting them break the no-shadowing rule.

*Call graph*: 2 external calls (__init__, log).


##### `SkillRegistry.with_member`  (lines 492–511)

```
def with_member(self, cards: Sequence[SkillCard], materialize: SkillMaterializer) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that includes the current agent’s saved member skills. Member skills are allowed as an extra tier but cannot replace deploy skills.

**Data flow**: It receives member SkillCards and a materializer callback. It keeps only cards whose names do not collide with deploy skills, logs refused collisions, and returns a new registry with those member cards attached.

**Call relations**: Turn setup can call this to bind member skills for one agent while preserving the stable deploy skill catalog.

*Call graph*: 2 external calls (__init__, log).


##### `_loaded_tree`  (lines 517–535)

```
def _loaded_tree(loaded: Sequence[LoadedSkill]) -> str
```

**Purpose**: Builds a compact text tree showing every file made available by a skill load. This helps the model know where files are without printing their contents.

**Data flow**: It receives loaded skills, gathers every skill file path under the skills root, sorts paths, emits each directory once with indentation, and returns the tree as text.

**Call relations**: loaded_context calls this at the end of the prompt text so every loaded workflow is accompanied by a map of available files.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 538–551)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Builds the full text shown to the model for one skill load. It includes new workflow instructions, a note for workflows already in context, and the loaded file tree.

**Data flow**: It receives LoadedSkill objects and a set of skill names already in context. It renders prompt bodies only for new skills, collects repeated names into one note, appends the loaded-files tree, and returns the final string.

**Call relations**: Both normal load_skill behavior and subagent preloading use this so skills look the same no matter how they are introduced.

*Call graph*: calls 1 internal fn (_loaded_tree).


##### `_wire_skill`  (lines 554–561)

```
def _wire_skill(skill: RuntimeSkill) -> dict[str, object]
```

**Purpose**: Converts a RuntimeSkill into the wire format expected by the sandbox. Wire format here means a safe dictionary representation that can be sent across a boundary.

**Data flow**: It receives a RuntimeSkill, gets all files, checks and normalizes each path under the skill root, base64-encodes each file’s bytes into text, computes the skill digest, and returns a dictionary with digest and files.

**Call relations**: install_skill and load_skills call this for user or member-provided skill content before asking the sandbox to load it.

*Call graph*: calls 3 internal fn (all_files, content_digest, root); called by 2 (install_skill, load_skills); 2 external calls (urlsafe_b64encode, contained_relative).


##### `install_skill`  (lines 564–568)

```
async def install_skill(sandbox: Sandbox, skill: RuntimeSkill) -> None
```

**Purpose**: Installs one materialized skill into the sandbox’s runtime skills directory. It is a convenience path for loading a single skill.

**Data flow**: It receives a sandbox session and a RuntimeSkill, converts the skill with _wire_skill, sends it to Sandbox.load_skills as user content, then checks that the sandbox reported a root path for that skill. If not, it raises an error.

**Call relations**: It hands the prepared skill off to the sandbox layer, which performs the actual runtime installation.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


##### `load_skills`  (lines 571–578)

```
async def load_skills(sandbox: Sandbox, loaded: Sequence[LoadedSkill]) -> None
```

**Purpose**: Loads a resolved group of skills into the sandbox. It separates bundled deploy skills from skills whose files must be sent directly.

**Data flow**: It receives a sandbox and LoadedSkill entries. Bundled skills become a name-to-digest map, non-bundled skills are converted with _wire_skill, both groups are sent to Sandbox.load_skills, and the function raises an error if any requested skill is missing from the sandbox response.

**Call relations**: After the registry has resolved and materialized a skill load, this function is the handoff that makes those files available inside the sandbox.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).
