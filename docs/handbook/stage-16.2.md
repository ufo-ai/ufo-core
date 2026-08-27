# Gbrain Markdown Source Readers  `stage-16.2`

This stage is the intake area for the Gbrain extension. It is shared behind-the-scenes support used when Gbrain needs to sync pages from Markdown files, either from a folder on the user’s computer or from a GitHub repository online. Think of it as a set of loaders that collect raw documents and hand over tidy page records to the rest of the system.

The folder reader scans a local directory safely, finds Markdown files, and builds a complete snapshot of the pages available there. The GitHub reader does the same kind of job for a remote repository, but it is careful not to waste time or network traffic: it checks whether the repository has changed before downloading file contents again. Both readers rely on the page conversion utility. That shared code filters out paths that should not become pages, makes sure each file is readable UTF-8 text, and turns the Markdown into a page record with a sensible title. Together, these pieces make different Markdown sources look the same to Gbrain.

## Files in this stage

### Markdown Source Readers
Local-folder and GitHub-repository readers discover Markdown files and expose them as syncable source snapshots.

### `extensions/gbrain/ufo_ext_gbrain/folder.py`

`io_transport` · `source sync`

This file is the bridge between ordinary files on disk and Gbrain pages. Its job is simple in human terms: “look in this folder, find the Markdown notes, and publish them as pages.” Each file becomes one page, identified by its path relative to the chosen root folder. The page title is worked out later from the file content, such as front matter or the first heading.

The important safety idea here is containment. The folder path can come from outside the program, so the code must not accidentally read files elsewhere on the machine. It uses sandbox helpers to confirm the root is safe and to open each file only if it really stays inside that root. It also refuses to follow symbolic links, which are shortcut-like filesystem entries that could point outside the folder.

A sync is a full scan every time. That means this file does not try to remember what changed; it returns the current set of pages as a snapshot, and the sync driver can compare digests elsewhere to skip unchanged pages or remove pages whose files disappeared. If the whole root folder is missing or invalid, the scan fails rather than returning an empty set. That prevents a temporary mount problem from looking like “delete everything.” It also enforces a maximum total Markdown size, so one huge folder cannot overwhelm the sync.

#### Function details

##### `GbrainFolderSource.fetch`  (lines 35–40)

```
async def fetch(self, config: GbrainFolderConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the public sync method for the folder source. It asks for the folder contents, converts each Markdown file into a page object, and returns those pages as a complete snapshot.

**Data flow**: It receives a folder configuration, an unused cursor, and source authentication details. It sends the configured root path and byte limit to the blocking disk-reading helper, but runs that work in a background thread so the async event loop is not stuck waiting on file I/O. It then decodes each file’s bytes into text, turns that text into a page, and returns a SyncResult with all pages, no next cursor, and snapshot mode turned on.

**Call relations**: The sync system calls this when it wants fresh content from a local folder. This function hands the slow filesystem scan to GbrainFolderSource._read, then passes each file through decoded and markdown_page so the rest of the system receives normal page objects instead of raw bytes.

*Call graph*: 4 external calls (__init__, to_thread, decoded, markdown_page).


##### `GbrainFolderSource._read`  (lines 43–57)

```
def _read(root: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: This helper safely reads all Markdown files under a root folder. It is careful not to read outside the chosen folder, not to follow symlinks, and not to exceed the configured byte limit.

**Data flow**: It starts with a root path and a maximum allowed number of bytes. It first turns the root into a safe contained directory, then walks through everything beneath it in sorted order. For each item, it builds the relative page reference, skips symlinks, non-files, and non-Markdown paths, then opens the file through a containment guard and reads its bytes. It keeps a running total; if the Markdown content is too large, it raises a StreamFault. Otherwise it returns a tuple of relative paths paired with file bytes.

**Call relations**: GbrainFolderSource.fetch calls this when a sync begins. This helper does the low-level filesystem work, using contained_root and contained_file as safety checks and is_markdown_path to decide which files count. It returns raw entries for fetch to decode and turn into pages.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (contained_file, contained_root, is_markdown_path).


### `extensions/gbrain/ufo_ext_gbrain/git.py`

`io_transport` · `source polling and sync`

This file is the GitHub-backed page importer for gbrain. Its job is to look at a GitHub repository, find Markdown files, and return them as pages the rest of the system can sync. Without it, a workspace could not use a GitHub repo as a living document source.

The main idea is careful polling. First it asks GitHub for the current commit SHA, which is like a precise version number for the repository. If the SHA is the same as last time, it returns no pages because nothing changed. If GitHub says the old answer is still valid, it also skips the download. For anonymous GitHub access, it waits longer between checks to avoid burning through GitHub’s small unauthenticated rate limit.

When the repository has changed, it downloads GitHub’s tarball archive for that exact SHA into a temporary file on disk. This is important because large archives should not be held in memory all at once. It then opens the archive, pulls out only Markdown files, checks size limits, decodes each file, and builds page objects. The result is a full snapshot, so the wider sync system can notice files that disappeared from Git and mark those pages as deleted.

#### Function details

##### `_Cursor.encoded`  (lines 56–59)

```
def encoded(self) -> str
```

**Purpose**: Turns the saved GitHub sync position into a text string that can be stored for the next run. The cursor contains the last seen commit SHA, GitHub cache tag, and last check time.

**Data flow**: It starts with the cursor’s fields in memory. It packs them into a small JSON object with stable key ordering. The output is a string that the sync driver can keep and pass back later.

**Call relations**: After a sync check, GbrainGitSource.fetch uses cursor objects to remember what was seen. This method is the final step that converts that memory into the stored next_cursor value returned in SyncResult.

*Call graph*: 1 external calls (dumps).


##### `_prior_cursor`  (lines 62–68)

```
def _prior_cursor(cursor: str | None) -> _Cursor | None
```

**Purpose**: Reads the stored cursor from a previous sync run, if one exists and is valid. If the stored text is missing or broken, it safely treats it as if there was no previous state.

**Data flow**: It receives either a cursor string or nothing. If there is a string, it tries to parse it into a _Cursor object. It returns that object on success, or returns nothing when the input is absent or invalid.

**Call relations**: GbrainGitSource.fetch calls this at the start of every run. The result decides whether the fetch can compare against an earlier GitHub commit and whether rate-limit-friendly polling rules apply.

*Call graph*: called by 1 (fetch).


##### `_probe_due`  (lines 71–78)

```
def _probe_due(prior: _Cursor) -> bool
```

**Purpose**: Decides whether an unauthenticated GitHub source is allowed to check GitHub again yet. This protects users from quickly exhausting GitHub’s small anonymous request allowance.

**Data flow**: It receives the previous cursor and reads its checked_at timestamp. If the timestamp is missing or unreadable, it says a probe is due. Otherwise it compares that time with the current UTC time and returns true only after the configured waiting period has passed.

**Call relations**: GbrainGitSource.fetch uses this only when there is no stored GitHub token. If it says the probe is not due, fetch returns immediately without contacting GitHub.

*Call graph*: called by 1 (fetch); 2 external calls (fromisoformat, now).


##### `GbrainGitSource.fetch`  (lines 91–127)

```
async def fetch(self, config: GbrainGitConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one complete sync attempt for a GitHub repository. It decides whether to skip, check, download, extract, and return Markdown pages.

**Data flow**: It receives repository configuration, the previous cursor string, and authentication context. It reads whether a GitHub token is stored, possibly delays anonymous checks, asks GitHub for the current commit, downloads the repository archive only when needed, extracts Markdown files, converts them into pages, and returns a SyncResult with pages, a new cursor, and whether this is a full snapshot.

**Call relations**: This is the main method the source driver calls during polling. It uses _prior_cursor and _probe_due to understand past state, _headers to prepare GitHub requests, _head to check the current commit, _spool_tarball to download changed content, and _markdown_entries to read Markdown from the downloaded archive.

*Call graph*: calls 5 internal fn (_head, _headers, _spool_tarball, _prior_cursor, _probe_due); 6 external calls (__init__, to_thread, now, AsyncClient, decoded, markdown_page).


##### `GbrainGitSource._headers`  (lines 129–136)

```
async def _headers(self, token_stored: bool) -> dict[str, str]
```

**Purpose**: Builds the HTTP headers used for GitHub API calls. If a GitHub token is available, it adds it so private repositories and higher rate limits can be used.

**Data flow**: It receives a yes-or-no value saying whether the token exists. It starts with standard GitHub API headers, then, when allowed, reads the token from credential storage and adds an Authorization header. It returns the finished header dictionary.

**Call relations**: GbrainGitSource.fetch calls this while creating the GitHub HTTP client. The resulting headers are then used by _head and _spool_tarball through that client.

*Call graph*: called by 1 (fetch).


##### `GbrainGitSource._head`  (lines 138–151)

```
async def _head(self, client: httpx.AsyncClient, config: GbrainGitConfig, prior: _Cursor | None) -> _Cursor | None
```

**Purpose**: Asks GitHub for the exact commit SHA currently being tracked. This is the cheap check that tells the sync whether the repository changed.

**Data flow**: It receives an HTTP client, repository configuration, and the previous cursor. It chooses the configured branch or HEAD, sends a GitHub request asking for only the SHA, and includes the old ETag when available so GitHub can answer 'not changed.' It returns no new cursor when unchanged, an empty-SHA cursor for an empty repository, or a cursor containing the new SHA and ETag.

**Call relations**: GbrainGitSource.fetch calls this before deciding whether to download the archive. It relies on _refuse_client_error to turn GitHub client-side errors, such as missing repositories or denied access, into source sync faults.

*Call graph*: calls 1 internal fn (_refuse_client_error); called by 1 (fetch); 2 external calls (__init__, get).


##### `GbrainGitSource._spool_tarball`  (lines 153–171)

```
async def _spool_tarball(self, client: httpx.AsyncClient, config: GbrainGitConfig, sha: str) -> str
```

**Purpose**: Downloads a repository archive for a specific commit into a temporary file. It streams the data in chunks so the whole tarball does not sit in memory.

**Data flow**: It receives an HTTP client, repository configuration, and commit SHA. It creates a temporary .tar.gz file, streams bytes from GitHub into that file, counts how much has arrived, and stops with a StreamFault if the archive is too large. It returns the temporary file path, or cleans up the file if anything goes wrong.

**Call relations**: GbrainGitSource.fetch calls this only after _head shows a new repository version. The temporary file it returns is passed into _markdown_entries for extraction, and fetch deletes it afterward.

*Call graph*: calls 2 internal fn (__init__, _refuse_client_error); called by 1 (fetch); 2 external calls (to_thread, stream).


##### `GbrainGitSource._markdown_entries`  (lines 174–192)

```
def _markdown_entries(repo: str, spool: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: Opens the downloaded GitHub archive and pulls out the Markdown files. It ignores folders and non-Markdown files, and protects the system from too much decompressed content.

**Data flow**: It receives the repository name, the temporary archive path, and a maximum byte limit. It walks through the tar archive, strips off GitHub’s top-level archive folder name, keeps only Markdown file paths, reads their bytes, and tracks the total extracted size. It returns a sorted tuple of file path and byte-content pairs.

**Call relations**: GbrainGitSource.fetch runs this in a background thread after downloading the tarball, because archive reading is blocking work. Its output is then decoded and passed to markdown_page to become the pages returned in the sync result.

*Call graph*: calls 1 internal fn (__init__); 2 external calls (open, is_markdown_path).


##### `_refuse_client_error`  (lines 195–198)

```
def _refuse_client_error(response: httpx.Response, what: str) -> None
```

**Purpose**: Turns bad HTTP responses from GitHub into clear sync failures. It gives friendlier source-specific errors for client problems while still raising normal HTTP errors for other failures.

**Data flow**: It receives an HTTP response and a short description of what was being requested. If GitHub returned a client error, such as 404 or 403, it raises StreamFault with the status and context. Otherwise it asks the HTTP library to raise for any remaining error status.

**Call relations**: GbrainGitSource._head uses this after checking the commit endpoint, and GbrainGitSource._spool_tarball uses it after starting the archive download. This keeps GitHub error handling consistent in both parts of the sync.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_head, _spool_tarball); 1 external calls (raise_for_status).


### Page Conversion Utilities
Shared Markdown-to-page logic filters files, validates text content, and derives clean page records for syncing and indexing.

### `extensions/gbrain/ufo_ext_gbrain/pages.py`

`domain_logic` · `source sync / indexing`

This file is the shared Markdown-to-page doorway for the gbrain source backends. Its job is to make sure only suitable Markdown files enter the system, then convert each file into a standard Page object the rest of the application can understand.

First, it decides whether a path should be considered at all. It accepts files ending in .md or .markdown, but rejects anything inside hidden folders or with hidden path parts, such as .git or dotfiles. That keeps repository internals and accidental private files out of a sync.

Next, it treats file bytes as UTF-8 text. UTF-8 is the common text encoding used on the web and in most modern tools. If a file cannot be decoded, the file raises a StreamFault with the path included, so the failure tells the user which file is the problem.

Finally, it builds a Page. It removes optional YAML frontmatter, which is a small metadata block at the top of many Markdown files. If that block has a title, the page uses it. If not, it looks for the first top-level Markdown heading, like “# Project Plan”. If neither exists, it falls back to the file path. In short, this file is like a mailroom clerk: it rejects the wrong envelopes, checks the letter is readable, removes the cover sheet, and labels the document before passing it on.

#### Function details

##### `is_markdown_path`  (lines 16–22)

```
def is_markdown_path(relpath: str) -> bool
```

**Purpose**: This function decides whether a root-relative path points to a Markdown file that should be synced. It deliberately excludes hidden files and hidden directories, so things like .git data and dotfiles do not become pages.

**Data flow**: It receives a path string using forward slashes. It splits the path into parts, rejects it if any part starts with a dot, then checks the file extension in a case-insensitive way. It returns true for accepted Markdown paths and false for everything else.

**Call relations**: This is the first gate before reading file contents. When a source backend is walking through files, it can call this function to decide which paths are worth opening; internally it uses PurePosixPath to read the suffix consistently from a POSIX-style path.

*Call graph*: 1 external calls (PurePosixPath).


##### `decoded`  (lines 25–31)

```
def decoded(source_ref: str, data: bytes) -> str
```

**Purpose**: This function converts raw file bytes into text, but only if the bytes are valid UTF-8. It gives a clearer project-level error when a file is not readable text.

**Data flow**: It receives a source reference, usually the path or name of the file, plus the file's raw bytes. It tries to decode the bytes as UTF-8. On success, it returns the decoded string; on failure, it raises a StreamFault that names the offending source reference.

**Call relations**: This sits between file reading and page creation. A backend can call it after loading bytes from storage; if decoding succeeds, the resulting text can be passed on to markdown_page, and if it fails the sync stops with a useful file-specific fault.

*Call graph*: calls 1 internal fn (__init__).


##### `markdown_page`  (lines 34–43)

```
def markdown_page(source_ref: str, text: str) -> Page
```

**Purpose**: This function turns one Markdown text file into a Page record for the rest of the system. It also chooses the best available title and keeps frontmatter metadata out of the indexed body.

**Data flow**: It receives the source reference and the decoded Markdown text. It asks _split_frontmatter to separate any YAML metadata block from the real body. Then it chooses a title: first a frontmatter title, then the first top-level heading found by _first_heading, and finally the source reference as a fallback. It returns a new Page with the source reference, cleaned body, page stream name, and chosen title.

**Call relations**: This is the main assembly step in the file. After a backend has accepted a path and decoded its bytes, it calls markdown_page to produce the standard Page object; markdown_page delegates the metadata parsing to _split_frontmatter and heading lookup to _first_heading before constructing the Page.

*Call graph*: calls 2 internal fn (_first_heading, _split_frontmatter); 1 external calls (__init__).


##### `_split_frontmatter`  (lines 46–60)

```
def _split_frontmatter(text: str) -> tuple[str | None, str]
```

**Purpose**: This helper looks for an optional YAML frontmatter block at the top of a Markdown file and extracts a title from it. It also removes that metadata block from the page body so it is not treated as normal content.

**Data flow**: It receives the full Markdown text. If the text does not start with a frontmatter delimiter, it returns no title and the original text. If a frontmatter block is present and can be parsed as YAML, it reads the title field when it is a non-empty string, removes the frontmatter from the body, and returns both. If the YAML is invalid or no closing delimiter is found, it safely falls back to no title and the original text.

**Call relations**: This is called by markdown_page before the Page is created. It uses yaml.safe_load to read the metadata block, but only markdown_page decides how the extracted title fits into the title fallback chain.

*Call graph*: called by 1 (markdown_page); 1 external calls (safe_load).


##### `_first_heading`  (lines 63–68)

```
def _first_heading(body: str) -> str | None
```

**Purpose**: This helper finds the first top-level Markdown heading in the page body. It provides a natural title when the file has no frontmatter title.

**Data flow**: It receives the Markdown body after any frontmatter has been removed. It scans the body line by line, trims surrounding spaces, and looks for a line that starts with '# '. If it finds one, it returns the heading text without the marker; if not, it returns nothing.

**Call relations**: This is called by markdown_page only after frontmatter has been checked. It supplies the second choice in the title chain, before markdown_page falls back to using the source reference.

*Call graph*: called by 1 (markdown_page).
