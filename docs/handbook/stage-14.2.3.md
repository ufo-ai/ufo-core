# Gbrain Markdown page sources  `stage-14.2.3`

This stage is the intake desk for Gbrain’s Markdown-based knowledge pages. It runs before the pages can be searched or used, gathering text either from a folder on the local computer or from a GitHub repository. The local-folder part reads every Markdown file under a chosen root folder and turns each one into a page, while checking paths carefully so a mistaken or malicious filename cannot make it read files outside that folder. The Git-backed part does the same job for a remote repository. It first checks whether the repository has changed, like asking “is there new mail?” before downloading, so it avoids unnecessary work. When needed, it refreshes the local copy and passes the Markdown files onward. The shared page-building code decides which files really count as Markdown pages, makes sure their text can be safely read, and gives each page a clear title. Together these pieces convert raw Markdown files into clean source pages Gbrain can index and search.

## Files in this stage

### Markdown source transports
Local folders and GitHub repositories are exposed as Markdown-backed page sources with path safety and refresh handling.

### `extensions/gbrain/ufo_ext_gbrain/folder.py`

`io_transport` · `sync`

This file is the bridge between a plain directory on disk and Gbrain's page-sync system. It solves a practical problem: people may already have notes or documents as Markdown files, and this backend lets the system serve those files as pages without needing a separate database or remote service.

The main class, GbrainFolderSource, performs a full scan of the configured folder each time it syncs. It walks through the folder, keeps only Markdown files, reads their bytes, and turns each file into a page. The page's reference is its path relative to the folder, so a file like docs/intro.md becomes a page keyed by that relative path.

The file is careful about safety. The root path must be absolute, and all file access goes through containment helpers. These act like a guardrail around the folder: symbolic links are not followed, and paths are checked so a malicious or mistaken path cannot escape into the rest of the host machine. There is also a total size limit for Markdown content. If the folder contains too much Markdown data, syncing stops with a clear fault instead of quietly consuming too much memory or time.

One important behavior is that this source returns a snapshot. In plain terms, every sync represents the current full folder contents. The wider sync system can then notice unchanged pages, update changed ones, and remove pages whose files disappeared.

#### Function details

##### `GbrainFolderSource.fetch`  (lines 35–40)

```
async def fetch(self, config: GbrainFolderConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the public sync method for the folder source. It reads the configured local folder, converts each Markdown file into a page, and returns the full current snapshot to the rest of the system.

**Data flow**: It receives a folder configuration, an unused cursor, and authentication information. It sends the blocking disk-reading work to a background thread so the async event loop is not stuck waiting on file I/O. It then decodes each file's bytes as text, builds page objects from them, and returns a SyncResult containing those pages, no next cursor, and a flag saying this is a complete snapshot.

**Call relations**: When the source system asks this backend to sync, it calls fetch. fetch delegates the low-level folder walking and safe file reading to GbrainFolderSource._read, through asyncio.to_thread. After that, it hands each file to decoded and markdown_page so the raw Markdown bytes become page objects, then wraps everything in SyncResult for the sync driver.

*Call graph*: 4 external calls (__init__, to_thread, decoded, markdown_page).


##### `GbrainFolderSource._read`  (lines 43–57)

```
def _read(root: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: This function safely scans a local directory and reads the Markdown files inside it. It exists to keep disk access contained, predictable, and limited in size.

**Data flow**: It receives an absolute root folder path and a maximum byte limit. First it turns the root into a checked, contained path. Then it walks every item under that root in sorted order. For each item, it skips symbolic links, non-files, and paths that are not Markdown. For each accepted file, it opens it through a containment check, reads only up to the allowed limit plus one byte, adds its size to a running total, and raises a StreamFault if all Markdown files together exceed the limit. It returns a tuple of pairs: each file's relative path and its raw bytes.

**Call relations**: GbrainFolderSource.fetch calls this when a sync begins. _read does the guarded file-system work by relying on contained_root and contained_file, uses is_markdown_path to decide what counts as a Markdown page, and raises StreamFault when the folder is too large for a safe sync.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (contained_file, contained_root, is_markdown_path).


### `extensions/gbrain/ufo_ext_gbrain/git.py`

`io_transport` · `source sync polling`

This file is the GitHub-backed source for gbrain. Its job is to keep pages in sync with Markdown files in a GitHub repository, without wasting network calls or memory. Think of it like a careful librarian: it first asks GitHub, “Has this shelf changed?” and only if the answer is yes does it bring back the whole box of books.

The source remembers a cursor, which is a small bookmark containing the last commit sha, an optional GitHub etag, and the time it last checked. A commit sha is GitHub’s unique label for one exact version of the repository. An etag is a web caching label that lets GitHub say “nothing changed” cheaply. If there is no stored GitHub token, the file also slows down checks so anonymous users do not burn through GitHub’s small rate limit.

When a check runs, it asks GitHub for the current commit. If nothing changed, it returns no pages. If the repository is empty, it returns an empty snapshot. If the commit is new, it downloads the repository tarball to a temporary file instead of holding the whole archive in memory. It then opens that archive, finds Markdown files, reads them, converts them into pages, and returns a full snapshot. Because it returns a snapshot, the larger sync driver can notice files that disappeared and mark their pages as deleted.

#### Function details

##### `_Cursor.encoded`  (lines 56–59)

```
def encoded(self) -> str
```

**Purpose**: This turns the sync bookmark into a stable text string that can be stored between runs. The bookmark records which repository version was last seen, plus cache and timing information.

**Data flow**: It starts with the cursor’s sha, etag, and checked_at values. It packs those values into a JSON string, with keys sorted so the output is predictable. The result is a plain string that can be saved and later read back.

**Call relations**: After a GitHub check, GbrainGitSource.fetch uses cursor objects to remember the new state. This method is the final step that turns that state into the next_cursor value returned to the sync system.

*Call graph*: 1 external calls (dumps).


##### `_prior_cursor`  (lines 62–68)

```
def _prior_cursor(cursor: str | None) -> _Cursor | None
```

**Purpose**: This reads the previously saved sync bookmark, if there is one. If the saved text is missing or broken, it safely treats it as no usable bookmark.

**Data flow**: It receives either a cursor string or nothing. If there is no string, it returns nothing. If there is a string, it tries to parse it into a _Cursor object; if parsing fails, it returns nothing instead of stopping the sync.

**Call relations**: GbrainGitSource.fetch calls this at the start of a sync run. The result decides whether the run can use GitHub caching, rate-limit pacing, and change detection from the previous run.

*Call graph*: called by 1 (fetch).


##### `_probe_due`  (lines 71–78)

```
def _probe_due(prior: _Cursor) -> bool
```

**Purpose**: This decides whether an anonymous GitHub check is allowed yet. It prevents repeated unauthenticated polling from quickly using up GitHub’s small anonymous request budget.

**Data flow**: It reads the prior cursor’s checked_at time. If that time is missing or invalid, it says a probe is due. Otherwise it compares that time with the current time and returns true only after the configured waiting period has passed.

**Call relations**: GbrainGitSource.fetch uses this only when there is no stored GitHub token. If this function says it is too soon, fetch returns immediately with no changes and keeps the old cursor.

*Call graph*: called by 1 (fetch); 2 external calls (fromisoformat, now).


##### `GbrainGitSource.fetch`  (lines 91–127)

```
async def fetch(self, config: GbrainGitConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the main sync routine for a GitHub Markdown repository. It checks whether the repository changed, downloads the repository only when necessary, extracts Markdown files, and returns them as pages.

**Data flow**: It receives the repository configuration, the previous cursor string, and source authentication information. It reads whether a GitHub token is stored, optionally waits out the anonymous probing interval, builds HTTP headers, asks GitHub for the current commit, and compares that commit with the previous one. If nothing changed, it returns an empty non-snapshot result. If the repository changed, it downloads the tarball to a temporary file, extracts Markdown entries, deletes the temporary file, converts the Markdown bytes into page objects, and returns a snapshot with a new cursor.

**Call relations**: This is the central coordinator in the file. It calls _prior_cursor to understand past state, _probe_due to protect anonymous rate limits, _headers to prepare GitHub requests, _head to learn the current commit, _spool_tarball to download the archive, and _markdown_entries to pull Markdown files from it. The SyncResult it returns is what the wider source driver uses to update, keep, or tombstone pages.

*Call graph*: calls 5 internal fn (_head, _headers, _spool_tarball, _prior_cursor, _probe_due); 6 external calls (__init__, to_thread, now, AsyncClient, decoded, markdown_page).


##### `GbrainGitSource._headers`  (lines 129–136)

```
async def _headers(self, token_stored: bool) -> dict[str, str]
```

**Purpose**: This builds the HTTP headers used when talking to GitHub. If a GitHub token is available, it includes it so private repositories can be read and the higher authenticated rate limit can be used.

**Data flow**: It receives a yes-or-no value saying whether the token is stored. It always adds GitHub’s expected API headers. If the token exists, it retrieves the token from credentials and adds an Authorization header. It returns the completed header dictionary.

**Call relations**: GbrainGitSource.fetch calls this before opening the GitHub HTTP client. The headers it returns are then used by later calls to _head and _spool_tarball through that client.

*Call graph*: called by 1 (fetch).


##### `GbrainGitSource._head`  (lines 138–151)

```
async def _head(self, client: httpx.AsyncClient, config: GbrainGitConfig, prior: _Cursor | None) -> _Cursor | None
```

**Purpose**: This asks GitHub for the exact commit currently being tracked. It uses GitHub’s caching support so GitHub can answer “not changed” without sending the commit again.

**Data flow**: It receives an HTTP client, repository configuration, and an optional prior cursor. It chooses the configured branch or HEAD, sends a GitHub request asking for just the commit sha, and includes the old etag when available. If GitHub says nothing changed, it returns nothing. If the repository is empty, it returns a cursor with an empty sha. Otherwise it checks for errors and returns a new cursor containing the sha and latest etag.

**Call relations**: GbrainGitSource.fetch calls this after setting up the HTTP client. Its answer controls the rest of the run: no download when unchanged, an empty snapshot for an empty repository, or a tarball download when the sha is new.

*Call graph*: calls 1 internal fn (_refuse_client_error); called by 1 (fetch); 2 external calls (__init__, get).


##### `GbrainGitSource._spool_tarball`  (lines 153–171)

```
async def _spool_tarball(self, client: httpx.AsyncClient, config: GbrainGitConfig, sha: str) -> str
```

**Purpose**: This downloads a repository archive for one exact commit and saves it to a temporary file. It streams the download in chunks so the process does not keep the whole archive in memory.

**Data flow**: It receives an HTTP client, repository configuration, and commit sha. It creates a temporary .tar.gz file, streams bytes from GitHub into that file, and counts how many bytes arrive. If GitHub returns an error or the archive is larger than the allowed limit, it cleans up the temporary file and raises a StreamFault. On success, it closes the file and returns its path.

**Call relations**: GbrainGitSource.fetch calls this only after _head reports a new commit. The returned file path is then passed to _markdown_entries for extraction, and fetch later deletes the temporary file.

*Call graph*: calls 2 internal fn (__init__, _refuse_client_error); called by 1 (fetch); 2 external calls (to_thread, stream).


##### `GbrainGitSource._markdown_entries`  (lines 174–192)

```
def _markdown_entries(repo: str, spool: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: This opens the downloaded repository archive and pulls out only Markdown files. It returns each file’s path inside the repository together with its raw bytes.

**Data flow**: It receives the repository name, the path to the temporary tarball, and a maximum allowed byte count. It walks through the archive members, skips directories and non-Markdown files, removes the archive’s top-level folder prefix from paths, reads each Markdown file, and tracks the total decompressed size. If the Markdown content is too large, it raises a StreamFault. Otherwise it returns the collected entries sorted by path.

**Call relations**: GbrainGitSource.fetch runs this in a worker thread after _spool_tarball finishes, because archive extraction and file reading are blocking work. Fetch then turns the returned Markdown entries into page objects.

*Call graph*: calls 1 internal fn (__init__); 2 external calls (open, is_markdown_path).


##### `_refuse_client_error`  (lines 195–198)

```
def _refuse_client_error(response: httpx.Response, what: str) -> None
```

**Purpose**: This turns bad GitHub responses into clear sync failures. It gives a friendlier message for client-side HTTP errors, such as a missing repository or forbidden access.

**Data flow**: It receives an HTTP response and a short description of what was being requested. If the response is a client error, it raises a StreamFault with the GitHub status code and context. Otherwise it asks the HTTP library to raise for any remaining unsuccessful status, and returns nothing if the response is acceptable.

**Call relations**: Both _head and _spool_tarball call this immediately after GitHub responds. It acts as the shared gatekeeper that stops the sync before later code tries to use an invalid response.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_head, _spool_tarball); 1 external calls (raise_for_status).


### Page extraction
Markdown files are filtered, read safely, titled, and converted into searchable gbrain source pages.

### `extensions/gbrain/ufo_ext_gbrain/pages.py`

`domain_logic` · `source sync and page ingestion`

This file is the shared “Markdown-to-page” helper for gbrain source backends. Its job is to keep source syncing focused on useful text files, then turn each accepted file into a Page object that the rest of the system can index or display.

First, it filters paths. Only files ending in .md or .markdown are accepted, and anything inside a hidden path segment, such as .git or .config, is skipped. This matters because a repository can contain many files that are not user-facing notes, and syncing those would add noise or even binary data.

Next, it makes sure file bytes are valid UTF-8 text. UTF-8 is the common text encoding used by Markdown. If a file cannot be decoded, the code raises a StreamFault with the file path, so the failure points to the exact bad file instead of producing a vague decoding error.

Finally, it builds a Page. It removes optional YAML frontmatter, which is a metadata block at the top of a Markdown file, like a label on a folder. If that block has a title, the title is used. Otherwise, it looks for the first top-level Markdown heading, such as “# Project Notes”. If neither exists, the file path becomes the title.

#### Function details

##### `is_markdown_path`  (lines 16–22)

```
def is_markdown_path(relpath: str) -> bool
```

**Purpose**: Decides whether a root-relative path should be treated as a Markdown page. It keeps hidden files and hidden folders out, so things like .git contents or dotfiles do not get synced as pages.

**Data flow**: It receives a path string such as "docs/intro.md". It splits the path into parts and rejects it if any part starts with a dot, then checks the file extension in a case-insensitive way. It returns true for accepted Markdown files and false for everything else.

**Call relations**: This is the first gate in the page-ingestion flow. It uses PurePosixPath to read the suffix cleanly from a slash-separated path, and callers can use the yes-or-no result before reading or decoding file contents.

*Call graph*: 1 external calls (PurePosixPath).


##### `decoded`  (lines 25–31)

```
def decoded(source_ref: str, data: bytes) -> str
```

**Purpose**: Turns raw file bytes into normal text, while giving a clear error if the file is not UTF-8. Someone would use it before parsing Markdown so the rest of the code can work with a string instead of bytes.

**Data flow**: It receives a source reference, usually the file path, and the file's raw bytes. It tries to decode those bytes as UTF-8. If decoding works, it returns the text; if decoding fails, it raises a StreamFault that names the source reference.

**Call relations**: This sits between file reading and Markdown page creation. When decoding fails, it creates a StreamFault so the wider sync process can report a stream-specific problem with the exact file attached.

*Call graph*: calls 1 internal fn (__init__).


##### `markdown_page`  (lines 34–43)

```
def markdown_page(source_ref: str, text: str) -> Page
```

**Purpose**: Builds a Page object from one Markdown file's path and text. It also chooses the best title available: frontmatter title first, then the first main heading, then the path.

**Data flow**: It receives a source reference and the already-decoded Markdown text. It asks _split_frontmatter to separate metadata from the body, then uses the metadata title if present; otherwise it asks _first_heading to look inside the body. It returns a Page containing the source reference, cleaned body text, stream name, and chosen title.

**Call relations**: This is the central assembly step for a Markdown source page. It calls _split_frontmatter so metadata is not indexed as page body, calls _first_heading only when it still needs a title, and then hands the final values into Page.__init__ to produce the object used downstream.

*Call graph*: calls 2 internal fn (_first_heading, _split_frontmatter); 1 external calls (__init__).


##### `_split_frontmatter`  (lines 46–60)

```
def _split_frontmatter(text: str) -> tuple[str | None, str]
```

**Purpose**: Looks for a YAML frontmatter block at the start of a Markdown file and extracts a title from it if possible. It also removes that metadata block from the page body so only the real content remains.

**Data flow**: It receives the full text of a Markdown file. If the text does not start with a frontmatter delimiter, it returns no title and the original text. If it finds a starting delimiter, it searches for the closing delimiter, tries to parse the lines between them as YAML, takes a non-empty string title if one exists, and returns that title plus the remaining body text. If parsing fails or no closing delimiter is found, it leaves the text unchanged and returns no title.

**Call relations**: markdown_page calls this before deciding the final Page title. This helper uses yaml.safe_load to read the metadata safely, then gives markdown_page both pieces it needs: a possible title and the body that should be indexed.

*Call graph*: called by 1 (markdown_page); 1 external calls (safe_load).


##### `_first_heading`  (lines 63–68)

```
def _first_heading(body: str) -> str | None
```

**Purpose**: Finds the first top-level Markdown heading in the page body and uses it as a fallback title. This helps pages get readable names even when they do not have frontmatter.

**Data flow**: It receives Markdown body text. It checks each line, trims surrounding spaces, and looks for a line that starts with "# ". If it finds one, it returns the heading text after the marker, unless that text is empty. If no suitable heading exists, it returns nothing.

**Call relations**: markdown_page calls this only after frontmatter did not provide a title. It supplies the second-best title choice before markdown_page falls back to using the source path.

*Call graph*: called by 1 (markdown_page).
