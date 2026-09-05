# Markdown and repository-backed page sources  `stage-14.1.1`

This stage is an intake area for the gbrain extension. Its job is to turn Markdown files into standard Page records, so the rest of the system can index or sync them without caring where they came from. It sits before the common source-sync pipeline, like a loading dock that labels and checks boxes before they enter a warehouse.

The folder source reads Markdown files from a local directory. It treats each file as one page and uses safety checks so the scan cannot wander outside the chosen folder. The GitHub source does the same kind of work for a repository online. It first checks whether the repository has changed, so it can avoid downloading it unnecessarily. When needed, it fetches the files and finds the Markdown pages.

The pages helper is the shared cleaner and formatter. It filters out paths that should not become pages, confirms the file is readable text, and picks a useful title. Together, these parts make different storage places look the same to the rest of the system.

## Files in this stage

### Markdown page sources
Local folders and GitHub repositories are scanned for Markdown files, which are then normalized into Page records for the shared sync pipeline.

### `extensions/gbrain/ufo_ext_gbrain/folder.py`

`io_transport` · `sync`

This file is the bridge between a plain directory on disk and the system’s idea of “pages.” It solves a simple problem: people may already have notes or documents as Markdown files, and the system needs to read them without letting a user-supplied path escape into other parts of the server’s filesystem.

The main type is `GbrainFolderSource`. When a sync happens, it scans the configured root folder, finds Markdown files, reads their bytes, and turns each one into a page. The page key is the file path relative to the root folder, so a file like `notes/today.md` becomes a page with that reference.

The file is deliberately cautious. The configured root must be an absolute path. Before reading, it passes the root through a containment guard, which is like checking that someone stays inside a fenced yard. It also refuses to follow symbolic links, because links can point outside the folder. If the folder contains too much Markdown data, it raises a `StreamFault` instead of silently continuing. That protects the sync from unexpectedly huge inputs.

Each sync is a full snapshot: the source reports all current pages and no cursor for incremental progress. Other parts of the system can then compare page contents, skip unchanged pages, and remove pages whose files disappeared.

#### Function details

##### `GbrainFolderSource.fetch`  (lines 35–40)

```
async def fetch(self, config: GbrainFolderConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the public sync method for the folder source. It reads the configured folder, turns each Markdown file into a page, and returns a complete snapshot of what exists now.

**Data flow**: It receives a folder configuration, an unused cursor, and authentication information. It sends the blocking disk scan to a background thread so the async event loop is not stalled. The raw file bytes come back as path-and-content pairs, then each pair is decoded and converted into a page. It returns a `SyncResult` containing those pages, no next cursor, and a flag saying this is a full snapshot.

**Call relations**: The sync driver calls this when it wants fresh content from the local folder backend. `fetch` delegates the actual filesystem walk to `GbrainFolderSource._read`, then hands each file’s contents to `decoded` and `markdown_page` so the rest of the system receives normal page objects rather than raw files.

*Call graph*: 4 external calls (__init__, to_thread, decoded, markdown_page).


##### `GbrainFolderSource._read`  (lines 43–57)

```
def _read(root: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: This helper safely scans a local folder and reads only Markdown files from inside it. It exists to keep all filesystem safety checks and size limits in one place.

**Data flow**: It receives a root directory path and a maximum byte limit. First it confirms the root is safely contained, then walks through the folder in sorted order. For each item, it builds the relative page reference, skips symbolic links, non-files, and non-Markdown paths, then opens the file through a containment-checked reader. It adds up the bytes read, raises a `StreamFault` if the total is too large, and finally returns a tuple of relative paths paired with file bytes.

**Call relations**: `GbrainFolderSource.fetch` calls this inside a background thread because reading many files can block. `_read` relies on `contained_root` and `contained_file` to prevent path escape, uses `is_markdown_path` to decide which files count as pages, and raises `StreamFault` when the folder is too large for a safe sync.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (contained_file, contained_root, is_markdown_path).


### `extensions/gbrain/ufo_ext_gbrain/git.py`

`io_transport` · `source polling and sync`

This file is the GitHub-backed source for gbrain pages. Its job is to watch one repository and keep the system’s copy of its Markdown files up to date. Without it, a workspace could not use a GitHub repo as a page collection, and deleted files in Git would not be reflected as deleted pages in the system.

The main idea is to avoid downloading the whole repository every time. First, it asks GitHub for the current commit identifier, called a SHA. A SHA is like a precise version label for the repository. If that label has not changed, the file returns “nothing new.” If it has changed, it downloads a compressed archive of the repository, saves it to a temporary file, and then reads only the Markdown files from that archive.

It also remembers a small cursor between runs. The cursor stores the last seen SHA, GitHub’s cache tag, and when the source was last checked. For unauthenticated public access, it deliberately slows down repeat checks to avoid quickly using up GitHub’s small anonymous rate limit. If a token is stored, it can check more often and can also read private repositories.

The file is careful with large data. It streams the archive to disk instead of keeping the whole download in memory, and it refuses archives or extracted Markdown content that exceed the configured size limit.

#### Function details

##### `_Cursor.encoded`  (lines 56–59)

```
def encoded(self) -> str
```

**Purpose**: Turns the saved sync position into a compact text string that can be stored between runs. This lets the next sync know which repository version was last seen and when GitHub was last checked.

**Data flow**: It starts with the cursor’s SHA, optional cache tag, and optional check time. It packages those values as JSON text with stable key ordering. The result is a string that can be saved as the next cursor.

**Call relations**: After a fetch decides what the next remembered state should be, it uses this method to hand that state back in the SyncResult. Later, a future fetch reads that same text through _prior_cursor.

*Call graph*: 1 external calls (dumps).


##### `_prior_cursor`  (lines 62–68)

```
def _prior_cursor(cursor: str | None) -> _Cursor | None
```

**Purpose**: Reads the previously saved cursor, if there is one, and turns it back into a cursor object. If the saved text is missing or invalid, it safely treats it as if there were no previous state.

**Data flow**: It receives either stored cursor text or nothing. If there is text, it tries to parse it as the expected cursor shape. It returns a usable _Cursor object when parsing works, or None when there is no trustworthy prior cursor.

**Call relations**: GbrainGitSource.fetch calls this at the start of a sync. The result decides whether the sync can make a conditional GitHub request, skip an unauthenticated check for a while, or must behave like a first-time sync.

*Call graph*: called by 1 (fetch).


##### `_probe_due`  (lines 71–78)

```
def _probe_due(prior: _Cursor) -> bool
```

**Purpose**: Decides whether an unauthenticated GitHub check is allowed yet. This protects the system from spending GitHub’s small anonymous request budget too quickly.

**Data flow**: It reads the last checked time stored in the cursor. If that time is missing, malformed, or old enough, it returns true. If the last check was recent, it returns false so the source can wait longer.

**Call relations**: GbrainGitSource.fetch uses this only when there is no stored GitHub token. If it says the check is not due, fetch returns immediately with no pages and keeps the old cursor.

*Call graph*: called by 1 (fetch); 2 external calls (fromisoformat, now).


##### `GbrainGitSource.fetch`  (lines 91–127)

```
async def fetch(self, config: GbrainGitConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Performs one sync attempt for a configured GitHub repository. It checks whether the repository changed, downloads it only when needed, extracts Markdown files, and returns them as pages.

**Data flow**: It receives the source configuration, the previous cursor text, and authentication context. It reads whether a GitHub token is stored, may skip a too-soon anonymous probe, asks GitHub for the current repository SHA, and compares that SHA with the prior one. If nothing changed, it returns an empty non-snapshot result. If the repo is empty, it returns an empty snapshot. If there is a new SHA, it downloads the tarball to a temporary file, extracts Markdown entries, converts them into pages, deletes the temporary file, and returns a full snapshot plus an updated cursor.

**Call relations**: This is the main doorway used by the source driver when it polls the GitHub source. It delegates cursor parsing to _prior_cursor, anonymous rate-limit timing to _probe_due, request headers to _headers, SHA lookup to _head, archive download to _spool_tarball, and Markdown extraction to _markdown_entries.

*Call graph*: calls 5 internal fn (_head, _headers, _spool_tarball, _prior_cursor, _probe_due); 6 external calls (__init__, to_thread, now, AsyncClient, decoded, markdown_page).


##### `GbrainGitSource._headers`  (lines 129–136)

```
async def _headers(self, token_stored: bool) -> dict[str, str]
```

**Purpose**: Builds the HTTP headers used for GitHub API requests. If a GitHub token is stored, it adds it so private repositories and the larger authenticated rate limit can be used.

**Data flow**: It receives a yes-or-no value saying whether the token exists. It always creates headers for GitHub’s JSON API and API version. If a token is stored, it retrieves the token from credentials and adds an Authorization header. It returns the complete header dictionary.

**Call relations**: GbrainGitSource.fetch calls this while creating the HTTP client. The returned headers are then applied to the later GitHub requests made by _head and _spool_tarball.

*Call graph*: called by 1 (fetch).


##### `GbrainGitSource._head`  (lines 138–151)

```
async def _head(self, client: httpx.AsyncClient, config: GbrainGitConfig, prior: _Cursor | None) -> _Cursor | None
```

**Purpose**: Asks GitHub which exact commit the source should sync. This is the cheap change check that avoids downloading the full repository when the tracked branch has not moved.

**Data flow**: It receives an HTTP client, repository configuration, and optional prior cursor. It chooses the configured branch or the repository default, sends a GitHub request asking for just the commit SHA, and may include the prior cache tag so GitHub can answer “not changed.” It returns None if GitHub says nothing changed, a cursor with an empty SHA if the repository is empty, or a cursor containing the new SHA and cache tag.

**Call relations**: GbrainGitSource.fetch calls this before deciding whether to download anything. This function uses _refuse_client_error to turn GitHub client-side failures into clear stream errors instead of silently continuing.

*Call graph*: calls 1 internal fn (_refuse_client_error); called by 1 (fetch); 2 external calls (__init__, get).


##### `GbrainGitSource._spool_tarball`  (lines 153–171)

```
async def _spool_tarball(self, client: httpx.AsyncClient, config: GbrainGitConfig, sha: str) -> str
```

**Purpose**: Downloads the repository archive for a specific SHA into a temporary file. It streams the data in chunks so the process does not need to hold the whole compressed archive in memory.

**Data flow**: It receives an HTTP client, repository configuration, and the exact SHA to download. It creates a temporary tar.gz file, streams bytes from GitHub into that file, and counts how many bytes arrive. If the download grows beyond the configured maximum, it raises a StreamFault and cleans up the partial file. On success, it closes the file and returns the temporary path.

**Call relations**: GbrainGitSource.fetch calls this only after _head reports a new repository version. The returned file path is then passed to _markdown_entries for extraction, and fetch deletes the temporary file afterward.

*Call graph*: calls 2 internal fn (__init__, _refuse_client_error); called by 1 (fetch); 2 external calls (to_thread, stream).


##### `GbrainGitSource._markdown_entries`  (lines 174–192)

```
def _markdown_entries(repo: str, spool: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: Reads a downloaded repository archive and pulls out the Markdown files. It returns each file’s path inside the repository together with its raw bytes.

**Data flow**: It receives the repository name, the temporary archive path, and a maximum allowed byte count. It opens the tar.gz archive, walks through its files, ignores directories and non-Markdown paths, reads Markdown contents, and keeps a running total of extracted bytes. If the extracted Markdown becomes too large, it raises a StreamFault. Otherwise, it returns the entries sorted by path.

**Call relations**: GbrainGitSource.fetch runs this in a background thread after the tarball has been downloaded, because archive extraction and file reading are blocking work. Its output is then decoded and converted into page objects by fetch.

*Call graph*: calls 1 internal fn (__init__); 2 external calls (open, is_markdown_path).


##### `_refuse_client_error`  (lines 195–198)

```
def _refuse_client_error(response: httpx.Response, what: str) -> None
```

**Purpose**: Turns bad HTTP responses from GitHub into explicit sync failures. This keeps the rest of the code from treating errors like missing access or invalid repositories as valid content.

**Data flow**: It receives an HTTP response and a short description of what was being requested. If GitHub returned a client error, such as a not-found or permission response, it raises a StreamFault with a clear message. For other unsuccessful statuses, it asks the HTTP library to raise its normal error. If the response is acceptable, it returns nothing.

**Call relations**: GbrainGitSource._head uses this after asking for the current SHA, and GbrainGitSource._spool_tarball uses it after starting the archive download. It acts as the shared gatekeeper that stops failed GitHub responses before later steps try to use them.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_head, _spool_tarball); 1 external calls (raise_for_status).


### `extensions/gbrain/ufo_ext_gbrain/pages.py`

`domain_logic` · `source sync and page ingestion`

This file is the shared doorway for Markdown pages used by the gbrain backends. Its job is to make sure only suitable files become source pages. Without it, the system might try to read hidden files like `.git` data, choke on non-text files, or index frontmatter metadata as if it were the actual article body.

The flow is simple. First, `is_markdown_path` decides whether a path points to a visible Markdown file. It rejects anything inside a hidden folder or with a hidden filename, and only accepts `.md` or `.markdown` endings. Next, `decoded` turns raw file bytes into UTF-8 text. UTF-8 is the common text encoding used here; if a file is not valid UTF-8, it raises a clear `StreamFault` that names the problem file.

Finally, `markdown_page` builds a `Page`, which is the system’s standard record for one piece of source content. It removes optional YAML frontmatter, which is a metadata block at the top of many Markdown files, like a label on a folder. If that metadata has a `title`, it uses it. If not, it looks for the first top-level Markdown heading. If neither exists, it uses the file path as the title.

#### Function details

##### `is_markdown_path`  (lines 16–22)

```
def is_markdown_path(relpath: str) -> bool
```

**Purpose**: This function decides whether a source path should be treated as a Markdown page. It keeps hidden files, hidden folders, and non-Markdown files out of the page stream.

**Data flow**: It receives a root-relative path written with forward slashes. It splits the path into parts, rejects it if any part starts with a dot, then checks the file extension in a case-insensitive way. It returns `true` only for visible `.md` or `.markdown` files, and `false` otherwise.

**Call relations**: This is an early gate in the source-reading flow. It uses `PurePosixPath` to read the path suffix reliably, so later steps only receive files that are likely to be Markdown text pages.

*Call graph*: 1 external calls (PurePosixPath).


##### `decoded`  (lines 25–31)

```
def decoded(source_ref: str, data: bytes) -> str
```

**Purpose**: This function turns a file’s raw bytes into readable UTF-8 text. If the bytes are not valid text in that format, it reports a clear source-stream error that includes the file reference.

**Data flow**: It receives a source reference, usually a path or similar identifier, and the raw bytes read from that source. It tries to decode the bytes as UTF-8. On success, it returns a string; on failure, it raises a `StreamFault` saying that this specific source is not UTF-8 text.

**Call relations**: This sits between file reading and Markdown parsing. If decoding fails, it creates a `StreamFault` so the larger sync process can fail with a useful, named record instead of an anonymous low-level decoding error.

*Call graph*: calls 1 internal fn (__init__).


##### `markdown_page`  (lines 34–43)

```
def markdown_page(source_ref: str, text: str) -> Page
```

**Purpose**: This function converts one Markdown text file into a `Page` record that the rest of the system can use. It chooses the page title from frontmatter, then from the first heading, and finally from the source path as a fallback.

**Data flow**: It receives a source reference and the decoded Markdown text. It asks `_split_frontmatter` to separate optional metadata from the real body text. It then creates a `Page` containing the source reference, the cleaned body, the fixed stream name `pages`, and the best available title.

**Call relations**: This is the main assembly step in the file. It calls `_split_frontmatter` first, then calls `_first_heading` only if the frontmatter did not provide a title, and finally hands all of that information into `Page` construction.

*Call graph*: calls 2 internal fn (_first_heading, _split_frontmatter); 1 external calls (__init__).


##### `_split_frontmatter`  (lines 46–60)

```
def _split_frontmatter(text: str) -> tuple[str | None, str]
```

**Purpose**: This helper looks for a YAML frontmatter block at the top of a Markdown file and separates it from the page body. It extracts a `title` value when one is present and valid.

**Data flow**: It receives the full Markdown text. If the text does not start with a frontmatter opening line, it returns no title and the original text. If it finds a closing delimiter, it tries to parse the lines between as YAML, then returns the cleaned title, if any, plus the body after the metadata block. If the YAML is broken or the block is incomplete, it leaves the text unchanged.

**Call relations**: It is called by `markdown_page` before page creation. It uses `yaml.safe_load`, a cautious YAML parser, to read metadata without treating that metadata as page content.

*Call graph*: called by 1 (markdown_page); 1 external calls (safe_load).


##### `_first_heading`  (lines 63–68)

```
def _first_heading(body: str) -> str | None
```

**Purpose**: This helper finds the first top-level Markdown heading and uses it as a possible page title. A top-level heading is a line that starts with `# `.

**Data flow**: It receives the Markdown body after any frontmatter has been removed. It scans the body line by line, trims extra spaces, and returns the text after the first `# ` heading. If it finds no usable heading, it returns nothing.

**Call relations**: It is called by `markdown_page` when frontmatter did not supply a title. This gives ordinary Markdown files a readable title without requiring separate metadata.

*Call graph*: called by 1 (markdown_page).
