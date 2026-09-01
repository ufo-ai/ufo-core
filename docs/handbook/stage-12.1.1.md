# gbrain Markdown, Folder, and Git Page Sources  `stage-12.1.1`

This stage is the intake point for gbrain pages that live outside the system as Markdown files. It is shared support used when the system needs to sync or import pages, rather than part of the user-facing work loop. Its job is to find Markdown files, read them safely, and turn them into standard page records that the rest of gbrain can search and sync.

The folder source treats a local directory like a small library. It walks through the chosen folder and makes one page from each Markdown file it finds. The Git source does the same idea for a GitHub repository, which is a remote code-and-file storage place. It fetches the repository’s Markdown files, but checks whether the repository has changed so it does not download the same content again unnecessarily.

The pages module is the shaping tool. It decides which files count as Markdown pages, confirms they are readable text, and picks friendly titles, so both folder and Git sources produce consistent page records.

## Files in this stage

### Markdown Page Sources
Local folders and GitHub repositories are exposed as gbrain page sources, with shared Markdown-to-page shaping logic.

### `extensions/gbrain/ufo_ext_gbrain/folder.py`

`io_transport` · `source sync`

This backend solves a simple but important problem: people may already have notes or documents sitting in a local directory, and the system needs a safe way to read them as gbrain pages. It scans a configured root folder, finds Markdown files, reads their bytes, and turns each file into a page using the shared page-building helpers.

Safety is a major part of the design. The folder path comes from outside the program, so the code does not trust it blindly. It uses containment checks to make sure reading stays inside the intended root directory. It also refuses to follow symbolic links, which are shortcut files that can point somewhere else on the computer. That prevents a folder from secretly exposing files outside the allowed area.

On each sync, it performs a full scan. Think of it like taking a fresh inventory of a bookshelf each time: files that are present become pages, files that are gone can be removed by the sync driver, and unchanged pages can be skipped elsewhere by comparing their contents. If the whole root cannot be used, the sync fails rather than pretending everything disappeared. It also enforces a maximum total Markdown size, so one folder cannot accidentally flood the system with too much data.

#### Function details

##### `GbrainFolderSource.fetch`  (lines 35–40)

```
async def fetch(self, config: GbrainFolderConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the public sync method for the folder source. It asks the filesystem-reading helper to collect Markdown files, converts each one into a page, and returns a complete snapshot of the folder.

**Data flow**: It receives a folder configuration, an unused cursor, and authentication information. It sends the configured root path and byte limit to the blocking reader in a background thread so the main async loop is not stuck waiting on disk. The raw file references and bytes come back, each file is decoded into text and wrapped as a Markdown page, and the function returns a sync result containing all pages with no next cursor because every run is a full scan.

**Call relations**: During a source sync, the broader source system calls this method to fetch content. This method delegates the slow disk work to GbrainFolderSource._read, then hands each result to the shared page helpers that decode Markdown and build page objects. Finally, it packages those pages into SyncResult so the rest of the sync pipeline can compare, index, or remove pages as needed.

*Call graph*: 4 external calls (__init__, to_thread, decoded, markdown_page).


##### `GbrainFolderSource._read`  (lines 43–57)

```
def _read(root: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: This helper safely walks the configured local folder and reads only Markdown files. It protects the system from reading outside the allowed folder and stops if the total Markdown content is too large.

**Data flow**: It starts with a root path and a maximum byte count. First it turns the root into a checked, contained directory. Then it scans every item under that directory in sorted order, skips symbolic links, non-files, and files that are not Markdown, and reads each allowed file through a guarded file-opening helper. It keeps a running total of bytes read; if the folder exceeds the limit, it raises a stream fault instead of returning partial content. If all is well, it returns a tuple of relative file paths paired with their raw bytes.

**Call relations**: GbrainFolderSource.fetch calls this helper whenever a sync needs the current folder contents. This helper relies on the sandbox helpers to enforce the folder boundary and on the Markdown path checker to decide which files count as pages. If it detects too much content, it raises a fault that tells the sync flow the source cannot be safely streamed.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (contained_file, contained_root, is_markdown_path).


### `extensions/gbrain/ufo_ext_gbrain/git.py`

`io_transport` · `source polling and sync`

This file is a GitHub-backed source for the gbrain extension. Its job is to look at a chosen GitHub repository, find Markdown files, turn them into pages, and report them to the rest of the system as one fresh snapshot. Without it, a workspace could not sync notes or documents directly from a GitHub repo.

The main flow is careful because GitHub has rate limits. First it remembers a small “cursor,” like a bookmark, containing the last commit SHA, an optional ETag, and the last time it checked. A SHA is Git’s unique name for a commit. An ETag is a web server tag that helps ask “has this changed?” cheaply. If there is no stored GitHub token, the file waits longer between checks so anonymous users do not burn through GitHub’s small free request budget.

When a check is due, it asks GitHub for the current commit. If GitHub says “not changed,” it returns no pages. If the repository is empty, it returns an empty snapshot. If the commit changed, it downloads the repository tarball, a compressed archive file, to a temporary file instead of keeping the whole download in memory. It then opens that archive, reads only Markdown files, limits total size for safety, converts them into pages, and deletes the temporary file.

#### Function details

##### `_Cursor.encoded`  (lines 56–59)

```
def encoded(self) -> str
```

**Purpose**: Turns the cursor bookmark into a JSON string so it can be stored and passed back into the next sync run. This preserves what commit was last seen, GitHub’s change tag, and when the last check happened.

**Data flow**: It starts with a cursor object containing a SHA, an optional ETag, and an optional checked-at time. It packs those fields into a small JSON text string with stable key ordering. The output is that string, ready to be saved as the next cursor.

**Call relations**: The fetch flow creates or updates cursor objects after talking to GitHub. When it needs to hand the bookmark back to the source driver, it calls this method so the driver receives plain text rather than a Python object.

*Call graph*: 1 external calls (dumps).


##### `_prior_cursor`  (lines 62–68)

```
def _prior_cursor(cursor: str | None) -> _Cursor | None
```

**Purpose**: Reads the saved cursor from a previous run, if there is one. If the saved value is missing or damaged, it quietly treats it as if there were no prior bookmark.

**Data flow**: It takes either a JSON cursor string or nothing. If there is no string, it returns nothing. If there is a string, it tries to parse it into a cursor object; if parsing fails, it returns nothing instead of stopping the sync.

**Call relations**: GbrainGitSource.fetch calls this at the start of every sync. The result decides whether the run can make a cheap conditional GitHub request, whether anonymous probing should be delayed, and whether a newly found SHA is actually new.

*Call graph*: called by 1 (fetch).


##### `_probe_due`  (lines 71–78)

```
def _probe_due(prior: _Cursor) -> bool
```

**Purpose**: Decides whether an anonymous GitHub check is allowed yet. This protects users without a stored GitHub token from using up GitHub’s small anonymous request allowance too quickly.

**Data flow**: It receives the previous cursor. If there is no recorded check time, or the time cannot be read, it says a probe is due. Otherwise it compares that time with the current time and returns true only after the configured waiting period has passed.

**Call relations**: GbrainGitSource.fetch uses this only when no GitHub token is stored and there is already a prior cursor. If this function says it is too soon, fetch returns immediately with no pages and keeps the same cursor.

*Call graph*: called by 1 (fetch); 2 external calls (fromisoformat, now).


##### `GbrainGitSource.fetch`  (lines 91–127)

```
async def fetch(self, config: GbrainGitConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one complete GitHub sync attempt for a configured repository. It checks whether the repository changed, downloads it only when needed, extracts Markdown files, and returns pages plus the next cursor.

**Data flow**: It receives the repository configuration, the previous cursor text, and source authentication context. It reads whether a GitHub token is stored, may skip the check if anonymous probing is too soon, then builds an HTTP client with the right headers. It asks for the current commit, returns no pages if unchanged, returns an empty snapshot if the repository is empty, or downloads the tarball if the SHA changed. After extracting Markdown entries from the temporary archive, it converts them into page objects and returns a SyncResult containing those pages, the updated cursor, and whether this is a full snapshot.

**Call relations**: This is the central story of the file. It calls _prior_cursor to restore the old bookmark, _probe_due to respect anonymous rate limits, _headers to prepare GitHub requests, _head to learn the current commit, _spool_tarball to download changed content, and the Markdown extraction and page-building helpers before handing a SyncResult back to the source driver.

*Call graph*: calls 5 internal fn (_head, _headers, _spool_tarball, _prior_cursor, _probe_due); 6 external calls (__init__, to_thread, now, AsyncClient, decoded, markdown_page).


##### `GbrainGitSource._headers`  (lines 129–136)

```
async def _headers(self, token_stored: bool) -> dict[str, str]
```

**Purpose**: Builds the HTTP headers used for GitHub API calls. If a GitHub token is stored, it adds it so private repositories can be read and higher rate limits apply.

**Data flow**: It receives a yes-or-no value saying whether the token exists. It always creates headers that ask for GitHub’s JSON API and a specific API version. If a token is available, it reads that credential and adds an Authorization header. The output is the header dictionary used by the HTTP client.

**Call relations**: GbrainGitSource.fetch calls this before creating its GitHub client. The headers it returns affect every later GitHub request made during that sync, including commit checks and tarball downloads.

*Call graph*: called by 1 (fetch).


##### `GbrainGitSource._head`  (lines 138–151)

```
async def _head(self, client: httpx.AsyncClient, config: GbrainGitConfig, prior: _Cursor | None) -> _Cursor | None
```

**Purpose**: Asks GitHub which commit the configured branch currently points to. This is the cheap check that decides whether the full repository archive needs to be downloaded.

**Data flow**: It receives an HTTP client, repository configuration, and optional prior cursor. It chooses the configured branch or HEAD, asks GitHub for the commit SHA, and includes the old ETag when possible so GitHub can answer “not modified.” If GitHub says nothing changed, it returns nothing. If the repository is empty, it returns a cursor with an empty SHA. Otherwise it checks for errors and returns a cursor with the new SHA and ETag.

**Call relations**: GbrainGitSource.fetch calls this after setting up the client. This function uses _refuse_client_error to turn GitHub client-side errors into clear stream faults. Its result tells fetch whether to stop, report an empty snapshot, or continue to tarball download.

*Call graph*: calls 1 internal fn (_refuse_client_error); called by 1 (fetch); 2 external calls (__init__, get).


##### `GbrainGitSource._spool_tarball`  (lines 153–171)

```
async def _spool_tarball(self, client: httpx.AsyncClient, config: GbrainGitConfig, sha: str) -> str
```

**Purpose**: Downloads the repository archive for a specific commit into a temporary file. It streams the data in chunks so the program does not hold the whole archive in memory.

**Data flow**: It receives an HTTP client, repository configuration, and commit SHA. It creates a temporary .tar.gz file, streams bytes from GitHub into that file, and counts how much has arrived. If the download grows beyond the allowed size, it raises a StreamFault and removes the partial file. On success, it closes the file and returns the temporary path.

**Call relations**: GbrainGitSource.fetch calls this only after _head shows the repository has changed. It calls _refuse_client_error on the tarball response before trusting it. Fetch later passes the returned file path to the Markdown extraction step and then deletes the temporary file.

*Call graph*: calls 2 internal fn (__init__, _refuse_client_error); called by 1 (fetch); 2 external calls (to_thread, stream).


##### `GbrainGitSource._markdown_entries`  (lines 174–192)

```
def _markdown_entries(repo: str, spool: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: Opens the downloaded repository archive and pulls out only Markdown files. It returns each Markdown file as a repository-relative path plus its raw bytes.

**Data flow**: It receives the repository name, the temporary archive path, and a maximum byte limit. It opens the compressed tar archive, walks through its files, strips the archive’s top folder prefix, ignores non-files and non-Markdown paths, reads matching file contents, and keeps a running decompressed-size total. If the Markdown content is too large, it raises a StreamFault. Otherwise it returns the entries sorted by path.

**Call relations**: After _spool_tarball finishes, GbrainGitSource.fetch runs this work in a background thread because archive reading is blocking file work. The extracted entries are then decoded and turned into page objects for the final SyncResult.

*Call graph*: calls 1 internal fn (__init__); 2 external calls (open, is_markdown_path).


##### `_refuse_client_error`  (lines 195–198)

```
def _refuse_client_error(response: httpx.Response, what: str) -> None
```

**Purpose**: Turns bad HTTP responses from GitHub into clear sync failures. It gives a friendlier error for client-side problems such as missing repositories or forbidden access.

**Data flow**: It receives an HTTP response and a short description of what was being requested. If the response is a client error, it raises a StreamFault that includes GitHub’s status code and the request description. If not, it asks the HTTP library to raise for other unsuccessful statuses; otherwise it returns without changing anything.

**Call relations**: Both _head and _spool_tarball call this right after receiving a GitHub response. It acts like a gatekeeper: only acceptable responses move forward to SHA reading or archive streaming, while error responses stop the sync with a meaningful fault.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_head, _spool_tarball); 1 external calls (raise_for_status).


### `extensions/gbrain/ufo_ext_gbrain/pages.py`

`domain_logic` · `source sync and page creation`

This file is a small but important gatekeeper for importing Markdown notes or documents. Without it, the system might try to index hidden files, binary files, or unreadable text, and pages could end up with confusing titles.

It works like a careful librarian. First, it checks a file path and only accepts normal Markdown files, while skipping dotfiles and hidden folders such as `.git`. Then it decodes the file bytes as UTF-8, which is the common text encoding used by Markdown. If the bytes are not valid UTF-8, it raises a clear stream error that names the problem file, so the sync fails with a useful message instead of a vague decoding crash.

Once the text is available, it builds a `Page`, which is the system’s record for indexed content. It looks for a title in YAML frontmatter, which is a metadata block at the top of many Markdown files between `---` lines. If there is no usable frontmatter title, it looks for the first top-level Markdown heading, like `# Project Notes`. If that is missing too, it falls back to the file path. Importantly, valid frontmatter is removed from the page body so metadata is not indexed as ordinary content.

#### Function details

##### `is_markdown_path`  (lines 16–22)

```
def is_markdown_path(relpath: str) -> bool
```

**Purpose**: This function decides whether a root-relative path should be treated as a Markdown document. It keeps hidden files, hidden folders, and non-Markdown files out of the page import process.

**Data flow**: It receives a path string such as `docs/intro.md`. It splits the path into folder and file parts, rejects it if any part starts with a dot, then checks the file extension in a case-insensitive way. It returns `true` only for accepted Markdown paths and `false` otherwise.

**Call relations**: This is the first filter other sync code can use before reading a file. It relies on `PurePosixPath` to inspect the file suffix cleanly, so later steps only see files that are likely to be Markdown text.

*Call graph*: 1 external calls (PurePosixPath).


##### `decoded`  (lines 25–31)

```
def decoded(source_ref: str, data: bytes) -> str
```

**Purpose**: This function turns raw file bytes into normal text, but only if the file is valid UTF-8. It gives a clear, file-specific error when a file cannot be read as text.

**Data flow**: It receives the file’s source reference, usually its path, and the raw bytes read from storage. It tries to decode those bytes as UTF-8. On success, it returns a text string; on failure, it raises a `StreamFault`, which is a controlled error for a bad item in a source stream.

**Call relations**: This sits between file reading and Markdown parsing. When decoding fails, it creates a `StreamFault` with the source reference so the larger sync run can report exactly which file caused the problem.

*Call graph*: calls 1 internal fn (__init__).


##### `markdown_page`  (lines 34–43)

```
def markdown_page(source_ref: str, text: str) -> Page
```

**Purpose**: This function converts one Markdown text file into a `Page` record that the rest of the system can index or display. It also chooses the best available title for that page.

**Data flow**: It receives a source reference and the decoded Markdown text. It asks `_split_frontmatter` to separate any metadata block from the real body, then chooses a title from frontmatter, the first top-level heading, or finally the source reference. It returns a new `Page` containing the source reference, cleaned body text, stream name, and title.

**Call relations**: This is the main assembly point in the file. It calls `_split_frontmatter` first because metadata should not become body text, then calls `_first_heading` only if no frontmatter title was found, and finally hands the finished information to `Page.__init__`.

*Call graph*: calls 2 internal fn (_first_heading, _split_frontmatter); 1 external calls (__init__).


##### `_split_frontmatter`  (lines 46–60)

```
def _split_frontmatter(text: str) -> tuple[str | None, str]
```

**Purpose**: This helper looks for a YAML frontmatter block at the very start of a Markdown file and extracts a usable `title` from it. It also removes that metadata block from the page body when the block is valid.

**Data flow**: It receives the full Markdown text. If the text does not start with a `---` frontmatter opener, it returns no title and the original text. If it finds a closing `---` or `...`, it tries to parse the lines between as YAML; when parsing succeeds, it returns the cleaned title if one exists plus the remaining body text. If parsing fails or no closing marker is found, it leaves the text unchanged.

**Call relations**: This is called by `markdown_page` before the page is created. It uses `yaml.safe_load`, a safer YAML parser, to read metadata without treating it as executable instructions, and it gives `markdown_page` both the possible title and the body that should be indexed.

*Call graph*: called by 1 (markdown_page); 1 external calls (safe_load).


##### `_first_heading`  (lines 63–68)

```
def _first_heading(body: str) -> str | None
```

**Purpose**: This helper finds the first top-level Markdown heading in the page body and uses it as a fallback title. A top-level heading is a line that starts with `# `.

**Data flow**: It receives Markdown body text after any frontmatter has been removed. It scans the text line by line, trims extra spaces, and returns the text after the first `# ` heading. If there is no such heading, or the heading is empty, it returns nothing.

**Call relations**: This is called by `markdown_page` only when frontmatter did not provide a title. It gives the page creation step a readable title before the final fallback to the file path.

*Call graph*: called by 1 (markdown_page).
