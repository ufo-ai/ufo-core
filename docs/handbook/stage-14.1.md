# Local and repository Markdown sources  `stage-14.1`

This stage is the intake point for Markdown content. It lets the gbrain extension read pages from places people already keep notes: a folder on disk or a GitHub repository. Its job is shared behind-the-scenes support for syncing, not the main user-facing work. It turns files into “source pages,” meaning units of text the rest of the system can compare, search, and update.

The folder connector scans a local directory safely, finds Markdown files, turns each one into a page, and reports the whole current state as a snapshot. The GitHub connector does the same kind of job for a remote repository. To avoid needless work, it first checks whether the repository has changed, only downloads it when needed, and stores enough information to continue from the last known state. The pages helper is the common translator. It decides which files really count as Markdown pages, makes sure their text can be read safely, and picks a sensible title for each page. Together, these parts act like a loading dock for Markdown knowledge.

## Files in this stage

### Markdown source connectors
Local folder and GitHub repository connectors expose Markdown files as syncable gbrain pages using shared page parsing logic.

### `extensions/gbrain/ufo_ext_gbrain/folder.py`

`io_transport` · `source sync`

This backend is for a simple but useful case: someone has a directory full of Markdown notes, and the system needs to serve or index them as pages. Each Markdown file becomes one page, identified by its path relative to the chosen root folder. The file’s title is worked out elsewhere from its front matter or first heading.

The important safety rule is that the folder path may come from user-facing configuration, so this code does not blindly follow paths. It uses sandbox helpers to make sure the root is a real contained directory and that each file read stays inside that root. It also refuses to follow symbolic links, which are shortcut-like filesystem entries that could otherwise point outside the allowed folder.

On each sync, it performs a complete scan rather than trying to remember partial changes. That is like taking a fresh inventory of a shelf every time. Later parts of the system can skip unchanged pages by comparing content, and can remove pages whose files disappeared. If the whole root folder is missing or unsafe, the scan fails instead of returning an empty snapshot, so a temporary mount problem does not accidentally erase everything from the index.

It also enforces a total byte limit for Markdown content, preventing one oversized folder from overwhelming the sync.

#### Function details

##### `GbrainFolderSource.fetch`  (lines 35–40)

```
async def fetch(self, config: GbrainFolderConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Starts a sync for this folder source. It reads the local Markdown files, converts them into page objects, and returns them as one complete snapshot.

**Data flow**: It receives a folder configuration, an optional cursor, and authentication information. The cursor and auth are not used here because this source always does a fresh local scan. It runs the blocking folder read in a background thread, decodes each file’s bytes as Markdown text, turns each one into a page, and returns a SyncResult with those pages, no next cursor, and snapshot mode enabled.

**Call relations**: When the sync system asks this source for data, this function is the main entry point. It hands the slow disk-reading work to asyncio.to_thread so the async event loop is not blocked. After the files come back, it relies on decoded and markdown_page from the Gbrain pages code to turn raw file contents into page records, then packages everything in SyncResult for the caller.

*Call graph*: 4 external calls (__init__, to_thread, decoded, markdown_page).


##### `GbrainFolderSource._read`  (lines 43–57)

```
def _read(root: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: Scans the configured folder and reads only Markdown files that are safely inside it. It protects the system from unsafe paths, symbolic links, non-Markdown files, and folders that are too large.

**Data flow**: It takes an absolute root path and a maximum byte limit. First it turns the root into a safe contained root. Then it walks every path under that root in sorted order. For each item, it builds a relative page reference, skips symbolic links, skips non-files, and skips paths that are not Markdown. For accepted files, it opens them through the containment guard, reads their bytes, adds them to the running total, and raises StreamFault if the total Markdown content exceeds the limit. It returns a tuple of relative path and raw bytes pairs.

**Call relations**: This is the worker that fetch runs in a background thread during a sync. It calls contained_root before scanning and contained_file before reading so filesystem access stays inside the allowed directory. It asks is_markdown_path which files count as Markdown, and if the folder’s Markdown content is too large it raises StreamFault so the sync fails clearly instead of silently producing a partial result.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (contained_file, contained_root, is_markdown_path).


### `extensions/gbrain/ufo_ext_gbrain/git.py`

`io_transport` · `source sync polling and snapshot download`

This file is the GitHub-backed source for gbrain pages. Its job is to turn Markdown files from a GitHub repository into pages the rest of the system can sync. Without it, a workspace could not use a GitHub repo as a living document source.

The main flow is careful about GitHub limits. First it asks GitHub for the current commit identifier, called a SHA, for the configured branch. A SHA is like a precise version stamp for the repository. If the SHA has not changed, the file returns no pages and avoids downloading anything large. If there is no saved GitHub token, it also waits at least 15 minutes between these checks, because anonymous GitHub access has a much smaller request budget.

When the SHA is new, the file downloads a compressed tarball archive of the repository into a temporary file on disk. This is like receiving a zipped folder, saving it to a scratch location, then opening it to pick out only the Markdown files. It does not keep the whole archive in memory. It also enforces size limits for both the downloaded archive and the extracted Markdown, so a huge repository cannot overwhelm the process.

Finally, each Markdown file is decoded and converted into a page. The returned result is a full snapshot pinned to the exact SHA, which lets the wider sync system notice deleted files and soft-delete their old pages.

#### Function details

##### `_Cursor.encoded`  (lines 56–59)

```
def encoded(self) -> str
```

**Purpose**: This turns the saved GitHub sync position into a text string that can be stored for the next run. It records the last seen commit SHA, the optional GitHub cache tag, and when the repository was last checked.

**Data flow**: It starts with a cursor object holding a SHA, an optional ETag, and an optional checked time. It packages those values as sorted JSON text. The result is a stable string that can be saved and later read back.

**Call relations**: The main fetch flow creates or updates cursor objects as it checks GitHub. When it needs to return a new saved position to the sync driver, it calls this method so the cursor can travel as plain text.

*Call graph*: 1 external calls (dumps).


##### `_prior_cursor`  (lines 62–68)

```
def _prior_cursor(cursor: str | None) -> _Cursor | None
```

**Purpose**: This reads the previously saved sync position, if there is one. It protects the sync run from bad or outdated cursor text by treating unreadable cursor data as missing.

**Data flow**: It receives either a stored cursor string or nothing. If there is no string, it returns nothing. If there is a string, it tries to parse it into a cursor object; if parsing fails, it returns nothing instead of stopping the sync.

**Call relations**: GbrainGitSource.fetch calls this at the start of a sync. The returned cursor tells the rest of the flow what commit was seen last time and whether GitHub can be asked conditionally.

*Call graph*: called by 1 (fetch).


##### `_probe_due`  (lines 71–78)

```
def _probe_due(prior: _Cursor) -> bool
```

**Purpose**: This decides whether an anonymous GitHub check is allowed yet. It exists to avoid burning through GitHub's small unauthenticated request limit by probing too often.

**Data flow**: It receives the previous cursor and looks at its saved checked time. If the time is missing, invalid, or at least 15 minutes old, it returns true. If the last check was recent, it returns false.

**Call relations**: GbrainGitSource.fetch uses this only when no GitHub token is stored. If this says the probe is not due, fetch skips the GitHub request and returns the old cursor unchanged.

*Call graph*: called by 1 (fetch); 2 external calls (fromisoformat, now).


##### `GbrainGitSource.fetch`  (lines 91–127)

```
async def fetch(self, config: GbrainGitConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the main sync operation for a GitHub repository. It checks whether the repository changed, downloads it only when necessary, extracts Markdown files, and returns pages for the rest of the system to store.

**Data flow**: It receives the source configuration, the previous cursor string, and source authentication context. It reads whether a GitHub token is stored, decides whether it is allowed to check GitHub, asks for the current commit SHA, and compares that SHA with the saved one. If nothing changed, it returns no pages. If the repository is empty, it returns an empty full snapshot. If the repository changed, it downloads the tarball to a temporary file, extracts Markdown entries, deletes the temporary file, turns each entry into a page, and returns those pages with an updated cursor.

**Call relations**: This is the coordinator for the file. It calls _prior_cursor and _probe_due before making requests, uses _headers to prepare the GitHub client, asks _head for the current repository version, calls _spool_tarball to download changed content, and then runs _markdown_entries in a worker thread so archive extraction does not block the async event loop.

*Call graph*: calls 5 internal fn (_head, _headers, _spool_tarball, _prior_cursor, _probe_due); 6 external calls (__init__, to_thread, now, AsyncClient, decoded, markdown_page).


##### `GbrainGitSource._headers`  (lines 129–136)

```
async def _headers(self, token_stored: bool) -> dict[str, str]
```

**Purpose**: This builds the HTTP headers used for GitHub API requests. If a GitHub token is available, it adds it so private repositories can be read and the higher authenticated rate limit can be used.

**Data flow**: It receives a yes-or-no value saying whether the token exists. It starts with standard GitHub API headers. If the token is stored, it reads the token from credentials and adds an Authorization header. It returns the completed header dictionary.

**Call relations**: GbrainGitSource.fetch calls this while creating the GitHub HTTP client. The headers it returns are then used by later calls to _head and _spool_tarball through that client.

*Call graph*: called by 1 (fetch).


##### `GbrainGitSource._head`  (lines 138–151)

```
async def _head(self, client: httpx.AsyncClient, config: GbrainGitConfig, prior: _Cursor | None) -> _Cursor | None
```

**Purpose**: This asks GitHub for the exact commit SHA currently at the configured branch or default HEAD. It is the cheap change detector that prevents unnecessary repository downloads.

**Data flow**: It receives an HTTP client, repository configuration, and the previous cursor if one exists. It chooses the branch or HEAD, sends a GitHub request that asks for just the SHA, and includes the previous ETag when available so GitHub can answer 'not modified.' If GitHub says nothing changed, it returns nothing. If the repository is empty, it returns a cursor with an empty SHA. Otherwise it returns a cursor containing the new SHA and response ETag.

**Call relations**: GbrainGitSource.fetch calls this after creating the GitHub client. This function uses _refuse_client_error to turn bad GitHub client responses into clear stream faults before fetch decides whether to skip, snapshot an empty repo, or download the tarball.

*Call graph*: calls 1 internal fn (_refuse_client_error); called by 1 (fetch); 2 external calls (__init__, get).


##### `GbrainGitSource._spool_tarball`  (lines 153–171)

```
async def _spool_tarball(self, client: httpx.AsyncClient, config: GbrainGitConfig, sha: str) -> str
```

**Purpose**: This downloads a repository tarball for a specific commit SHA and saves it to a temporary file. It keeps the large archive out of memory and stops the download if it grows beyond the configured limit.

**Data flow**: It receives an HTTP client, repository configuration, and a SHA. It creates a temporary .tar.gz file, streams the GitHub tarball into it in chunks, counts the bytes received, and raises a stream fault if the limit is exceeded. If anything goes wrong, it closes and removes the temporary file. On success, it closes the file and returns its path.

**Call relations**: GbrainGitSource.fetch calls this only after _head reports a new commit. It relies on _refuse_client_error to reject bad GitHub responses, then hands the temporary file path back to fetch so the archive can be inspected by _markdown_entries.

*Call graph*: calls 2 internal fn (__init__, _refuse_client_error); called by 1 (fetch); 2 external calls (to_thread, stream).


##### `GbrainGitSource._markdown_entries`  (lines 174–192)

```
def _markdown_entries(repo: str, spool: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: This opens the downloaded repository archive and pulls out only Markdown files. It produces the raw file paths and bytes that will later be turned into pages.

**Data flow**: It receives the repository name, the temporary tarball path, and a maximum decompressed byte limit. It walks through the archive members, skips folders and non-Markdown files, removes the archive's top-level folder prefix from each path, reads each Markdown file's bytes, and keeps a running total. If the extracted Markdown is too large, it raises a stream fault. Otherwise it returns a sorted tuple of path-and-bytes pairs.

**Call relations**: GbrainGitSource.fetch runs this in a worker thread after _spool_tarball has saved the archive. Its output is passed to the page-building helpers that decode Markdown text and create page objects.

*Call graph*: calls 1 internal fn (__init__); 2 external calls (open, is_markdown_path).


##### `_refuse_client_error`  (lines 195–198)

```
def _refuse_client_error(response: httpx.Response, what: str) -> None
```

**Purpose**: This turns unsuccessful GitHub responses into clear sync errors. It gives friendlier context for normal client-side failures, such as a missing repository or forbidden access.

**Data flow**: It receives an HTTP response and a short description of what was being requested. If GitHub returned a client error status, it raises a StreamFault with the status code and description. For other error statuses, it asks the HTTP library to raise its standard exception. If the response is successful, it changes nothing and returns nothing.

**Call relations**: GbrainGitSource._head uses this after asking for the current commit SHA, and GbrainGitSource._spool_tarball uses it after starting the archive download. This keeps both request paths using the same error policy.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_head, _spool_tarball); 1 external calls (raise_for_status).


### `extensions/gbrain/ufo_ext_gbrain/pages.py`

`domain_logic` · `source sync/import`

This file is a small but important gate between a folder of files and the pages that gbrain can index or sync. Without it, the system might try to read hidden files like `.git` internals, binary files, or badly encoded text, and it might index page metadata as if it were normal content.

The flow is simple. First, a path is checked to make sure it is a visible Markdown file, such as `notes/today.md`, and not a dotfile or something inside a hidden folder. Next, the file bytes are decoded as UTF-8, which is the common text encoding used by Markdown. If decoding fails, the code raises a clear stream error that names the exact file, instead of leaving the user with a vague low-level text error.

Once the text is readable, the file is converted into a `Page`, which is the project’s common record for something that can be indexed. The title is chosen in a friendly order: first from YAML frontmatter, which is a metadata block at the top of some Markdown files; then from the first top-level `# Heading`; and finally from the file path if no better title exists. The frontmatter is removed from the page body so metadata does not pollute the searchable text.

#### Function details

##### `is_markdown_path`  (lines 16–22)

```
def is_markdown_path(relpath: str) -> bool
```

**Purpose**: This function answers the question: “Should this path be treated as a Markdown page?” It accepts only Markdown-looking file names and rejects hidden files or anything inside hidden folders.

**Data flow**: It receives a root-relative path written with forward slashes. It splits the path into its folder and file parts, rejects it if any part begins with a dot, then checks the file extension in a case-insensitive way. It returns `true` for visible `.md` or `.markdown` files, and `false` for everything else.

**Call relations**: This is the first filter in the page-reading flow. Other code can call it before opening files, so the rest of this module only sees likely Markdown documents instead of hidden project data, dotfiles, or unrelated files.

*Call graph*: 1 external calls (PurePosixPath).


##### `decoded`  (lines 25–31)

```
def decoded(source_ref: str, data: bytes) -> str
```

**Purpose**: This function turns raw file bytes into readable text, but only if the file is valid UTF-8. It gives a clear, file-specific error when the text cannot be decoded.

**Data flow**: It receives a source reference, usually a path-like name for the file, and the file’s raw bytes. It tries to decode those bytes as UTF-8 text. If decoding works, it returns the text; if it fails, it raises a `StreamFault`, which is a project-level error record that says which source file caused the problem.

**Call relations**: This sits after file selection and before Markdown parsing. It protects later steps like page creation from receiving broken text, and it hands failures upward in a form the sync stream can report clearly.

*Call graph*: calls 1 internal fn (__init__).


##### `markdown_page`  (lines 34–43)

```
def markdown_page(source_ref: str, text: str) -> Page
```

**Purpose**: This function converts one Markdown document into a `Page`, the shared object used by the source system. It also chooses the best title available and keeps frontmatter metadata out of the searchable body.

**Data flow**: It receives a source reference and the decoded Markdown text. It asks `_split_frontmatter` to separate optional metadata from the body, then builds a title from the frontmatter title, or from `_first_heading`, or finally from the source reference. It returns a new `Page` with the original source reference, cleaned body text, the `pages` stream name, and the chosen title.

**Call relations**: This is the main assembly step in the file. After a caller has found and decoded a Markdown file, it calls `markdown_page`; this function delegates title extraction to `_split_frontmatter` and `_first_heading`, then hands back the finished `Page` object for the broader sync or indexing pipeline.

*Call graph*: calls 2 internal fn (_first_heading, _split_frontmatter); 1 external calls (__init__).


##### `_split_frontmatter`  (lines 46–60)

```
def _split_frontmatter(text: str) -> tuple[str | None, str]
```

**Purpose**: This helper looks for a YAML frontmatter block at the very start of a Markdown file and extracts a `title` from it if one is present. YAML is a small structured text format often used for page metadata.

**Data flow**: It receives the full Markdown text. If the text does not start with a frontmatter delimiter, it returns no title and the original text. If a frontmatter block is found and can be parsed, it removes that block from the body and returns the stripped `title` value when it is a non-empty string. If the metadata is missing, malformed, or not a dictionary-like block, it leaves the text unchanged or returns no title as appropriate.

**Call relations**: This helper is called by `markdown_page` before the page is created. Its job is to separate metadata from real page content, so `markdown_page` can use the metadata for the title without indexing it as part of the body.

*Call graph*: called by 1 (markdown_page); 1 external calls (safe_load).


##### `_first_heading`  (lines 63–68)

```
def _first_heading(body: str) -> str | None
```

**Purpose**: This helper finds the first top-level Markdown heading, such as `# Project Notes`, and uses it as a fallback title. It gives untitled files a human-readable name when there is no frontmatter title.

**Data flow**: It receives the Markdown body text after any frontmatter has been removed. It scans the body line by line, trims whitespace, and looks for the first line that starts with `# `. If it finds one with text after the marker, it returns that heading text; otherwise it returns nothing.

**Call relations**: This helper is called by `markdown_page` only when frontmatter did not provide a title. It supplies the second choice in the title chain before `markdown_page` falls back to using the source reference.

*Call graph*: called by 1 (markdown_page).
