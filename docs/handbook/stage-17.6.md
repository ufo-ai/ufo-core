# Skill discovery, package markers, and sample probes  `stage-17.6`

This stage is shared support around “skills,” which are packaged add-ons the system can discover, show, or test. It is not the main document conversion work. Instead, it provides small pieces that help the larger system recognize packages, browse available skills, and confirm that a sample skill can run.

The documents package marker, `__init__.py`, is like a label on a folder. It tells Python, the programming language, that `ufo_ext_documents` is an importable package. It adds no actions of its own, but without the label other code may not be able to find the package cleanly.

The sample skill probe is a simple test button. When run, it prints a fixed success message, letting the system check that the sample skill is installed and reachable.

The web community reader connects the web app to the public `skills.sh` directory. It can list skills, search them, and fetch a skill’s full `SKILL.md` description for the Community skills page.

## Files in this stage

### Packaged Skill Support
Package markers, sample probes, and community directory readers provide ancillary support for discovering and validating skills.

### `extensions/documents/ufo_ext_documents/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That matters because other parts of the project can then refer to code inside `extensions/documents/ufo_ext_documents` using normal Python import paths. Think of it like putting a label on a folder in a filing cabinet: the label does not contain documents itself, but it lets people find and refer to the folder reliably. Because this file is empty, it does not run setup code, expose helper functions, or create shared objects when the package is imported. Its value is structural: without it, some Python tooling or older import behavior might not recognize this directory as a package.


### `extensions/sample/skills/sample_skill/probe.py`

`test` · `startup or extension health check`

This file is like a simple doorbell test for the sample skill. It does not load data, make decisions, or talk to other services. It only prints the text `sample-skill-probe-ok`.

That may seem too small to matter, but it is useful in systems that load optional extensions or skills. Before trusting a skill, the system may run a small probe script to check that the file exists, Python can execute it, and the surrounding extension is wired up correctly. If this file cannot run or does not print the expected message, that is a clear sign that the sample skill is missing, broken, or not installed correctly.

There are no functions or classes here. The print happens immediately when the file is executed. In everyday terms, it is a smoke test: not a full inspection, just a quick sign that the basic path is working.


### `extensions/web/ufo_ext_web/community.py`

`io_transport` · `request handling`

The Community tab depends on information that lives outside this project, on skills.sh. This file is the small bridge to that outside directory. Without it, the web app could not show popular community skills, search for them, or load the description and instructions shown during install.

The file has two main public reads. `CommunitySkills.listing` returns a list of skill names, source repositories, and install counts. If the user has not typed a search, it reads the public leaderboard page. If the user has typed a search, it calls the skills.sh search API. `CommunitySkills.fetch` downloads one skill’s document and extracts its name, description, and instructions from the `SKILL.md` file.

It is careful not to overuse the remote service. Listings are cached for 15 minutes, and fetched documents are remembered for the rest of the process. Think of this like keeping a menu at the table instead of asking the restaurant host for a new copy every minute. It also puts size limits on downloads, checks for rate-limit errors, and turns bad remote answers into a clear `CommunityUnavailable` message that the user can see instead of a silent empty result.

#### Function details

##### `_refusal`  (lines 43–49)

```
def _refusal(code: int) -> CommunityUnavailable
```

**Purpose**: This helper turns an HTTP failure code from skills.sh into a user-readable error. It gives a special, clearer message when the directory says the app has hit its read limit.

**Data flow**: It receives a numeric status code from a failed web response. It checks whether the code means “too many requests”; if so, it creates a `CommunityUnavailable` error explaining the hourly limit. For any other code, it creates an error saying what code the directory returned.

**Call relations**: When `_search` or `_body` sees that skills.sh did not return a normal success response, they call `_refusal`. `_refusal` hands back the exception they should raise, so the rest of the app gets one consistent kind of failure message.

*Call graph*: called by 2 (_body, _search); 1 external calls (__init__).


##### `CommunitySkills.listing`  (lines 79–89)

```
async def listing(self, query: str) -> list[CommunitySkill]
```

**Purpose**: This is the main way the web app asks for community skill rows. It returns popular skills when there is no search text, or matching skills when the user has searched.

**Data flow**: It receives a search query string. First it looks in the in-memory listing cache; if the same query was read recently, it returns that saved list. Otherwise it opens an HTTP client, asks either `_popular` or `_search` for fresh results, trims the list to the display limit, stores it in the cache with the current time, and returns the list.

**Call relations**: This is a public doorway into the file’s listing behavior. It creates a client through `_client`, then chooses `_popular` for the default leaderboard or `_search` for a typed query. The helper methods do the remote reading and cleanup; `listing` decides when to reuse cached data and how much to return.

*Call graph*: calls 3 internal fn (_client, _popular, _search); 1 external calls (monotonic).


##### `CommunitySkills.fetch`  (lines 91–116)

```
async def fetch(self, source: str, name: str) -> CommunityDocument | None
```

**Purpose**: This is the main way the web app loads the full document for one selected community skill. It is used when the interface needs the description and install instructions, not just a row in a list.

**Data flow**: It receives a source repository such as `owner/repo` and a skill name. It first checks whether that exact document was already fetched. If not, it builds the download URL, reads the response body, parses the JSON response, finds the `SKILL.md` file inside it, and passes that markdown text to `_parse`. It saves either the parsed document or `None` in the cache, then returns it.

**Call relations**: This is the public doorway for one-skill details. It relies on `_client` to create the web client, `_body` to safely download the remote response, and `_parse` to turn the SKILL.md text into a structured `CommunityDocument`. If the remote JSON cannot be read, it raises `CommunityUnavailable` so the web route can show a clear failure.

*Call graph*: calls 3 internal fn (_body, _client, _parse); 2 external calls (__init__, loads).


##### `CommunitySkills._client`  (lines 118–119)

```
def _client(self, timeout: float) -> httpx.AsyncClient
```

**Purpose**: This helper creates the HTTP client used to talk to skills.sh. It centralizes timeout, redirect, and test-transport settings so the rest of the file does not repeat them.

**Data flow**: It receives a timeout value in seconds. It builds an asynchronous HTTP client with that timeout, the optional injected transport used by tests, and redirect-following turned on. The client is returned to the caller, which uses it inside a short-lived context.

**Call relations**: `listing` and `fetch` both call `_client` before making remote reads. This keeps the public methods focused on what they are trying to retrieve, while `_client` supplies the network tool they need.

*Call graph*: called by 2 (fetch, listing); 1 external calls (AsyncClient).


##### `CommunitySkills._popular`  (lines 121–136)

```
async def _popular(self, client: httpx.AsyncClient) -> list[CommunitySkill]
```

**Purpose**: This helper reads the skills.sh leaderboard and turns it into a sorted list of community skill rows. It is used for the default Community view when the user has not searched.

**Data flow**: It receives an HTTP client. It downloads the leaderboard page through `_body`, scans the page text for embedded JSON-looking skill entries, tries to decode each one, and passes each decoded entry to `_entry`. It removes duplicates by source and name, raises a clear error if no usable listing is found, then returns the skills sorted by install count from highest to lowest.

**Call relations**: `listing` calls `_popular` when the query is empty. `_popular` delegates safe downloading to `_body` and entry validation to `_entry`, then hands the cleaned and ranked list back to `listing` for limiting and caching.

*Call graph*: calls 2 internal fn (_body, _entry); called by 1 (listing); 2 external calls (__init__, loads).


##### `CommunitySkills._search`  (lines 138–147)

```
async def _search(self, client: httpx.AsyncClient, query: str) -> list[CommunitySkill]
```

**Purpose**: This helper calls the public skills.sh search API for a user’s search text. It converts the API response into the same simple skill rows used by the leaderboard.

**Data flow**: It receives an HTTP client and the query text. It sends a GET request with the query and result limit. If the response is not successful, it turns the status code into a `CommunityUnavailable` error through `_refusal`. Otherwise it reads the returned JSON, converts each listed skill through `_entry`, discards unusable entries, sorts the rest by install count, and returns them.

**Call relations**: `listing` calls `_search` when the user has entered a query. `_search` talks directly to the search endpoint, uses `_refusal` for remote failures, and uses `_entry` so search results are cleaned in the same way as leaderboard results.

*Call graph*: calls 2 internal fn (_entry, _refusal); called by 1 (listing); 1 external calls (get).


##### `CommunitySkills._entry`  (lines 149–156)

```
def _entry(self, entry: object) -> CommunitySkill | None
```

**Purpose**: This helper turns one raw skill record from skills.sh into a trusted `CommunitySkill` row. It filters out records that are missing a name or have an invalid source repository.

**Data flow**: It receives an unknown object, usually decoded from JSON. If the object is not a dictionary, it returns `None`. Otherwise it reads the skill name, source, and install count, checks that the source looks like `owner/repo`, and returns a `CommunitySkill`. If the required fields are not safe or present, it returns `None`.

**Call relations**: Both `_popular` and `_search` feed raw remote entries into `_entry`. This gives the two listing paths the same gatekeeper, so only well-shaped skill rows reach the public `listing` result.

*Call graph*: called by 2 (_popular, _search); 1 external calls (__init__).


##### `CommunitySkills._body`  (lines 158–178)

```
async def _body(self, client: httpx.AsyncClient, url: str, cap: int, headers: dict[str, str] | None=None) -> bytes
```

**Purpose**: This helper safely downloads bytes from a skills.sh URL. It protects the app from bad status codes and from unexpectedly huge responses.

**Data flow**: It receives an HTTP client, a URL, a maximum byte size, and optional request headers. It streams the response in chunks. If the status code is not successful, it raises the standardized error from `_refusal`. As chunks arrive, it counts their total size; if the response grows past the allowed cap, it raises `CommunityUnavailable`. If all is well, it joins the chunks and returns the full byte body.

**Call relations**: `_popular` uses `_body` to read the leaderboard page, and `fetch` uses it to read a skill download response. `_body` is the shared safety layer for remote downloads, handing failures to `_refusal` and handing valid bytes back to the higher-level parsers.

*Call graph*: calls 1 internal fn (_refusal); called by 2 (_popular, fetch); 2 external calls (__init__, stream).


##### `CommunitySkills._parse`  (lines 180–199)

```
def _parse(self, document: str) -> CommunityDocument | None
```

**Purpose**: This helper reads a downloaded `SKILL.md` file and extracts the parts the web app needs: name, description, instructions, and the original document.

**Data flow**: It receives the markdown document as text. It first checks for YAML front matter, which is a metadata block between `---` lines at the top of the file. It safely parses that metadata, verifies it contains a name and description, trims the remaining markdown body as instructions, and returns a `CommunityDocument`. If the document is not in the expected shape, it returns `None`.

**Call relations**: `fetch` calls `_parse` after it finds `SKILL.md` in the downloaded files. `_parse` does not contact the network; it simply turns raw document text into the structured object that `fetch` caches and returns.

*Call graph*: called by 1 (fetch); 2 external calls (__init__, safe_load).
