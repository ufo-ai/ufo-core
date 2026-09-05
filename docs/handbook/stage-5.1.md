# Prompt, skill, and environment document resolution  `stage-5.1`

This stage is behind-the-scenes preparation for a model turn. Before the agent answers, it gathers the written instructions and reference material that shape what the model will see. The environment file defines “environment documents,” which are saved bundles of prompt changes, tool settings, skills, model choices, and files. It stores them by content digest, a unique label made from the file’s contents, so runs can be replayed exactly.

The delivery register loads shared answer-writing rules, such as how concise responses should be, so every kind of agent follows the same standards. The prompt renderer then fills in prompt templates, checks that all blanks were filled, and makes a fingerprint of the final prompt so changes can be tracked.

Skills are small instruction folders. The skill runtime reads and registers them, while the model catalog file creates one built-in skill that accurately lists the models available in this deployment. Finally, skill selection chooses which skill descriptions fit into the current turn, like packing the most useful tools into a limited toolbox.

## Files in this stage

### Environment and delivery documents
Defines replayable environment documents and shared answer-delivery rules that shape the turn context.

### `core/src/ufo/host/environment.py`

`data_model` · `config load and turn setup`

An environment document is like a sealed instruction sheet for an experiment. It can say, for example, “use this prompt text,” “hide this tool,” “rewrite this tool description,” “replace this skill,” or “place this file in the workspace.” The important rule is that these changes can narrow or rewrite what the system already offers, but they cannot secretly grant extra power. The one special addition is a `run` tool, which runs a command inside the same sandbox the turn already has.

This file uses Pydantic models, which are Python classes that check incoming data, to describe what a valid document may contain. The checks are strict: unknown fields are rejected, contradictory settings are rejected, skill text must parse as a real skill, file paths must stay inside the workspace, and stored digests must look like SHA-256 hashes.

The file also makes documents reproducible. A document may be written as YAML or JSON, but `parse_environment_document` converts it into one standard JSON form before hashing it. That means the digest depends on the meaning of the document, not on spacing or whether the author used YAML. Storage functions then put documents and files into a workspace blob store under keys based on those hashes, and loading functions verify that the bytes still match the digest.

#### Function details

##### `PromptOverride._one_form`  (lines 59–62)

```
def _one_form(self) -> 'PromptOverride'
```

**Purpose**: This validation check makes sure a prompt override uses exactly one style: either replace the whole prompt with new text, or apply a list of smaller text edits. It prevents unclear instructions such as asking for both at once, or asking for neither.

**Data flow**: It reads the already-filled `PromptOverride` object. If the object has exactly one of `text` or `replace`, it returns the object unchanged. If the shape is ambiguous or empty, it raises an error before the document can be accepted.

**Call relations**: This runs automatically while Pydantic is building a `PromptOverride` from an environment document. It protects later prompt assembly code from having to guess which kind of prompt change the author intended.


##### `ToolOverride._one_meaning`  (lines 91–104)

```
def _one_meaning(self) -> 'ToolOverride'
```

**Purpose**: This validation check makes sure a tool override has one clear meaning. A tool can be hidden, rewritten, or replaced/added as a sandbox command, but those meanings cannot be mixed in contradictory ways.

**Data flow**: It reads the fields of one `ToolOverride`: description edits, parameter text edits, whether the tool is enabled, any custom input fields, and any command to run. It returns the object if the combination is valid, or raises an error if the requested change does not make sense.

**Call relations**: This runs automatically when a tool override is parsed from an environment document. It keeps the later tool-offer building step simple: by then, each tool override already says one unambiguous thing.


##### `EnvironmentDocument._entries_parse`  (lines 150–171)

```
def _entries_parse(self) -> 'EnvironmentDocument'
```

**Purpose**: This validation check verifies the parts of a complete environment document that refer to skills and files. It catches broken skill replacements, unsafe file destinations, and malformed file digests early.

**Data flow**: It reads the document’s `skills` and `files` maps. Full skill text is passed through the skill parser to prove it is usable. Each file destination is checked to make sure it is a relative path inside the workspace, and each file digest is checked against the expected digest pattern. If all entries are safe and valid, the document is returned; otherwise an error is raised.

**Call relations**: This runs automatically when an `EnvironmentDocument` is built. It calls the skill parser for skill text, the containment helper for workspace-safe paths, and the digest pattern checker for stored file references, so later turn setup can trust these entries.

*Call graph*: 3 external calls (contained_relative, parse_skill_content, fullmatch).


##### `parse_environment_document`  (lines 174–190)

```
def parse_environment_document(body: bytes) -> tuple[EnvironmentDocument, bytes, str]
```

**Purpose**: This function reads an authored environment document and turns it into a checked, canonical, hashable form. It is used when a document is being accepted for storage.

**Data flow**: It takes raw bytes, first rejecting documents over the size cap. It parses the bytes as YAML, which also covers JSON, validates the result as an `EnvironmentDocument`, converts that validated document into a standard compact JSON byte string, and computes a SHA-256 digest from those canonical bytes. It returns the validated document, the canonical bytes, and the digest.

**Call relations**: This is called by `store_environment_document` before saving anything. It hands that function the exact bytes to store and the digest that will later pin and verify the document.

*Call graph*: called by 1 (store_environment_document); 3 external calls (sha256, dumps, safe_load).


##### `store_environment_document`  (lines 193–196)

```
async def store_environment_document(blob: WorkspaceBlobStore, body: bytes) -> str
```

**Purpose**: This function saves an environment document in the workspace blob store under a key based on its content hash. It returns the digest that other parts of the system can use to refer to that exact document.

**Data flow**: It receives a blob store and raw document bytes. It asks `parse_environment_document` to validate and canonicalize the document, then writes the canonical bytes into the blob store under the environment-document prefix plus the digest. The output is the digest string.

**Call relations**: This function sits at the upload or registration point for environment documents. It relies on `parse_environment_document` for correctness, then hands the verified bytes to `WorkspaceBlobStore.put` so future turns can load the same document by digest.

*Call graph*: calls 2 internal fn (put, parse_environment_document).


##### `load_environment_document`  (lines 199–205)

```
async def load_environment_document(blob: WorkspaceBlobStore, digest: str) -> EnvironmentDocument
```

**Purpose**: This function retrieves a stored environment document by digest and checks that the stored bytes really match that digest. It protects replay from corrupted or mismatched stored data.

**Data flow**: It takes a blob store and a digest string. It first checks that the digest has the right shape, reads the stored bytes from the blob store, recomputes their SHA-256 digest, and compares it with the requested digest. If they match, it parses the stored JSON into an `EnvironmentDocument`; if not, it raises an error.

**Call relations**: This is used when a turn needs to apply a previously pinned environment document. It calls the blob store to fetch bytes and uses the digest pattern and hash check before giving the validated document back to turn setup.

*Call graph*: calls 1 internal fn (get); 2 external calls (sha256, fullmatch).


##### `store_environment_file`  (lines 208–217)

```
async def store_environment_file(blob: WorkspaceBlobStore, body: bytes) -> str
```

**Purpose**: This function saves a raw file that an environment document wants placed into a workspace sandbox. Unlike documents, the file bytes are not parsed; they are stored exactly as uploaded.

**Data flow**: It receives a blob store and raw file bytes. It rejects files over the size cap, computes a SHA-256 digest of the bytes, stores the bytes under the environment-file prefix plus that digest, and returns the digest string.

**Call relations**: This is used before or while preparing an environment document that references files. It hands the raw bytes to `WorkspaceBlobStore.put`, and the returned digest can be written into the document’s `files` map.

*Call graph*: calls 1 internal fn (put); 1 external calls (sha256).


##### `load_environment_file`  (lines 220–224)

```
async def load_environment_file(blob: WorkspaceBlobStore, digest: str) -> bytes
```

**Purpose**: This function retrieves a raw environment file by digest and verifies that the stored bytes have not changed. It is used when preparing a turn sandbox with files named by an environment document.

**Data flow**: It takes a blob store and a digest string. It reads the stored bytes from the environment-file area, recomputes their SHA-256 digest, and compares it with the requested digest. If the check passes, it returns the bytes; if it fails, it raises an error.

**Call relations**: This is called when the system needs the actual file contents named in an environment document. It gets the bytes from `WorkspaceBlobStore.get` and only hands them on after confirming the content matches the digest.

*Call graph*: calls 1 internal fn (get); 1 external calls (sha256).


### `core/src/ufo/runtime/turns/delivery_register.py`

`config` · `startup and prompt construction`

This file is a small rulebook loader. The project has a “delivery register,” meaning a shared set of instructions about how an agent should present final words to a person or to another agent. Instead of copying those instructions into many places, this file reads them once from a nearby Markdown file called `delivery_register.md` and exposes them as `DELIVERY_REGISTER_BLOCK`.

It also defines size limits for two kinds of output. `DIRECT_PROSE_RESULT_MAX_CHARS` caps direct prose results, so answers meant to be short do not grow too large. `SUBAGENT_RESULT_MAX_WORDS` caps what a subagent reports back to its parent agent. The longer `SUBAGENT_RESULT_DESCRIPTION` turns that limit into plain instructions for a subagent: give one parent-visible result, keep it brief, call `finish` when done, do not first write the result as normal assistant prose, and do not repeat the full contents of an artifact when a file path is enough.

In everyday terms, this file is like the style card taped next to a service desk: everyone uses the same card, so customers get consistent answers no matter which worker helped them.


### Prompt rendering
Renders prompt templates into final model-ready system text and fingerprints the resulting content.

### `core/src/ufo/runtime/prompts/render.py`

`domain_logic` · `prompt rendering before a model turn`

This file is the prompt assembly room. The project keeps reusable prompt text in Markdown files, with slots such as `{{agent-prompt}}`, `{{sections}}`, and `{{knowledge_cutoff}}`. This renderer fills those slots with the agent’s instructions, available skills, workspace capabilities, citation rules, and the model’s knowledge cutoff date.

The main safety idea is that unfinished prompt text should fail loudly. A placeholder like `{{name}}` is only allowed if the caller supplies a matching value, and every supplied value must match a declared placeholder. After rendering, the file scans the whole prompt again. If any `{{...}}` slot remains, it raises an error instead of sending confusing raw braces to the model.

It also standardizes small prompt blocks. For example, skills are wrapped in an `<available_skills>` section, workspace facts are wrapped in a `<workspace_capabilities>` section, and object kinds are listed in a predictable shape. This is like giving every insert in a binder the same labeled tab, so the model can read the prompt consistently.

Finally, the rendered prompt is paired with a SHA-256 digest, a short cryptographic fingerprint of the exact text. That lets logs and observability tools tell which prompt version was used without comparing the whole prompt.

#### Function details

##### `rendered_prompt`  (lines 61–62)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: Wraps finished prompt text together with a digest, which is a stable fingerprint of that exact text. This makes it easier to notice and trace prompt changes later.

**Data flow**: It receives one string: the fully rendered prompt content. It computes a SHA-256 hash from that text, prefixes it with `sha256:`, and returns a `RenderedPrompt` object containing both the digest and the original content.

**Call relations**: This is the final packaging step used by `render_template`. After `render_template` has filled and checked the prompt, it calls `rendered_prompt` so the finished text can travel through the system with its fingerprint attached.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 65–82)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: Builds the main agent’s complete system prompt from the project’s standard shell template. It adds the agent instructions, skills, capability sections, citation block, and a human-readable knowledge cutoff date.

**Data flow**: It receives the agent prompt, contributed sections, optional skills, and a model knowledge cutoff like `2026-02`. It turns that date into a friendlier form like `February 2026`, inserts it into the knowledge cutoff block, places that block into the shell template, and passes everything to `render_template`. The result is a `RenderedPrompt` with final text and digest.

**Call relations**: This is the high-level entry for rendering the normal system prompt. It prepares the special knowledge-cutoff piece first, then hands the real filling and validation work to `render_template`.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 85–103)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: Fills a prompt template and verifies that it is complete. It is the central function that combines the shell text, agent prompt, skills, citation rules, and section text into one safe final prompt.

**Data flow**: It receives a template, an agent prompt, a mapping of variable names to values, a list of skills, and a list of sections. First it substitutes variables inside the agent prompt. Then it fills the template slots for skills, citation text, contributed sections, and the agent prompt. It checks for any leftover `{{...}}` placeholders, cleans up extra blank lines, trims the end, and returns a `RenderedPrompt`.

**Call relations**: `render_system_prompt` calls this after preparing the shell template. Inside, it asks `_substitute_vars` to fill variables safely, asks `render_skill_index` to format the skill list, and finally calls `rendered_prompt` to attach the digest.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_workspace_facts`  (lines 115–128)

```
def render_workspace_facts(lines: Sequence[str]) -> str
```

**Purpose**: Formats lines describing capabilities the workspace already has. This helps the prompt tell the model what is already set up, so it does not offer to set up the same thing again.

**Data flow**: It receives a sequence of text lines, one per existing workspace capability. If there are no lines, it returns an empty string. Otherwise, it wraps the lines in a `<workspace_capabilities>` block and adds one shared closing instruction: `Already set up — do not offer again.`

**Call relations**: This helper prepares one kind of section that can later be included in the prompt as contributed section text. It does not call other functions in this file; it simply produces a standardized block for the larger rendering flow to use.


##### `render_object_kinds`  (lines 134–151)

```
def render_object_kinds(kinds: Sequence[tuple[str, str, Sequence[str]]]) -> str
```

**Purpose**: Formats the kinds of workspace objects the current turn can refer to, along with their descriptions and possible actions. This gives the model a clear list of what objects are available and what can be done with them.

**Data flow**: It receives entries containing an object kind name, a description, and a list of actions. If the list is empty, it returns an empty string. Otherwise, it writes one line for each kind and, when actions exist, adds an indented actions line. The whole result is wrapped in a `<workspace_objects>` block.

**Call relations**: This helper creates another standardized prompt section for the broader prompt assembly process. It stands alone here, producing text that other orchestration code can pass into `render_template` as part of the sections list.


##### `render_skill_index`  (lines 154–163)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: Turns the available skills list into a small prompt block the model can read. If there are no skills, it avoids adding an empty section.

**Data flow**: It receives skill name and description pairs. With no skills, it returns an empty string. With skills, it creates an `<available_skills>` block where each skill appears as `- name: description`.

**Call relations**: `render_template` calls this whenever it fills the skill slot in a prompt. This keeps skill formatting in one place, instead of making the main template renderer know the exact line-by-line format.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 166–173)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: Safely replaces `{{variable}}` placeholders inside an agent prompt. It protects against both missing values and accidental extra values, so prompt mistakes are caught before reaching the model.

**Data flow**: It receives a text template and a mapping of variable names to replacement strings. It scans the template for declared placeholder names, compares them with the supplied names, and raises an error if any are missing or extra. If everything matches, it replaces each placeholder with its supplied value and returns the filled text.

**Call relations**: `render_template` calls this before putting the agent prompt into the larger shell. This means the agent prompt is checked on its own first, then the complete rendered prompt is checked again for any leftover slots.

*Call graph*: called by 1 (render_template).


### Skill runtime and selection
Defines skills, registers built-in and authored skill content, and chooses which skill descriptions fit the current turn.

### `core/src/ufo/runtime/skills/runtime.py`

`domain_logic` · `startup and request handling`

This file is the center of the skill system. In this project, a skill is a folder containing a SKILL.md file with two parts: a YAML frontmatter block, which is structured metadata, and a markdown body, which is the workflow text shown to the agent. The skill may also include asset files. Without this file, the system would not know how to read those folders, decide which skills exist, follow dependencies between skills, avoid showing the same instructions twice, or place skill files into the sandbox where code can use them.

The file works in layers. First, it parses skill folders into RuntimeSkill objects, keeping the original SKILL.md text and all bundled files. It also discovers child skills in nested folders, but nesting only affects naming; a child must still declare a dependency if it needs its parent.

Next, SkillRegistry acts like the library catalog. It knows built-in deployed skills and optional member-created skills. It can answer “what skills are known?”, “what should appear in the prompt index?”, and “if I load this skill, what other skills must come with it?”

Finally, the runtime loading helpers build the text shown to the agent and copy skill files into the sandbox. They also track which workflows are already in the conversation, so repeated loads mention the skill without wasting space by repeating its full instructions.

#### Function details

##### `skill_root`  (lines 52–54)

```
def skill_root(name: str) -> str
```

**Purpose**: Builds the stable runtime folder path for a skill. Other code uses this path when telling the sandbox where the skill’s files should appear.

**Data flow**: It takes a skill name, puts it under the shared skills root path, and returns the full string path. It does not read or change anything else.

**Call relations**: RuntimeSkill.root calls this when a RuntimeSkill needs to report where its files will live inside the runtime.

*Call graph*: called by 1 (root).


##### `RuntimeSkill.all_files`  (lines 92–93)

```
def all_files(self) -> dict[str, bytes]
```

**Purpose**: Returns every file that belongs to one skill, including SKILL.md itself. This gives later code one complete package of files to hash or send to the sandbox.

**Data flow**: It reads the RuntimeSkill’s stored raw SKILL.md text and its asset file bytes, then returns a dictionary from file path to bytes. The skill object is not changed.

**Call relations**: RuntimeSkill.content_digest uses this complete file set to compute a fingerprint, and _wire_skill uses it to prepare files for sandbox loading.

*Call graph*: called by 2 (content_digest, _wire_skill).


##### `RuntimeSkill.root`  (lines 95–96)

```
def root(self) -> str
```

**Purpose**: Reports the folder where this skill should be installed inside the runtime. It is a small convenience around the shared skill path rule.

**Data flow**: It reads the skill’s name, passes it to skill_root, and returns the resulting path string.

**Call relations**: When _wire_skill prepares a skill for sandbox transfer, it asks RuntimeSkill.root for the expected destination path.

*Call graph*: calls 1 internal fn (skill_root); called by 1 (_wire_skill).


##### `RuntimeSkill.card`  (lines 98–106)

```
def card(self) -> SkillCard
```

**Purpose**: Creates the lightweight catalog entry for a skill. The catalog entry includes routing information such as name, description, dependencies, and target agents, but not the full workflow body.

**Data flow**: It reads selected fields from the RuntimeSkill and returns a SkillCard containing only the information needed for searching and dependency resolution.

**Call relations**: Skill registries use these cards when they need to reason about skills without loading or rereading all of their files.

*Call graph*: 1 external calls (__init__).


##### `RuntimeSkill.content_digest`  (lines 108–114)

```
def content_digest(self) -> str
```

**Purpose**: Computes a stable fingerprint for a skill’s full contents. This lets different parts of the system tell whether two copies of a skill are exactly the same.

**Data flow**: It gathers all files from the skill, sorts them by path, hashes each path and file content, combines those hashes, and returns a sha256-prefixed digest string.

**Call relations**: _wire_skill uses this digest when sending skills to the sandbox, and SystemSkillBundle.from_skills uses it to detect duplicate names with different contents.

*Call graph*: calls 1 internal fn (all_files); called by 1 (_wire_skill); 1 external calls (sha256).


##### `SystemSkillBundle.from_skills`  (lines 126–151)

```
def from_skills(cls, skills: Iterable[RuntimeSkill]) -> 'SystemSkillBundle'
```

**Purpose**: Builds one deterministic archive of system skills for deployment and caching. Deterministic means the same input skills produce the same bytes, which is important for reliable caches and verification.

**Data flow**: It takes a collection of RuntimeSkill objects, checks that duplicate names do not hide different contents, builds a manifest describing their file lists and digests, writes the manifest and all skill files into a ZIP archive, and returns a SystemSkillBundle containing the archive, manifest, and bundle digest.

**Call relations**: Startup and serving code call this when preparing the fixed skill bundle used by the runtime, shared surfaces, and sandbox template.

*Call graph*: called by 4 (init_runtime, _mount_shared_surfaces, run, system_skill_bundle); 4 external calls (sha256, BytesIO, dumps, ZipFile).


##### `SystemSkillBundle._write`  (lines 154–157)

```
def _write(archive: zipfile.ZipFile, path: str, content: bytes) -> None
```

**Purpose**: Writes one file into the system skill ZIP archive with fixed metadata. Fixed metadata keeps the archive byte-for-byte stable across builds.

**Data flow**: It takes an open ZIP archive, a path, and file bytes, creates a ZIP entry with a fixed timestamp and file permissions, and writes the content into the archive.

**Call relations**: SystemSkillBundle.from_skills uses this helper for both the manifest and every skill file it places in the archive.

*Call graph*: 2 external calls (writestr, ZipInfo).


##### `LoadedSkill.prompt_body`  (lines 170–180)

```
def prompt_body(self) -> str
```

**Purpose**: Builds the text block that one loaded skill contributes to the agent’s context. It clearly labels whether the agent asked for the skill directly or it arrived as a dependency.

**Data flow**: It reads the loaded skill’s name, dependency marker, and workflow instructions, then returns a markdown block with a header and the instructions. It does not include asset file contents.

**Call relations**: loaded_context uses this when assembling the full message shown to the model after a skill load.


##### `LoadedSkills.reseed`  (lines 206–225)

```
def reseed(self, loads: Iterable[tuple[LoadedRef, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the tracker of which skill workflows are already visible to the model. This prevents the system from repeating long instructions that are already in the conversation.

**Data flow**: It clears the current tracker, then reads resolved load records and optional preloaded skills. It fills in_context with every visible skill name and asked_for with the skills the agent directly requested.

**Call relations**: It calls LoadedSkills.reset first, then is used when the runtime reconstructs state from the current conversation window rather than blindly trusting old memory.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 227–232)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the skill names the agent explicitly asked for, then clears the tracker. This is useful at a boundary where old workflow text is dropped but the system wants to remember what should be reloadable.

**Data flow**: It reads the asked_for set, sorts it into a tuple, clears both tracking sets, and returns the sorted names.

**Call relations**: It calls LoadedSkills.reset after collecting the names, so later turns start from a clean tracking state.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 234–236)

```
def reset(self) -> None
```

**Purpose**: Clears all remembered skill context information. It is the simple reset button for the loaded-skill tracker.

**Data flow**: It empties the in_context set and the asked_for set. It returns nothing.

**Call relations**: LoadedSkills.reseed uses it before rebuilding the tracker, and LoadedSkills.drain uses it after extracting the names to carry forward.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 239–245)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates a SKILL.md file into its metadata header and instruction body. This enforces the expected file shape before the skill is accepted.

**Data flow**: It takes the raw SKILL.md text, checks that it starts with the frontmatter fence, finds the closing fence, and returns the metadata text and body text. If the fences are missing, it raises an error.

**Call relations**: parse_skill_content calls this before interpreting the metadata as YAML and building a RuntimeSkill.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 248–253)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate child folders that are themselves skills. A child skill is recognized by having its own SKILL.md file.

**Data flow**: It reads the entries directly inside a skill directory, keeps subdirectories containing SKILL.md, sorts them, and returns their paths.

**Call relations**: parse_skill uses it to exclude child skill subtrees from the parent’s asset files, and discover_skills uses it to recurse into child skills.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 256–297)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Turns an in-memory set of skill files into a validated RuntimeSkill. This is used when skill bytes come from storage as well as when they come from disk.

**Data flow**: It receives a directory name and a mapping of file paths to bytes. It reads SKILL.md, splits and parses the frontmatter, checks that the declared name matches the directory, validates selected metadata fields, collects asset files, and returns a RuntimeSkill.

**Call relations**: parse_skill calls this after reading files from disk. It relies on _split_frontmatter for the SKILL.md structure and produces the RuntimeSkill objects used by registries and loaders.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 300–309)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads one skill folder from disk and parses it. It treats child skill folders as separate skills, not as asset folders of the parent.

**Data flow**: It scans the folder recursively, skips files inside immediate child skill directories, reads the remaining file bytes, and passes them to parse_skill_content. The result is one RuntimeSkill for the folder itself.

**Call relations**: discover_skills calls this for each skill directory it visits while building a flat map of parent and child skills.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 312–330)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills. It flattens them into a name-to-skill map so the registry can look them up by name.

**Data flow**: It parses the current directory as a RuntimeSkill, records it under its registry name, finds immediate child skill directories, and recursively discovers each child under a path-style name such as parent/child.

**Call relations**: _load_core_skills calls this while loading built-in skills at import time. It calls parse_skill for the current folder and _child_skill_dirs to find children.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 333–340)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the project’s built-in skills from the directory next to this file. These core skills are always available in the base registry.

**Data flow**: It lists eligible subdirectories, discovers skills inside each one, and returns a dictionary from skill name to RuntimeSkill.

**Call relations**: This runs during module setup to create CORE_SKILLS_BY_NAME and, from that, the default CORE_SKILL_REGISTRY.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.__post_init__`  (lines 367–369)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the default set of bundled skill names when the registry is created. If no explicit bundled set is provided, all deployed skills are treated as bundled.

**Data flow**: It checks the bundled_names field. If it is missing, it sets it to the current by_name keys without otherwise changing the registry’s skill data.

**Call relations**: This dataclass hook runs automatically after SkillRegistry construction, including registries made by merged_with and with_member.


##### `SkillRegistry.named`  (lines 371–375)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up a deployed runtime skill by exact name. It is for cases that need the full RuntimeSkill object, not just a routing card.

**Data flow**: It reads the by_name dictionary and returns the RuntimeSkill for the requested name. If the name is missing, it raises a helpful unknown-skill error.

**Call relations**: When lookup fails, it delegates to SkillRegistry._unknown to build an error message with possible close matches.

*Call graph*: calls 1 internal fn (_unknown).


##### `SkillRegistry._unknown`  (lines 377–380)

```
def _unknown(self, name: str) -> ValueError
```

**Purpose**: Creates a friendly error for an unknown skill name. It may include nearby known names to help the caller spot a typo.

**Data flow**: It reads all known skill names, compares them to the requested name, and returns a ValueError with optional suggestions.

**Call relations**: SkillRegistry.named and SkillRegistry._card use this whenever a requested skill cannot be found.

*Call graph*: calls 1 internal fn (known_names); called by 2 (_card, named); 1 external calls (get_close_matches).


##### `SkillRegistry._card`  (lines 382–389)

```
def _card(self, name: str) -> SkillCard
```

**Purpose**: Gets the routing card for a skill, whether it is a deployed skill or a member-created skill. A routing card is the lightweight record used for search and dependency walking.

**Data flow**: It first checks deployed skills and converts a deployed RuntimeSkill into a card. If not found there, it checks member_cards. If neither tier has the name, it raises an unknown-skill error.

**Call relations**: SkillRegistry.closure and its inner dependency walk call this whenever they need to resolve a skill name into dependency metadata.

*Call graph*: calls 1 internal fn (_unknown); called by 2 (closure, add).


##### `SkillRegistry.known_names`  (lines 391–394)

```
def known_names(self) -> frozenset[str]
```

**Purpose**: Returns every skill name this registry can resolve. This combines deployed skill names and member skill names.

**Data flow**: It reads the keys from by_name and member_cards, joins them into one frozen set, and returns that set.

**Call relations**: SkillRegistry._unknown uses this set to generate close-name suggestions for error messages.

*Call graph*: called by 1 (_unknown).


##### `SkillRegistry.all_cards`  (lines 396–401)

```
def all_cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: Returns routing cards for every loadable skill. This gives search code a single list to score without caring whether a skill is deployed or member-created.

**Data flow**: It converts deployed RuntimeSkill objects into SkillCards, appends existing member cards, and returns them as one tuple.

**Call relations**: Search and selection code can use this as the registry’s catalog view while leaving full workflow bodies untouched.


##### `SkillRegistry.bundled_skills`  (lines 403–406)

```
def bundled_skills(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Returns the deployed skills that are part of the fixed bundle. These are the skills expected to already be available through the terminal archive or sandbox image.

**Data flow**: It reads bundled_names and by_name, keeps only matching deployed RuntimeSkill objects, and returns them as a tuple.

**Call relations**: Serving code uses this when mounting shared skill surfaces for the runtime environment.

*Call graph*: called by 1 (_mount_shared_surfaces).


##### `SkillRegistry.closure`  (lines 408–432)

```
def closure(self, *names: str) -> tuple[LoadedRef, ...]
```

**Purpose**: Figures out the full set of skills needed for one load request. It includes the requested skills first, then follows their declared dependencies, making sure each skill appears only once.

**Data flow**: It takes one or more skill names, resolves each to a SkillCard, then walks each card’s depends list. It records whether each skill was directly requested or pulled by another skill and returns LoadedRef records in load order.

**Call relations**: The runtime engine calls this before loading skills. Its inner add helper continues the dependency walk while preventing cycles from causing endless recursion.

*Call graph*: calls 1 internal fn (_card); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 422–427)

```
def add(card: SkillCard, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency and its own dependencies to a closure walk. It is the recursive step that follows the dependency chain safely.

**Data flow**: It receives a SkillCard and the name of the skill that pulled it. If the card is already recorded, it stops; otherwise it records a LoadedRef and repeats the process for each dependency listed on that card.

**Call relations**: This helper lives inside SkillRegistry.closure and is called while resolving the transitive dependencies of requested skills.

*Call graph*: calls 1 internal fn (_card); 1 external calls (__init__).


##### `SkillRegistry.materialize`  (lines 434–456)

```
async def materialize(self, refs: Sequence[LoadedRef]) -> tuple[LoadedSkill, ...]
```

**Purpose**: Turns resolved skill references into full loaded skills with their workflow text and files. This is where member-created skills are actually fetched from storage.

**Data flow**: It receives LoadedRef records, looks up each deployed skill directly or asks the materializer callback for a member skill, checks that the returned skill has the expected name, wraps it as a LoadedSkill, and returns the tuple. If a skill disappeared, it raises an error.

**Call relations**: After SkillRegistry.closure has decided what should load, materialize provides the RuntimeSkill objects needed by loaded_context and sandbox installation.

*Call graph*: 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 458–467)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the list of skills shown in the prompt’s skill index. It includes top-level deployed skills and child skills that explicitly opt into being indexed.

**Data flow**: It reads the deployed skills in registry order, keeps skills with no parent or with indexed set to true, and returns pairs of skill name and description.

**Call relations**: Prompt-building code calls this when filling the skill index shown to the agent.

*Call graph*: called by 2 (_prompt_skill_index, prompt_index).


##### `SkillRegistry.merged_with`  (lines 469–490)

```
def merged_with(self, generated: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that includes generated deployed skills, such as skills produced during setup. Existing deployed skills win if names collide.

**Data flow**: It copies the current deployed skill map, adds generated skills that do not collide, logs and skips generated collisions, then removes any member cards shadowed by the resulting deployed tier. It returns a new SkillRegistry.

**Call relations**: Higher-level orchestration uses this when combining the base registry with runtime-generated skills while preserving the rule that deployed skills cannot be shadowed.

*Call graph*: 2 external calls (__init__, log).


##### `SkillRegistry.with_member`  (lines 492–511)

```
def with_member(self, cards: Sequence[SkillCard], materialize: SkillMaterializer) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that includes a bound agent’s saved member skills. Member skills are allowed only when they do not reuse a deployed skill name.

**Data flow**: It receives member SkillCards and a materializer callback, filters out cards whose names collide with deployed skills while logging those refusals, and returns a new SkillRegistry with the member tier attached.

**Call relations**: Turn setup code uses this to add agent-specific saved skills to the otherwise fixed deployed registry.

*Call graph*: 2 external calls (__init__, log).


##### `_loaded_tree`  (lines 517–535)

```
def _loaded_tree(loaded: Sequence[LoadedSkill]) -> str
```

**Purpose**: Builds a compact file tree showing every file loaded for a group of skills. This lets the agent see where files are available without printing their contents.

**Data flow**: It receives loaded skills, gathers all their file paths under the shared skills root, sorts them, emits directory lines only once, and returns a formatted text tree.

**Call relations**: loaded_context calls this after preparing workflow text so the final load result includes both instructions and a map of available files.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 538–551)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Builds the full text shown to the model after a skill load. It includes new workflow instructions, a note for workflows already present, and a file tree for everything loaded.

**Data flow**: It receives LoadedSkill objects and a set of skill names already in context. It renders prompt bodies only for new skills, records repeated names in one note, appends the loaded file tree, and returns the final string.

**Call relations**: The skill-loading tool and subagent preloading use this shared formatter so skills look the same whether loaded interactively or preloaded.

*Call graph*: calls 1 internal fn (_loaded_tree).


##### `_wire_skill`  (lines 554–561)

```
def _wire_skill(skill: RuntimeSkill) -> dict[str, object]
```

**Purpose**: Converts a RuntimeSkill into the wire format expected by the sandbox skill loader. “Wire format” here means a safe, serializable dictionary of file paths, encoded file contents, and a digest.

**Data flow**: It reads all skill files, checks and normalizes each file path relative to the skill root, base64-encodes the bytes into text, adds the skill content digest, and returns a dictionary ready to send.

**Call relations**: install_skill and load_skills call this before asking the sandbox to make skill files available inside the runtime.

*Call graph*: calls 3 internal fn (all_files, content_digest, root); called by 2 (install_skill, load_skills); 2 external calls (urlsafe_b64encode, contained_relative).


##### `install_skill`  (lines 564–568)

```
async def install_skill(sandbox: Sandbox, skill: RuntimeSkill) -> None
```

**Purpose**: Installs one materialized skill into the sandbox runtime. This is used when a single skill needs to be placed under the runtime skills directory.

**Data flow**: It receives a Sandbox and a RuntimeSkill, converts the skill with _wire_skill, sends it to sandbox.load_skills as a user skill, and checks that the sandbox reports a path for that skill. If not, it raises an error.

**Call relations**: It is the one-skill version of sandbox loading and hands the actual file placement off to the Sandbox object.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


##### `load_skills`  (lines 571–578)

```
async def load_skills(sandbox: Sandbox, loaded: Sequence[LoadedSkill]) -> None
```

**Purpose**: Installs a whole resolved skill load into the sandbox runtime. It separates already-bundled deployed skills from user-provided skill files so the sandbox can load each efficiently.

**Data flow**: It receives a Sandbox and LoadedSkill records. Bundled skills are sent by name and digest, while non-bundled skills are converted with _wire_skill and sent with their file contents. It asks the sandbox to load them and raises an error if any expected skill path is missing.

**Call relations**: After closure resolution and materialization, this function performs the sandbox side of making loaded skills available as files.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


### `core/src/ufo/harness/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a trust problem: people need to know what models are available, what they cost, how much text they can read at once, and what features they support. If that information were written by hand, it could easily become stale. Instead, this file generates the catalog from the same model records the runtime uses for routing requests, pricing, and prompt decisions.

Think of it like a restaurant menu printed from the kitchen’s actual inventory system. If the kitchen adds or removes an item, the menu reflects that automatically instead of depending on someone remembering to edit a document.

The main function, `model_catalog_skill`, takes a `ModelRegistry`, which is the system’s collection of known model specifications. It sorts those models by id, turns each one into a row in a Markdown table, and wraps the table in a `RuntimeSkill`. A `RuntimeSkill` is a piece of instruction-like content the runtime can load and show or use. The catalog includes each model’s provider, knowledge cutoff, context window, input and output price, whether reasoning is supported, and API surface.

The small helper `_per_mtok` formats prices into readable dollars per million tokens. Without this file, users would lose a reliable, runtime-accurate way to compare models.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This helper turns a stored model price into a human-readable dollar amount per million tokens. It exists so catalog prices appear as simple values like `$1.25` instead of internal micro-dollar numbers.

**Data flow**: It receives an integer price measured in micro-dollars per million tokens. It divides that by the constant number of micro-dollars in one dollar, formats the result with two decimal places, and returns a string ready to display in the catalog table.

**Call relations**: `model_catalog_skill` calls this helper while building each model row. It uses it once for the input price and once for the output price, so the final catalog shows costs in a form people can compare quickly.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function creates the actual model catalog skill from the live model registry. Someone would use it during startup to produce a readable, loadable list of all models this deployment can run.

**Data flow**: It receives a `ModelRegistry`, reads the model specifications inside it, sorts them by model id, and turns them into a Markdown table. For each model, it includes facts such as provider, knowledge cutoff, context window, pricing, reasoning support, and API surface. It then wraps that table, plus the skill name and description, into a `RuntimeSkill` object and returns it.

**Call relations**: At boot time, code that is assembling runtime skills can call this function after the model registry is available. While building the table, it hands raw price numbers to `_per_mtok` so they become readable dollar strings. At the end, it creates a `RuntimeSkill`, which is the object the rest of the runtime can load or present as the model catalog.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### `core/src/ufo/runtime/skills/selection.py`

`domain_logic` · `per-turn prompt construction`

An agent may have many saved skills, each like a small recipe card with a name, description, and sometimes a pinned flag. The model cannot be shown unlimited text, so this file acts like a careful librarian: it decides which cards go on the front desk, which go into a compact list, and which are only reachable by search.

If the member’s saved skills are small enough, they are folded directly into the normal system prompt beside built-in skills. If they are too large, this file builds a separate `<saved_skills>` block for the current turn. That block follows a clear priority order. Pinned skills are shown first with full descriptions. If the whole catalog fits, every skill gets a full line. If not, the file chooses a few query-relevant unpinned skills to describe fully, then still lists the remaining skills by name so the agent knows they exist. If even that is too much, it trims from the end and adds a note saying how many were omitted and that `skill_search` can find them.

Everything here is pure calculation: it reads skill cards and a query, returns strings and decisions, and does no disk, network, or database work. That matters because this runs every turn and must be fast, predictable, and unable to fail the conversation.

#### Function details

##### `_query_terms`  (lines 35–42)

```
def _query_terms(query: str) -> tuple[str, ...]
```

**Purpose**: Turns a user query into a small, clean set of searchable words. It removes punctuation boundaries, ignores very short words, avoids duplicates, and limits how much text it will examine so a huge pasted query does not become expensive.

**Data flow**: It receives a query string. It looks only at the first allowed chunk of that string, lowercases it in a Unicode-aware way, splits it around non-word characters, removes terms that are too short, and keeps only the first occurrence of each remaining term. It returns those terms as an ordered tuple.

**Call relations**: This is the shared preparation step for matching text. `lexical_score` uses it before counting query matches on one card, and `select_top_k` uses it once before ranking many cards.

*Call graph*: called by 2 (lexical_score, select_top_k).


##### `_term_hits`  (lines 45–47)

```
def _term_hits(terms: Sequence[str], card: SkillCard) -> int
```

**Purpose**: Counts how many prepared query terms appear in a skill card’s name or description. It is a simple relevance check: the more query words found, the more related the card looks.

**Data flow**: It receives a sequence of already-cleaned terms and one skill card. It combines the card’s name and description into one lowercase search area, checks each term against it, and returns the number of distinct terms that were found.

**Call relations**: This is the counting step used by `lexical_score` after `_query_terms` has prepared the query. Together they turn raw text plus a card into a small relevance number.

*Call graph*: called by 1 (lexical_score).


##### `lexical_score`  (lines 50–55)

```
def lexical_score(query: str, card: SkillCard) -> int
```

**Purpose**: Gives one skill card a simple text-match score for a query. Someone would use it to ask, in plain terms, “how many meaningful words from this query appear on this skill card?”

**Data flow**: It receives a raw query string and a skill card. It first asks `_query_terms` to clean and shorten the query into useful terms, then asks `_term_hits` to count how many of those terms appear in the card’s name or description. It returns that count as an integer.

**Call relations**: This function combines the two small helper steps into a public scoring tool. It does not choose among many cards itself; it just explains how relevant one card looks by the same lexical, or word-based, matching idea used elsewhere in this file.

*Call graph*: calls 2 internal fn (_query_terms, _term_hits).


##### `select_top_k`  (lines 58–66)

```
def select_top_k(query: str, cards: Sequence[SkillCard]) -> tuple[SkillCard, ...]
```

**Purpose**: Chooses the best few unpinned saved skills for the current query. It is used when there are too many skills to show with full descriptions, so only the most text-relevant ones get extra detail.

**Data flow**: It receives a query and a sequence of skill cards. It turns the query into searchable terms once, filters out pinned cards because those are already given priority elsewhere, ranks the remaining cards by how many query terms they match, and keeps only the configured maximum number. It returns those chosen cards as a tuple, preserving original order for ties.

**Call relations**: `member_visibility` calls this only when the full catalog will not fit in the saved-skills block. The chosen cards become the unpinned skills that receive full descriptive lines; the rest are still listed by name if there is room.

*Call graph*: calls 1 internal fn (_query_terms); called by 1 (member_visibility).


##### `skill_line`  (lines 69–71)

```
def skill_line(card: SkillCard) -> str
```

**Purpose**: Formats one skill card as a single readable list line. It also caps the line length so one long description cannot crowd out many other skills.

**Data flow**: It receives one skill card. It builds text in the form `- name: description`, then cuts that text to the configured maximum length. It returns the capped line as a string.

**Call relations**: This is the standard way this file displays a full skill card. `folds_into_prompt`, `prompt_index`, `catalog_fits`, and `member_visibility` all rely on it so their size checks and rendered text agree.

*Call graph*: called by 4 (catalog_fits, folds_into_prompt, member_visibility, prompt_index).


##### `folds_into_prompt`  (lines 74–78)

```
def folds_into_prompt(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Decides whether all member saved skills are small enough to be placed directly in the system prompt. This prevents a large personal skill library from silently taking over the most important prompt space.

**Data flow**: It receives a sequence of skill cards. It renders each card with `skill_line`, measures the combined text size using `_joined_size`, and compares that size with the prompt-fold budget. It returns `true` if the member skills fit and `false` otherwise.

**Call relations**: `prompt_index` calls this before deciding whether member cards should join the normal skill index. The same line format used here is also used later for actual display, so the decision is based on the real text that would be shown.

*Call graph*: calls 2 internal fn (_joined_size, skill_line); called by 1 (prompt_index).


##### `prompt_index`  (lines 81–93)

```
def prompt_index(registry: SkillRegistry) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the skill index entries that go into the system prompt for a turn. It always includes deploy-level skills, and it includes member saved skills only when that member tier is small enough.

**Data flow**: It receives a skill registry, which contains deploy skills and member cards. It reads the member cards, asks `folds_into_prompt` whether they fit, and gets the deploy index from `SkillRegistry.index()`. If the member cards do not fit, it returns only the deploy index. If they do fit, it appends each member card as a name plus capped description matching `skill_line`.

**Call relations**: This function is the bridge between the saved-skill visibility rules and the prompt-building code. It calls into the registry for the stable deploy index, then uses this file’s fold decision so large member catalogs are moved out of the system prompt and into the turn block instead.

*Call graph*: calls 3 internal fn (index, folds_into_prompt, skill_line).


##### `catalog_fits`  (lines 96–99)

```
def catalog_fits(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Checks whether every saved skill can be shown with a full description inside the member block. It answers the question, “Can we avoid ranking and just show the whole catalog?”

**Data flow**: It receives a sequence of skill cards. It renders each card with `skill_line`, measures how large those lines would be inside the `<saved_skills>` wrapper using `_block_size`, and compares that with the block budget. It returns a boolean result.

**Call relations**: This is a standalone version of a decision that `member_visibility` also makes in its one-pass flow. It uses the same line rendering and block-size helper so callers get the same answer the block renderer would use.

*Call graph*: calls 2 internal fn (_block_size, skill_line).


##### `member_visibility`  (lines 113–146)

```
def member_visibility(query: str, cards: Sequence[SkillCard]) -> MemberVisibility
```

**Purpose**: Makes the full per-turn decision for member saved skills: whether they fold into the system prompt, whether the full catalog fits in the turn block, and what block text should be shown. This is the main decision-maker in the file.

**Data flow**: It receives the current query and the member skill cards. It renders each card once with `skill_line`, measures whether the cards fit in the prompt and whether they fit in the saved-skills block, and returns an empty block if there are no cards or the cards already folded into the prompt. If the catalog fits, it renders pinned cards first and then the rest. If it does not fit, it calls `select_top_k` so the most query-relevant unpinned cards get full descriptions, lists other unpinned cards by name, trims from the end until the block fits, and adds a dropped-count note if needed. It returns a `MemberVisibility` value containing the decisions and final text.

**Call relations**: `member_block` calls this as the single source of truth for rendered saved-skill text. Inside, it coordinates the helper functions: `skill_line` creates consistent lines, `_joined_size` and `_block_size` enforce budgets, `select_top_k` chooses which crowded-out cards deserve descriptions, and `_render` wraps the finished lines.

*Call graph*: calls 5 internal fn (_block_size, _joined_size, _render, select_top_k, skill_line); called by 1 (member_block); 1 external calls (__init__).


##### `member_block`  (lines 149–157)

```
def member_block(query: str, cards: Sequence[SkillCard]) -> str
```

**Purpose**: Returns just the saved-skills block text for a turn. It is the simple public doorway for callers that only need the text to attach to the turn message.

**Data flow**: It receives the current query and skill cards. It delegates the real decision to `member_visibility`, then extracts and returns the `block` field from that result. If the skills folded into the system prompt or there is nothing to show, the returned string is empty.

**Call relations**: This is a convenience wrapper around `member_visibility`. Prompt or message-building code can call it without needing the extra fold and catalog-fit flags.

*Call graph*: calls 1 internal fn (member_visibility).


##### `_joined_size`  (lines 163–164)

```
def _joined_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how long a group of lines will be when joined with newline characters. It is a small size-checking helper used to enforce prompt and block budgets accurately.

**Data flow**: It receives a sequence of already-rendered lines. If there are lines, it adds each line’s length plus the newline between lines and returns the total joined size. If there are no lines, it returns zero.

**Call relations**: `folds_into_prompt` and `member_visibility` use this to decide whether text fits directly in the system prompt. `_block_size` also builds on it when measuring text inside the saved-skills wrapper.

*Call graph*: called by 3 (_block_size, folds_into_prompt, member_visibility).


##### `_block_size`  (lines 167–168)

```
def _block_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how large a saved-skills block would be after adding its opening and closing tags. This keeps budget checks tied to the real wrapper text that will be sent.

**Data flow**: It receives a sequence of rendered skill lines. It asks `_joined_size` for the size of the lines themselves, then adds the fixed size of the `<saved_skills>` and `</saved_skills>` wrapper plus needed newline spacing. It returns the total character count.

**Call relations**: `catalog_fits` uses this for the standalone full-catalog check, and `member_visibility` uses it in the per-turn decision flow. Because it depends on `_joined_size`, both prompt-style and block-style measurements share the same basic counting rule.

*Call graph*: calls 1 internal fn (_joined_size); called by 2 (catalog_fits, member_visibility).


##### `_render`  (lines 171–172)

```
def _render(lines: tuple[str, ...]) -> str
```

**Purpose**: Wraps saved-skill lines in the final XML-like block tags. The tags give the model a clear boundary around the saved-skills section.

**Data flow**: It receives a tuple of lines that have already been chosen and ordered. It places the opening tag first, then the lines, then the closing tag, joining everything with newlines. It returns the finished block string.

**Call relations**: `member_visibility` calls this after all selection, ordering, and trimming are complete. By keeping rendering here, the main function can focus on deciding what belongs in the block before this helper turns it into final text.

*Call graph*: called by 1 (member_visibility).
