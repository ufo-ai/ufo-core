# Sandbox lifecycle, workspace storage, and egress control  `stage-12`

This stage is the safety shell around work that can affect the outside world. It supports the main work loop whenever the agent runs code, edits files, opens a browser, or reaches the internet. The sandbox session layer defines the boundary: tools see only the conversation’s workspace, while the actual carrier can be local execution, Docker, or E2B cloud sandboxes. The template builder keeps the Docker and E2B environments made from the same recipe.

Workspace storage is the shared filing cabinet. Blob storage hides whether bytes are local or in S3. Artifacts are the files intentionally shared back for later use. Short-lived storage credentials and mount-command generation let a sandbox attach its /workspace folder to only its own storage area.

The local, Docker, and E2B carriers start, resume, talk to, copy files into or out of, and clean up sandboxes. Browser sessions and coding, website, and REPL helpers are the tools used inside that boundary.

Network access passes through the proxy. Its rules decide what traffic is allowed, when secrets may be injected, and when broker forwarding is needed. The proxy server enforces those choices and records audit and billing data.

## Sub-stages

- [Browser and computer-use sessions](stage-12.1.md) `stage-12.1` — 25 files
- [Coding, website, and REPL execution helpers](stage-12.2.md) `stage-12.2` — 3 files

## Files in this stage

### Sandbox foundations
Defines the sandbox package boundary, the shared session abstraction, and the image build recipe used by sandbox backends.

### `core/src/ufo/sandbox/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside this directory using names like `ufo.sandbox.something`. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label simply tells Python that the drawer exists and can be opened by name. Because the file is empty, it does not run setup code, expose shortcuts, or change how the sandbox works. Its importance is structural: without it, some Python tooling or older import behavior might not recognize this directory as a package.


### `core/src/ufo/sandbox/session.py`

`domain_logic` · `startup and per-turn sandbox use`

A sandbox is a disposable work area where tools can run commands and read or write files without touching the rest of the system. This file is the shared contract for that work area. Think of it like a hotel room key: tools get access to one room, not the whole building.

The file defines small value objects that describe a sandbox: what workspace is mounted, where the network proxy is, which container image to use, and how to remember an already-created sandbox. It also defines `Carrier`, a protocol, meaning a promise that any sandbox backend must offer the same basic actions: create a sandbox, run a command, write a file, export a file, destroy the sandbox, and expose a port.

The important safety rule is enforced by `workspace_path`. Any path supplied by a tool is resolved under `/workspace`, and attempts to climb out with `..` are rejected. This keeps tools away from transcripts and other private records outside the workspace.

`SandboxSession` is the per-turn object that tools actually use. It wraps the carrier and adds safe conveniences: run bash, write a file, check for a file, call the in-sandbox file helper, export a file to blob storage, or find a reachable host for a service started inside the sandbox. It also signs and verifies run tokens so the egress proxy can tell which workspace and turn a sandbox network request belongs to.

#### Function details

##### `RunTokenCodec.from_env`  (lines 45–49)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Builds a token signer from the deployment's secret environment variable. This is used when the server or proxy needs to mint or verify sandbox run tokens, and it refuses to continue if the secret is missing.

**Data flow**: It reads the token secret from the process environment → checks that a value exists → converts that text secret into bytes → returns a `RunTokenCodec` ready to sign and verify run tokens. If the secret is absent, it raises an error instead of silently creating insecure tokens.

**Call relations**: The serve process and proxy setup call this during startup so both sides use the same secret. Later, that codec is used to prove that proxy requests came from tokens minted by this deployment.

*Call graph*: called by 2 (serve, run).


##### `RunTokenCodec.encode`  (lines 51–53)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns a workspace-and-turn pair into a signed token string. The token can be passed into the sandbox so network egress can later be attributed to the right conversation turn.

**Data flow**: It takes a `RunToken` containing a workspace ID and turn ID → formats them into a clear payload beginning with `ufo-run` → signs that payload with the deployment secret → returns the signed string.

**Call relations**: When the queue opens a sandbox for a turn, it calls this to create the run token that will be placed into the sandbox's proxy settings. The actual signing is handed off to the shared token-signing helper.

*Call graph*: called by 1 (_open_sandbox); 1 external calls (sign_token).


##### `RunTokenCodec.from_proxy_auth`  (lines 55–66)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Reads a proxy `Authorization` header and recovers the workspace and turn only if the header contains a valid signed UFO run token. This lets the proxy reject forged or malformed sandbox requests.

**Data flow**: It takes an authorization header → checks that it uses Basic authentication → decodes the base64 username → verifies the signed token with the deployment secret → checks that the token is for the `ufo-run` domain → parses the workspace and turn IDs → returns a `RunToken`. Bad encoding, bad signatures, wrong token kind, or invalid IDs become a `ValueError`.

**Call relations**: This is used on the proxy side when a sandbox request arrives. It relies on base64 decoding, token verification, and UUID parsing to turn the incoming header back into trusted run identity.

*Call graph*: 4 external calls (__init__, b64decode, verify_token, UUID).


##### `format_sandbox_handle`  (lines 146–150)

```
def format_sandbox_handle(backend: str, container_id: str) -> str
```

**Purpose**: Creates the durable text handle stored for a sandbox by combining the backend name and container ID. The backend prefix prevents one sandbox backend from accidentally resuming or deleting another backend's container.

**Data flow**: It takes a backend name and a container ID → joins them with a colon → returns a string like `<backend>:<id>` that can be stored on a conversation record.

**Call relations**: Carriers use this format when they need to persist a sandbox reference across process restarts. Later code can inspect the prefix before trying to resume or reap the sandbox.


##### `sandbox_handle_id`  (lines 153–157)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Extracts the container ID from a stored sandbox handle, but only if the handle belongs to the expected backend. This protects deployments that change sandbox providers from touching old provider-specific handles.

**Data flow**: It takes the current backend name and a stored handle string → checks whether the handle starts with that backend prefix → returns the ID part if it matches, or `None` if it belongs to some other backend.

**Call relations**: A carrier can call this before resuming or cleaning up a sandbox. If the prefix does not match, the carrier knows the handle is not its responsibility.


##### `Carrier.create`  (lines 172–172)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the required operation for creating or attaching to a sandbox. Each backend implements this in its own way while presenting the same shape to the rest of the system.

**Data flow**: It receives a `SandboxSpec` describing the conversation, image, workspace mount, proxy settings, run token, and environment → the backend creates or resumes the sandbox → it returns a `SandboxHandle` that represents that running sandbox.

**Call relations**: The queue calls this when opening a sandbox for a conversation turn. Concrete carriers such as Docker or remote providers supply the actual behavior behind this protocol method.

*Call graph*: called by 1 (_open_sandbox).


##### `Carrier.exec`  (lines 174–176)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines the required operation for running a command inside the sandbox. Tools use this indirectly so they do not need to know which sandbox backend is in use.

**Data flow**: It receives a sandbox handle, a command as an argument tuple, and a timeout → the backend runs that command inside the sandbox → it returns an `ExecResult` containing standard output, standard error, and the exit code.

**Call relations**: `SandboxSession` calls this for bash commands, file checks, tool-output setup, and `sbxfs` operations. Each carrier decides how to execute the command safely in its environment.


##### `Carrier.write`  (lines 178–184)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines the required operation for copying bytes into a workspace file. This avoids passing large or arbitrary file contents through command-line arguments, which can break provider limits and create quoting problems.

**Data flow**: It receives a sandbox handle, an absolute workspace path, and bytes to write → the backend creates parent directories if needed and writes the content → it returns nothing, but the file appears inside the sandbox workspace.

**Call relations**: `SandboxSession.write_file` calls this after first checking that the path stays inside `/workspace`. Concrete carriers implement the copy-in using their own filesystem APIs or streams.


##### `Carrier.export`  (lines 186–191)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Defines the required operation for streaming a workspace file out to blob storage. It is designed for large files, so the host process does not need to load the whole file into memory.

**Data flow**: It receives a sandbox handle, an absolute workspace path, a blob store, and a destination key → the backend streams the file from the sandbox or mount into the blob store → it returns nothing after the upload is complete.

**Call relations**: `SandboxSession.export_file` calls this after applying the workspace path guard. Each carrier chooses the most efficient copy-out path for its backend.


##### `Carrier.destroy`  (lines 193–193)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Defines the required operation for reclaiming a sandbox. Destroying the container should remove temporary compute resources without deleting the durable workspace data.

**Data flow**: It receives a sandbox handle → the backend tears down the corresponding container or remote sandbox → it returns nothing once cleanup has been requested or completed.

**Call relations**: Lifecycle code can call this when a sandbox is no longer needed. The concrete backend decides what destruction means for its own infrastructure.


##### `Carrier.host`  (lines 195–201)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Defines the required operation for finding an address outside the sandbox that can reach a service running on a sandbox port. This is needed for things like browser debugging ports or development server previews.

**Data flow**: It receives a sandbox handle and an internal port number → the backend maps that port to an externally reachable host or host-and-port string → it returns that address, or the backend may raise an error if it cannot expose ports.

**Call relations**: `SandboxSession.host` calls this when another part of the system needs to connect to an in-sandbox service. Remote carriers may return public per-port hosts; local carriers may use local routing or may not support it.


##### `workspace_path`  (lines 204–212)

```
def workspace_path(path: str) -> str
```

**Purpose**: Turns a tool-supplied path into a safe absolute path under `/workspace`. It blocks attempts to escape the workspace, which is one of the main safety guarantees of the sandbox session.

**Data flow**: It receives a path that may be relative or absolute → treats relative paths as being under `/workspace` → resolves `.` and `..` pieces → checks that the result is still `/workspace` or inside it → returns the safe absolute path. If the path climbs outside the workspace, it raises `ValueError`.

**Call relations**: File-writing, file-checking, file-export, and `sbxfs` operations call this before touching a path. It delegates the path-part cleanup to `_resolve_parts`, then uses `PurePosixPath` to reason about the result.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 4 (export_file, file_exists, run_sbxfs, write_file); 1 external calls (PurePosixPath).


##### `_resolve_parts`  (lines 215–224)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Simplifies path pieces by removing current-directory markers and applying parent-directory markers. It is the small helper that makes `workspace_path` able to detect path escapes.

**Data flow**: It receives the individual pieces of a path → walks through them like a stack → ignores empty and `.` pieces → removes the previous piece for `..` → raises an error if `..` would climb above the workspace root → returns the cleaned list of path pieces.

**Call relations**: `workspace_path` is the caller. This helper does the low-level path cleanup so the outer function can focus on enforcing the `/workspace` boundary.

*Call graph*: called by 1 (workspace_path).


##### `SandboxSession.bash`  (lines 236–241)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a shell command inside the sandbox for the current turn. It gives tools a simple way to ask the sandbox to do command-line work without knowing anything about the backend.

**Data flow**: It receives a command string and optionally a timeout → wraps the command as `bash -lc <command>` → uses the default timeout if none is given → sends it to the carrier's `exec` method → returns the command's output, error text, and exit code.

**Call relations**: The sandbox Chrome extension calls this when it needs to run setup or helper commands. The actual execution is handed to the carrier, so Docker, E2B, or another backend can implement it differently.

*Call graph*: called by 1 (_run).


##### `SandboxSession.write_file`  (lines 243–244)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file inside the sandbox workspace. It protects the workspace boundary before asking the backend to copy the file in.

**Data flow**: It receives a path and byte content → converts the path into a safe `/workspace` path using `workspace_path` → passes the handle, safe path, and content to the carrier's `write` method → returns nothing after the write is complete.

**Call relations**: The skill runtime uses this when mounting skill files into the sandbox. This method sits between callers and the carrier so every write gets the same path safety check.

*Call graph*: calls 1 internal fn (workspace_path); called by 1 (mount_skill).


##### `SandboxSession.ensure_tool_output_dir`  (lines 246–268)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure the engine's private `.tool-output` directory exists inside the workspace. If a file or broken link is squatting on that name, it removes that squatter and creates the directory.

**Data flow**: It runs a small shell script against the fixed `/workspace/.tool-output` path → if the path is already a directory, it does nothing → if a non-directory exists there, it removes it, prints a marker, and creates the directory → if the command fails, it raises `OSError` → otherwise it returns `true` if something was reclaimed and `false` if no cleanup was needed.

**Call relations**: This is used before the engine offloads tool output into its private workspace area. It calls the carrier's `exec` directly because the check and possible repair must happen inside the sandbox filesystem.


##### `SandboxSession.file_exists`  (lines 270–275)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists inside the workspace. It is a safe existence test because the requested path is first forced under `/workspace`.

**Data flow**: It receives a path → turns it into a safe workspace path → runs `test -f` inside the sandbox → returns `true` if the command exits successfully and `false` otherwise.

**Call relations**: Callers use this through the session when they need to know whether a workspace file is present. The method relies on `workspace_path` for safety and the carrier's `exec` for the in-sandbox check.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.run_sbxfs`  (lines 277–304)

```
async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one structured file operation through the `sbxfs` command inside the sandbox and returns its JSON result. This keeps heavy file work inside the sandbox instead of pulling entire files into the host process.

**Data flow**: It receives an operation name and a dictionary of arguments → if the arguments include a string `path`, it rewrites that path safely under `/workspace` → serializes the arguments as compact JSON → executes `sbxfs <op> <json>` inside the sandbox → trims and parses stdout as JSON → returns the parsed object. Missing output, invalid JSON, or a non-object result becomes a runtime error; a returned JSON `error` string becomes a `ValueError` for a recoverable tool-level problem.

**Call relations**: Tools use this session method for bounded file operations such as reading windows of text or rendering file previews. It combines the workspace path guard, JSON encoding and decoding, and carrier execution into one safe flow.

*Call graph*: calls 1 internal fn (workspace_path); 2 external calls (dumps, loads).


##### `SandboxSession.export_file`  (lines 306–307)

```
async def export_file(self, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Streams a workspace file out of the sandbox into blob storage. It is meant for attachments or large outputs that should not be read fully into memory by the host process.

**Data flow**: It receives a path, a blob store, and a destination key → checks and converts the path to a safe `/workspace` path → asks the carrier to export that file to the blob store under the key → returns nothing when the export finishes.

**Call relations**: Callers use this through the session whenever a sandbox-produced file must become a stored blob. The carrier provides the backend-specific streaming mechanism.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.host`  (lines 309–313)

```
async def host(self, port: int) -> str
```

**Purpose**: Gets an address that outside code can use to reach a service listening on a port inside the sandbox. This supports workflows like connecting to a browser debugging endpoint started in the sandbox.

**Data flow**: It receives an internal sandbox port → asks the carrier to translate that port into an externally reachable host string → returns that host string.

**Call relations**: The sandbox Chrome CDP provider calls this when leasing a browser connection. The session simply forwards the request to the carrier because each backend has its own networking layout.

*Call graph*: called by 1 (lease).


##### `SandboxSession.traffic_token`  (lines 316–319)

```
def traffic_token(self) -> str | None
```

**Purpose**: Returns the carrier's extra traffic token for sandboxes whose public ports require one. If the backend does not use such a token, it returns `None`.

**Data flow**: It reads the `traffic_token` stored on the session's sandbox handle → returns that string or `None` without changing anything.

**Call relations**: Code that dials a host returned by `SandboxSession.host` can also read this property and attach the token as a connection header when the carrier requires it, such as for some remote sandbox providers.


### `sandbox/build_template.py`

`entrypoint` · `build and deploy time`

This script is the build recipe and command-line tool for UFO’s sandbox environment. A sandbox is the isolated machine where the project can run tools safely, like a workshop stocked with the same tools every time. Without this file, the E2B cloud sandbox and the Docker-based sandbox could end up with different packages, scripts, users, or startup behavior, which would make features work in one place and fail in another.

The file defines one shared set of layers: system packages, Python packages, Node packages, browser support, GitHub CLI support, filesystem tools, UFO helper scripts, environment variables, and the command that keeps the sandbox running. It then applies those layers to two different bases. For E2B, it starts from an E2B template. For Docker, it starts from the public Docker image that corresponds to that template.

A key safety feature is the build digest: a compact fingerprint of the whole build definition, including script contents. The digest is baked into the image. Later, the script can boot the published template and compare the live digest to the current source recipe. This catches stale templates before they are trusted. The script can also verify that a newly published template really contains the expected tools before reporting success.

#### Function details

##### `build_definition_digest`  (lines 183–209)

```
def build_definition_digest() -> str
```

**Purpose**: Creates a fingerprint of everything important that goes into the sandbox image. This lets the project tell whether a published sandbox was built from the current recipe or from an older one.

**Data flow**: It reads the build constants in this file, the shared sandbox environment, and the contents of each bundled sandbox script. It turns that information into a stable JSON payload, hashes it with SHA-256, and returns a string like a label on a sealed box: if any meaningful ingredient changes, the label changes too.

**Call relations**: The image-building path calls this while adding layers so the digest can be written into the sandbox itself. The checking path calls it again later to compare today’s source recipe with the digest found inside the live published template.

*Call graph*: called by 2 (apply_layers, check_published_template); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 212–239)

```
def apply_layers(builder: object) -> object
```

**Purpose**: Adds the shared UFO sandbox setup to a template builder. This is the central recipe that makes the E2B and Docker sandboxes contain the same tools and behavior.

**Data flow**: It receives a builder object that already knows which base image or template it starts from. It switches to the build user, installs operating-system tools, builds the pinned s3fs filesystem tool, installs GitHub CLI, Python packages, Node packages, and Playwright’s browser, writes the build digest, copies UFO helper scripts into the command path, sets environment variables, switches to the runtime user, and returns the finished builder with its start and readiness commands set.

**Call relations**: Both template-building routes rely on this function: one route prepares the E2B template, and the other prepares the Dockerfile. It calls the digest function so every built image carries proof of which recipe created it.

*Call graph*: calls 1 internal fn (build_definition_digest); called by 2 (e2b_template, pod_dockerfile).


##### `e2b_template`  (lines 242–244)

```
def e2b_template() -> object
```

**Purpose**: Creates the E2B version of the sandbox definition. E2B is the cloud sandbox service used here to run isolated code environments.

**Data flow**: It starts with the project root as the file context, chooses the E2B base template, then passes that builder through the shared layer recipe. The result is a complete E2B template definition ready to build and publish.

**Call relations**: The main command uses this when no special flag is given, meaning the user wants to build and publish the E2B template. It delegates the actual contents of the sandbox to the shared layer function so it stays aligned with Docker.

*Call graph*: calls 1 internal fn (apply_layers); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 247–249)

```
def pod_dockerfile() -> str
```

**Purpose**: Creates the Dockerfile text for the Docker version of the same sandbox. This lets local or Docker-only deployments use the same toolset as the E2B sandbox.

**Data flow**: It starts from the configured public Docker base image, applies the same sandbox layers, and asks the E2B SDK to render that builder as a Dockerfile string. The returned text can be printed or passed directly to Docker.

**Call relations**: The main command calls this when the user asks to print the Dockerfile. The Docker build helper also calls it so it can feed the generated Dockerfile into the local Docker build command.

*Call graph*: calls 1 internal fn (apply_layers); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 252–263)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker image used by the Docker carrier. It is useful when someone wants a Docker sandbox without needing an E2B account or key.

**Data flow**: It asks for the generated Dockerfile, sends that text to `docker build` through standard input, uses the repository root as the build context, and tags the result with the configured sandbox image name. If Docker fails, it stops the script with an error; if it succeeds, it prints the tag.

**Call relations**: The main command calls this when the user passes the Docker build flag. This function bridges the shared build recipe to the local Docker daemon, which is the background service that builds and runs Docker containers.

*Call graph*: calls 1 internal fn (pod_dockerfile); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 266–281)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a newly published E2B template actually boots and contains the required tools. This prevents a broken sandbox image from being treated as successfully published.

**Data flow**: It receives the template name, starts a sandbox from that template, runs the readiness command inside it, and then shuts the sandbox down. If the command fails or exits unsuccessfully, it raises an error saying the published template is missing expected runtime tools.

**Call relations**: After the main command builds and publishes the E2B template, it calls this as a publish gate. The function hands the final judgment to the same readiness probe that was baked into the template, so build success means the tools are really present.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 284–305)

```
def check_published_template(name: str) -> None
```

**Purpose**: Checks whether the already-published E2B template matches the current source recipe, without publishing anything. This is a drift check: it catches cases where source changed but the live sandbox was not rebuilt.

**Data flow**: It calculates the expected digest from the current source, boots the live template, reads the digest file from inside the sandbox, and shuts the sandbox down. If the digest is missing or different, it raises an error telling the user to republish the sandbox template.

**Call relations**: The main command calls this when the user passes the check flag, such as in continuous integration. It uses the digest function for today’s recipe and E2B sandbox startup to inspect what is actually live.

*Call graph*: calls 1 internal fn (build_definition_digest); called by 1 (main); 1 external calls (create).


##### `main`  (lines 308–339)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for building, checking, printing, or Docker-building the sandbox image. It decides which path to run based on the user’s flags.

**Data flow**: It reads command-line arguments, chooses exactly one action, and then runs it. It may print a Dockerfile, build a Docker image, check the live E2B template, or build and publish the E2B template and verify it afterward. Its outputs are either printed status information or errors that stop the command.

**Call relations**: This is the top-level dispatcher for the file. Depending on the chosen mode, it calls the Dockerfile generator, Docker builder, drift checker, E2B template builder, E2B publish operation, and published-template verifier in the order needed for that user request.

*Call graph*: calls 5 internal fn (build_docker_image, check_published_template, e2b_template, pod_dockerfile, verify_published_template); 2 external calls (ArgumentParser, build).


### Workspace storage
Provides blob-backed workspace storage, shared artifacts, temporary storage credentials, and mount command generation.

### `core/src/ufo/blob.py`

`io_transport` · `cross-cutting storage access during request handling, background work, and exports`

Many parts of the project need a place to put files, attachments, workspace outputs, and records that may be too large to keep in memory. This file is that storage layer. It defines a common promise, called BlobStore, for saving, reading, copying, deleting, streaming, and listing byte objects using string keys that look like paths.

There are two real backends. FilesystemBlobStore stores each key as a file under a configured root folder. It writes through a temporary file and then replaces the final file, like writing a letter on scratch paper before swapping it into the official folder, so readers do not see half-written data. It also checks that keys cannot escape the storage root.

S3BlobStore stores the same kind of objects in an S3 bucket. S3 is a cloud object store, meaning it keeps named byte objects rather than normal folders. For large uploads and copies, it uses multipart operations, which split the work into pieces so huge files can move safely without loading everything at once. The helper blob_store_for chooses the right backend from configuration.

#### Function details

##### `BlobStore.put`  (lines 46–46)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Defines the promise for saving an in-memory block of bytes under a storage key. Callers use it when the whole payload is already available in memory.

**Data flow**: A caller provides a key and bytes. A concrete store, such as the filesystem or S3 version, writes those bytes under that key. Nothing is returned; after it finishes, the key should point to the saved bytes.

**Call relations**: Workspace setup and the sample extension call this promise when they need to save small generated content. The actual work is performed by whichever BlobStore implementation was configured.

*Call graph*: called by 2 (ensure_workspace_marker, export).


##### `BlobStore.put_file`  (lines 48–51)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: Defines the promise for saving a local file into blob storage without first reading the whole file into memory. This matters for large workspace files or exported artifacts.

**Data flow**: A caller provides a destination key and a local file path. The concrete store reads the file from disk and writes its bytes into storage. Nothing is returned; the storage key becomes the new copy of that file.

**Call relations**: Local and Docker carriers call this when they export a produced file. The protocol lets those callers work the same way whether storage is local disk or S3.

*Call graph*: called by 2 (export, export).


##### `BlobStore.get`  (lines 53–53)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Defines the promise for reading a whole stored object as bytes. It is useful for small records or identity data that callers need all at once.

**Data flow**: A caller provides a key. The concrete store looks up that key and returns the stored bytes, or raises BlobNotFound if the key does not exist.

**Call relations**: Transcript and Slack identity code call this promise when they need stored records. The storage backend decides how to fetch the bytes.

*Call graph*: called by 2 (read_compaction_record, read_identity).


##### `BlobStore.exists`  (lines 55–55)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Defines the promise for checking whether a key currently has an object behind it. This lets callers avoid trying to read optional data that may not be present.

**Data flow**: A caller provides a key. The concrete store checks its storage system and returns true if a stored object exists there, otherwise false.

**Call relations**: Slack identity reading uses this before deciding how to proceed with identity data. The filesystem and S3 implementations each perform the check in their own way.

*Call graph*: called by 1 (read_identity).


##### `BlobStore.delete`  (lines 57–59)

```
async def delete(self, key: str) -> None
```

**Purpose**: Defines the promise for removing a stored object. Deleting a missing key is intentionally harmless, so retrying cleanup does not create a new failure.

**Data flow**: A caller provides a key. The concrete store removes that object if it is present. Nothing is returned, and absence is treated as already done.

**Call relations**: This is part of the shared storage contract. Any caller that needs cleanup can use it without caring whether the object is on disk or in S3.


##### `BlobStore.get_stream`  (lines 61–61)

```
def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines the promise for reading a stored object piece by piece. This is for large blobs where loading the entire content into memory would be wasteful or unsafe.

**Data flow**: A caller provides a key. The concrete store opens the object and yields chunks of bytes over time. The caller receives a stream of chunks until the object is fully read, or BlobNotFound if it is missing.

**Call relations**: This method is part of the common storage shape. Implementations provide the chunked reading behavior for local files and S3 objects.


##### `BlobStore.put_stream`  (lines 63–63)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Defines the promise for writing a blob from a stream of byte chunks. It lets the system accept or produce large data without holding it all in memory.

**Data flow**: A caller provides a key and an async stream of byte chunks. The concrete store consumes the chunks in order and writes them as one stored object. Nothing is returned when the upload is complete.

**Call relations**: This belongs to the shared BlobStore contract. The concrete backends decide how to turn the incoming chunks into a local file or an S3 object.


##### `BlobStore.copy`  (lines 65–70)

```
async def copy(self, src_key: str, dst_key: str) -> None
```

**Purpose**: Defines the promise for duplicating an object inside the same storage system. It is important because a file can be promoted or shared without sending all bytes through the application process.

**Data flow**: A caller provides a source key and a destination key. The concrete store verifies or reads the source and creates a second object at the destination. Nothing is returned; if the source is absent, BlobNotFound is raised.

**Call relations**: Docker and E2B export paths use this when an existing workspace file needs to become an exported artifact. S3 can do this server-side, while the filesystem backend copies files locally.

*Call graph*: called by 2 (export, export).


##### `BlobStore.list`  (lines 72–76)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Defines the promise for listing stored objects under a required key prefix. This gives callers a bounded view of one area of storage rather than scanning everything.

**Data flow**: A caller provides a non-empty prefix. The concrete store finds matching objects and returns BlobEntry records with each key, size, and last modified time, capped at a fixed maximum.

**Call relations**: This is the common listing contract used by read views such as workspace or record browsing. Both backends return the same kind of entries.


##### `FilesystemBlobStore.put`  (lines 85–90)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves a block of bytes into the local filesystem backend. It uses an atomic write pattern so a reader should not see a partly written file.

**Data flow**: It receives a key and bytes, turns the key into a safe path under the root, creates parent folders, writes the bytes to a uniquely named temporary file, and replaces the final file with that temporary file. It returns nothing but changes the file tree.

**Call relations**: This is the filesystem implementation of BlobStore.put. It relies on _resolve to keep paths inside the storage root and uses background threads for blocking disk work so the async event loop is not stalled.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.put_file`  (lines 92–97)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: Stores an existing local file into the filesystem blob store. It copies from file to file rather than loading the whole source into memory.

**Data flow**: It receives a storage key and a source path, resolves the destination path safely, creates needed folders, copies the source file to a temporary destination, and then replaces the final destination file. The stored object becomes a copy of the source file.

**Call relations**: This is the local-disk version of the BlobStore.put_file promise used by export code. It uses _resolve for safety and background thread calls for disk operations.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.get`  (lines 99–104)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete stored file from the filesystem backend. It translates normal missing-file errors into the project-specific BlobNotFound error.

**Data flow**: It receives a key, resolves it to a safe local path, and reads all bytes from that file. It returns the bytes, or raises BlobNotFound if the file is not there.

**Call relations**: This implements BlobStore.get for local storage. It depends on _resolve for path safety and uses a thread for the blocking read.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.exists`  (lines 106–108)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether a blob key is present as a file in the local storage root. It is a lightweight way to ask whether optional content exists.

**Data flow**: It receives a key, resolves it to a safe path, and checks whether that path is a regular file. It returns true or false and does not change storage.

**Call relations**: This is the filesystem implementation of BlobStore.exists. It uses _resolve first so even existence checks cannot probe outside the configured blob root.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.delete`  (lines 110–112)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes a local stored file if it exists. Missing files are ignored so cleanup can be repeated safely.

**Data flow**: It receives a key, resolves it to a safe path, and asks the filesystem to unlink, or remove, that file with missing files allowed. It returns nothing and may remove one file.

**Call relations**: This is the filesystem implementation of BlobStore.delete. It follows the protocol’s no-error-on-missing behavior.

*Call graph*: calls 1 internal fn (_resolve); 1 external calls (to_thread).


##### `FilesystemBlobStore.get_stream`  (lines 114–127)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a local blob in fixed-size chunks. This keeps memory use bounded when the stored file is large.

**Data flow**: It receives a key, resolves and opens the file, then repeatedly reads up to the configured chunk size and yields each chunk. It closes the file when finished or if reading stops early, and raises BlobNotFound if the file is absent.

**Call relations**: This implements the streaming read part of BlobStore for local disk. It uses _resolve for safety and runs file operations in threads so other async work can continue.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (__init__, to_thread).


##### `FilesystemBlobStore.put_stream`  (lines 129–142)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes a stream of chunks into a local blob file. It is the large-data counterpart to putting one in-memory byte string.

**Data flow**: It receives a key and an async source of byte chunks. It resolves the destination, writes each chunk to a temporary file, closes it, and atomically replaces the final file. If anything goes wrong, it closes and removes the temporary file before re-raising the error.

**Call relations**: This is the filesystem implementation of BlobStore.put_stream. It combines _resolve, temporary files, and thread-backed disk writes to make streamed uploads safe and non-blocking to the event loop.

*Call graph*: calls 1 internal fn (_resolve); 2 external calls (to_thread, uuid4).


##### `FilesystemBlobStore.copy`  (lines 144–152)

```
async def copy(self, src_key: str, dst_key: str) -> None
```

**Purpose**: Copies one local blob to another local key. It preserves the rule that the destination should appear all at once, not half-copied.

**Data flow**: It receives source and destination keys, resolves both safely, and first checks that the source is a file. It copies the source into a temporary destination file and then replaces the final destination. It raises BlobNotFound if the source is missing.

**Call relations**: This implements BlobStore.copy for the filesystem backend. Export paths can call the generic copy promise, and this version performs an ordinary local file copy under the hood.

*Call graph*: calls 1 internal fn (_resolve); 3 external calls (__init__, to_thread, uuid4).


##### `FilesystemBlobStore.list`  (lines 154–157)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists local blobs whose keys start with a given prefix. It refuses an empty prefix to avoid accidentally walking the whole storage tree.

**Data flow**: It receives a prefix, checks that it is not empty, and then runs _walk in a background thread. It returns a tuple of BlobEntry records for matching files.

**Call relations**: This is the filesystem implementation of BlobStore.list. It delegates the actual directory walking and entry creation to _walk.

*Call graph*: 1 external calls (to_thread).


##### `FilesystemBlobStore._walk`  (lines 159–180)

```
def _walk(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Performs the actual filesystem scan for FilesystemBlobStore.list. It turns matching files into BlobEntry records with key, size, and modification time.

**Data flow**: It receives a prefix, chooses the directory area that could contain matching files, walks through files below it, ignores temporary files and non-matching keys, reads each file’s size and timestamp, sorts by key, and returns only up to the configured maximum.

**Call relations**: FilesystemBlobStore.list calls this in a worker thread because directory walking and file stat calls are blocking disk work. It uses _resolve to stay inside the storage root and creates the BlobEntry objects returned to callers.

*Call graph*: calls 1 internal fn (_resolve); 4 external calls (__init__, fromtimestamp, walk, Path).


##### `FilesystemBlobStore._resolve`  (lines 182–187)

```
def _resolve(self, key: str) -> Path
```

**Purpose**: Converts a blob key into a safe path under the configured local root. This is the guardrail that prevents a malicious or mistaken key from reaching files outside blob storage.

**Data flow**: It receives a key, joins it to the root folder, resolves both to real absolute paths, and checks that the result is inside the root and not the root itself. It returns the safe path or raises ValueError if the key escapes the store.

**Call relations**: Nearly every filesystem operation calls this before touching disk. It is the shared safety step for put, get, delete, streaming, copying, and listing.

*Call graph*: called by 9 (_walk, copy, delete, exists, get, get_stream, put, put_file, put_stream).


##### `_is_missing_key`  (lines 190–191)

```
def _is_missing_key(error: ClientError) -> bool
```

**Purpose**: Recognizes S3 errors that mean an object was not found. S3 and compatible services can report this with several different error codes, so this helper centralizes the check.

**Data flow**: It receives a ClientError from the S3 library, looks inside the error response for the error code, and returns true if that code is one of the known missing-object codes. It does not change anything.

**Call relations**: S3BlobStore uses this in get, get_stream, exists, and copy so all those methods treat missing keys consistently and convert them into BlobNotFound or false as appropriate.

*Call graph*: called by 4 (copy, exists, get, get_stream).


##### `S3BlobStore.put`  (lines 210–212)

```
async def put(self, key: str, data: bytes) -> None
```

**Purpose**: Saves an in-memory block of bytes as an object in an S3 bucket. It is the simple upload path for data that is already fully available.

**Data flow**: It receives a key and bytes, gets or creates an S3 client for the current async event loop, and sends a put-object request to store the bytes in the configured bucket. It returns nothing after S3 accepts the upload.

**Call relations**: This implements BlobStore.put for S3. It begins by calling _client so S3 connection setup is reused instead of rebuilt on every operation.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.put_file`  (lines 214–246)

```
async def put_file(self, key: str, source: Path) -> None
```

**Purpose**: Uploads a local file to S3, using multipart upload for non-empty files. Multipart upload splits a large file into pieces so it can be sent reliably without one huge memory buffer.

**Data flow**: It receives a key and source file path, checks the file size, and gets an S3 client. Empty files are uploaded directly. Otherwise it starts a multipart upload, reads fixed-size pieces from the file, uploads each part, and asks S3 to assemble them; if any step fails, it aborts the upload and closes the file descriptor.

**Call relations**: This is the S3 implementation of BlobStore.put_file used by export flows when the configured blob backend is S3. It depends on _client for the S3 connection and uses background thread calls for local file reads and file descriptor operations.

*Call graph*: calls 1 internal fn (_client); 1 external calls (to_thread).


##### `S3BlobStore.get`  (lines 248–258)

```
async def get(self, key: str) -> bytes
```

**Purpose**: Reads a complete object from S3 as bytes. It maps S3’s missing-object errors into BlobNotFound so callers see the same error shape as other backends.

**Data flow**: It receives a key, gets an S3 client, asks S3 for the object, and reads the response body fully. It returns the bytes, raises BlobNotFound if S3 says the key is missing, or re-raises other S3 errors.

**Call relations**: This implements BlobStore.get for S3. It calls _client for the cached client and _is_missing_key to make missing-object behavior match the rest of the storage layer.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.exists`  (lines 260–268)

```
async def exists(self, key: str) -> bool
```

**Purpose**: Checks whether an object exists in S3 without downloading it. It uses S3 metadata lookup, which is cheaper than reading the full object.

**Data flow**: It receives a key, gets an S3 client, and sends a head-object request. If S3 reports a known missing-key error it returns false; if the request succeeds it returns true; other errors are allowed to surface.

**Call relations**: This is the S3 implementation of BlobStore.exists. It uses _is_missing_key so missing objects become a simple false result.

*Call graph*: calls 2 internal fn (_client, _is_missing_key).


##### `S3BlobStore.delete`  (lines 270–272)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes an object from the configured S3 bucket. S3 delete requests are safe to send even when the object is already absent.

**Data flow**: It receives a key, gets an S3 client, and sends a delete-object request for that key in the configured bucket. It returns nothing after the request completes.

**Call relations**: This implements BlobStore.delete for S3. It calls _client to reuse the event-loop-specific S3 client.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.get_stream`  (lines 274–285)

```
async def get_stream(self, key: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams an S3 object in chunks instead of reading it all at once. This is useful for large attachments or files that should pass through the service with limited memory use.

**Data flow**: It receives a key, gets an S3 client, opens the S3 object body, and yields chunks of bytes up to the configured chunk size. It closes the response body when done and raises BlobNotFound if S3 reports the key is missing.

**Call relations**: This is the S3 implementation of BlobStore.get_stream. It uses _client for access and _is_missing_key for consistent missing-object handling.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.put_stream`  (lines 287–331)

```
async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Uploads incoming byte chunks to S3 as one object. Small streams are sent as a normal put, while larger streams are sent as a multipart upload.

**Data flow**: It receives a key and an async stream of chunks. It collects chunks until it has enough for an S3 part; once needed, it starts a multipart upload, uploads each part, and finally completes the upload with S3. If the stream was small, it stores it with one put-object request; if an error occurs after multipart upload starts, it aborts that upload.

**Call relations**: This implements BlobStore.put_stream for S3. It calls _client first, then chooses between simple upload and multipart upload based on how much data arrives.

*Call graph*: calls 1 internal fn (_client).


##### `S3BlobStore.copy`  (lines 333–369)

```
async def copy(self, src_key: str, dst_key: str) -> None
```

**Purpose**: Copies an object to another key inside the same S3 bucket without downloading it through the application. This saves bandwidth and memory, especially for large workspace artifacts.

**Data flow**: It receives source and destination keys, gets an S3 client, and checks the source object’s size. Missing sources become BlobNotFound. Small enough objects are copied with one S3 copy request; larger ones are copied in byte ranges as multipart copy parts and then assembled. If multipart copy fails, it aborts the unfinished destination upload.

**Call relations**: This is the S3 implementation of BlobStore.copy, used by export flows through the generic BlobStore interface. It calls _client for S3 access and _is_missing_key to translate absent sources consistently.

*Call graph*: calls 2 internal fn (_client, _is_missing_key); 1 external calls (__init__).


##### `S3BlobStore.list`  (lines 371–388)

```
async def list(self, prefix: str) -> tuple[BlobEntry, ...]
```

**Purpose**: Lists S3 objects whose keys begin with a required prefix. The prefix requirement keeps listing focused on one area instead of accidentally scanning the bucket broadly.

**Data flow**: It receives a prefix, rejects it if empty, gets an S3 client, and pages through S3 list results. For each object it creates a BlobEntry with key, size, and UTC modification time, stopping at the configured maximum. It returns those entries as a tuple.

**Call relations**: This implements BlobStore.list for S3. It uses the S3 paginator to move through result pages and returns the same BlobEntry shape as the filesystem backend.

*Call graph*: calls 1 internal fn (_client); 1 external calls (__init__).


##### `S3BlobStore._client`  (lines 390–406)

```
async def _client(self) -> AioBaseClient
```

**Purpose**: Returns a reusable S3 client for the current async event loop. This avoids repeatedly building expensive clients and avoids sharing a client across event loops where it may not be safe.

**Data flow**: It looks up the currently running event loop and checks a cache. If a client already exists for that loop, it returns it. Otherwise it creates a new aiobotocore S3 client with the configured endpoint and region, stores it in the cache, and returns it; if another client won the race, it closes the redundant one and logs if that close fails.

**Call relations**: Every S3BlobStore operation calls this before talking to S3. It is the connection factory and cache that keeps blob traffic from repeatedly paying client setup costs.

*Call graph*: called by 9 (copy, delete, exists, get, get_stream, list, put, put_file, put_stream); 3 external calls (get_session, get_running_loop, log).


##### `blob_store_for`  (lines 409–421)

```
def blob_store_for(config: BlobConfig) -> FilesystemBlobStore | S3BlobStore
```

**Purpose**: Builds the correct blob store from configuration. It is the small factory that turns settings into a working storage backend.

**Data flow**: It receives a BlobConfig, looks at the selected backend name, checks that the required fields are present, and returns either a FilesystemBlobStore or an S3BlobStore. If required configuration is missing, it raises ValueError.

**Call relations**: Startup or setup code can call this once it has loaded configuration. The returned object is then used through the common BlobStore interface by the rest of the system.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/artifacts.py`

`domain_logic` · `request handling`

This file is the bridge between files produced during a conversation and the object system that users can browse. Think of an artifact like a labeled package put on a shared shelf: the file bytes live in blob storage, while the database keeps the label, size, type, sharing turn, and time. This code turns those database rows into stable object names and actions.

Artifacts are grouped by two things: the conversation that shared them and the filename. If the same conversation shares the same filename again, that is treated as a new version of the same artifact. If another conversation shares a file with the same filename, it becomes a different artifact. The public name combines a short conversation prefix with a cleaned-up filename, so related files naturally sort together.

The `ArtifactObjects` class provides the object behavior. Listing shows the latest version and a short summary. Getting an artifact returns its details and points back to the conversation that created it. Status does extra work: it may create a temporary download link and, if the file is not too large, copy the latest bytes back into the workspace so a later turn can reuse the file. Create and update are deliberately refused, because artifacts must come from sharing an existing workspace file. Delete removes every stored version and its bytes.

#### Function details

##### `artifact_object_names`  (lines 65–85)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Builds the user-facing object name for each distinct artifact identity. It keeps files from different conversations separate even when their filenames match, and only adds a short hash suffix when two cleaned-up names would otherwise collide.

**Data flow**: It receives pairs of conversation ID and filename. It cleans each filename into a safe short slug, prefixes it with the first part of the conversation ID, counts duplicate proposed names, and returns a mapping from each original pair to its final object name.

**Call relations**: When artifact rows are grouped in `ArtifactObjects._groups`, this function gives each group the name that listing, getting, status, and delete will use. It relies on `_slug` for readable filenames and `_identity_digest` only when a collision needs an extra unique marker.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_groups); 1 external calls (Counter).


##### `_slug`  (lines 88–90)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short, plain, object-name-friendly piece of text. This avoids exposing awkward characters and keeps artifact names readable.

**Data flow**: It takes a filename, lowercases it, replaces runs of non-letter-or-number characters with dashes, trims extra dashes, limits the length, and returns a fallback word if nothing usable remains.

**Call relations**: `artifact_object_names` calls this while building artifact names. It provides the human-readable filename part of names such as a cleaned `report.txt` becoming something like `report-txt`.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 93–95)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a stable shortable fingerprint for an artifact identity. It is used to distinguish two artifacts only when their conversation prefix and cleaned filename would otherwise produce the same name.

**Data flow**: It takes a conversation ID and filename, joins them into one string, hashes that string with SHA-256, and returns the hexadecimal hash text.

**Call relations**: `artifact_object_names` calls this only for name collisions. In normal cases artifact names stay simple; this helper acts like a tie-breaker tag.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 113–125)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a paged list of artifact objects visible in the current workspace. Each list entry represents one conversation-and-filename group, with the latest version summarized.

**Data flow**: It reads the artifact groups from `_groups`, turns each group into an object row with a name, summary, filename, and subject, then passes those rows through the object paging helper using the caller’s list query.

**Call relations**: This is the list operation for the artifact object kind. It asks `_groups` for the current artifact inventory, uses `_summary` to make each entry understandable, and hands the result to the shared object-listing machinery.

*Call graph*: calls 2 internal fn (_groups, _summary); 2 external calls (__init__, object_page).


##### `ArtifactObjects.get`  (lines 127–146)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Returns the main details for one named artifact, or nothing if that name does not exist. It describes the latest shared version and links it back to the conversation that created it.

**Data flow**: It receives an artifact name, looks up its versions with `_find`, chooses the newest share, and builds an object detail containing filename, media type, subject, first-created time, latest-updated time, and a `created_in` link to the conversation.

**Call relations**: The object system calls this when someone asks to inspect an artifact. It uses `_find` to translate the public name into stored rows, then packages the data in the standard object-detail shape.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `ArtifactObjects.status`  (lines 148–167)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information for an artifact and may copy the latest file back into the workspace. This is the operation that makes a previously shared file reusable by later work.

**Data flow**: It receives a context and artifact name, finds the latest version, optionally mints a temporary download URL if token signing is configured, calls `_materialize` to write the file into the workspace when it is small enough, and returns size, share time, turn ID, version count, download URL, and workspace path.

**Call relations**: The object-get flow calls status when it needs the extra live information. It depends on `_find` to locate the artifact, `mint_artifact_token` to make a time-limited download link, and `_materialize` to restore the file into the sandbox workspace.

*Call graph*: calls 2 internal fn (_find, _materialize); 2 external calls (now, mint_artifact_token).


##### `ArtifactObjects.apply`  (lines 169–172)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None) -> None
```

**Purpose**: Refuses attempts to create or update artifacts through the generic object interface. This protects the rule that artifacts only exist after a file has been shared with `share_file`.

**Data flow**: It receives the context, name, desired spec, and old spec, but does not use them to write anything. Instead, it raises a clear unsupported-operation error telling the caller to use sharing instead.

**Call relations**: The object system would call this for create or update operations. For artifacts, the function intentionally stops that path and points callers back to the producer flow, `share_file`.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 174–186)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Deletes an artifact completely, including all of its versions. After this, the database records are gone and the stored file bytes are removed, so old download links no longer work.

**Data flow**: It receives an artifact name, finds all stored versions, removes their rows from the current workspace’s `shared_artifact` table inside a database transaction, then deletes each version’s blob from blob storage.

**Call relations**: The object system calls this when a user deletes an artifact. It uses `_find` to gather every version for that object, then coordinates database deletion and blob deletion so the shared file is no longer available.

*Call graph*: calls 1 internal fn (_find); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._materialize`  (lines 188–199)

```
async def _materialize(self, ctx: ToolContext, name: str, latest: sa.Row) -> str | None
```

**Purpose**: Copies the latest artifact bytes from blob storage back into the conversation workspace, unless the file is too large. This lets a later turn reuse a file that an earlier turn produced.

**Data flow**: It receives the context, artifact name, and latest share row. If the stored size is above the configured limit, it returns no path. Otherwise it reads the bytes from blob storage, writes them to `artifacts/<name>/<filename>` inside the sandbox workspace, and returns that path.

**Call relations**: `ArtifactObjects.status` calls this as part of status reporting. It is deliberately not called by every internal lookup, so actions like delete or detail building do not unexpectedly write files into the workspace.

*Call graph*: called by 1 (status).


##### `ArtifactObjects._find`  (lines 201–203)

```
async def _find(self, name: str) -> tuple[sa.Row, ...] | None
```

**Purpose**: Looks up one artifact by its public object name. It hides the work of turning database rows into named groups from the higher-level get, status, and delete operations.

**Data flow**: It receives a name, asks `_groups` for all named artifact groups, searches for the matching name, and returns that group’s version rows or `None` if there is no match.

**Call relations**: `ArtifactObjects.get`, `ArtifactObjects.status`, and `ArtifactObjects.delete` all call this before acting on a single artifact. It acts like the small name-to-record lookup step between the object API and the stored artifact rows.

*Call graph*: calls 1 internal fn (_groups); called by 3 (delete, get, status).


##### `ArtifactObjects._groups`  (lines 205–238)

```
async def _groups(self) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Builds the current artifact catalog for the workspace. It collects raw shared-file rows, groups versions together, assigns each group its object name, and sorts everything for stable display.

**Data flow**: It opens a workspace database transaction, selects shared artifact rows joined to their turns so it can know the conversation ID, filters to the current workspace, groups rows by conversation ID and filename, asks `artifact_object_names` for public names, sorts each group with newest versions first, and returns the sorted named groups.

**Call relations**: This is the main data-gathering helper for artifact objects. `ArtifactObjects.list` uses it to show all artifacts, while `_find` uses it to locate one named artifact for get, status, or delete.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, list); 3 external calls (select, workspace_tx, ws_current).


##### `_summary`  (lines 241–247)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short human-readable sentence for an artifact list entry. It shows the latest filename, type, size, date shared, and whether there are multiple versions.

**Data flow**: It receives the version rows for one artifact, reads the newest row, adds a version count note when needed, trims the text to the summary length limit, and returns that summary string.

**Call relations**: `ArtifactObjects.list` calls this while building each listed object row. It turns stored metadata into the quick description a user sees before opening the artifact.

*Call graph*: called by 1 (list).


### `core/src/ufo/sandbox/fs_creds.py`

`domain_logic` · `sandbox mount setup and credential refresh`

A sandbox needs a mounted workspace, like a temporary folder where the agent can read and write files. In this system that workspace lives inside a blob bucket, such as AWS S3 or MinIO. The danger is that ordinary bucket credentials would be too powerful: if leaked or misused, they could expose private conversation records or other users’ work. This file solves that by minting temporary credentials that are locked to one narrow path: `conversations/<conversation_id>/workspace/`.

The flow has two layers. First, the server creates a signed token that names a conversation and, for normal runs, a specific live turn. A signed token is like a tamper-proof ticket: the sandbox proxy can verify that the server issued it. Second, when the sandbox filesystem needs fresh credentials, the token is redeemed. The minter checks that the token is valid and, for turn tokens, asks whether that turn is still allowed. Only then does it call STS, the cloud service that issues temporary access keys, with an inline policy that limits access to the workspace prefix.

The file also creates a tiny empty “directory marker” object before mounting. This matters because some S3 filesystem tools behave badly when asked to mount a totally empty prefix. Without this file, sandbox file access would either be too broad and unsafe, or too brittle to mount reliably.

#### Function details

##### `SandboxFsCredentials.ecs_json`  (lines 48–58)

```
def ecs_json(self) -> bytes
```

**Purpose**: This turns a temporary credential into the JSON shape expected by ECS-style credential consumers. In plain terms, it packages the access key, secret, session token, expiration time, and role name into the format the sandbox filesystem can read.

**Data flow**: It starts with a `SandboxFsCredentials` object already holding temporary access details. It formats those fields with the exact key names expected by the consumer, changes the expiration time into a standard text timestamp, encodes the JSON as bytes, and returns those bytes. It does not change the credential object.

**Call relations**: After credentials have been minted elsewhere, this method is the final packaging step when something needs to send them over an HTTP-like credential endpoint or another byte-based channel.

*Call graph*: 1 external calls (dumps).


##### `StsClient.assume_role`  (lines 86–88)

```
async def assume_role(self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int) -> Any
```

**Purpose**: This is the shared promise for anything that can ask STS for temporary credentials. STS means Security Token Service, a service that trades an existing trusted identity for short-lived, restricted access keys.

**Data flow**: A caller provides a role name, a session name, a permission policy, and a duration. An implementation is expected to send those details to an STS-compatible service and return the service’s response, which contains temporary credentials.

**Call relations**: The credential minter depends on this promise rather than one specific STS implementation. That lets production use the real AWS or MinIO client while tests or other environments can provide a stand-in with the same behavior.


##### `workspace_key_prefix`  (lines 91–96)

```
def workspace_key_prefix(conversation_id: UUID) -> str
```

**Purpose**: This builds the exact bucket path that belongs to one conversation’s sandbox workspace. It is the central rule that says the sandbox may touch `workspace/` files, not the transcript or other framework-owned records above that folder.

**Data flow**: It takes a conversation ID and inserts it into the standard path pattern. The output is a string such as `conversations/<id>/workspace`, which later code uses for marker creation and permission policies.

**Call relations**: When preparing a mount, `ensure_workspace_marker` uses this to decide where to place the empty marker object. When minting credentials, `SandboxFsCredentialMinter._mint` uses the same prefix so the permission policy matches the mounted workspace exactly.

*Call graph*: called by 2 (_mint, ensure_workspace_marker).


##### `ensure_workspace_marker`  (lines 99–108)

```
async def ensure_workspace_marker(blob: BlobStore, conversation_id: UUID) -> str
```

**Purpose**: This creates an empty object that makes the workspace prefix exist before the sandbox filesystem tries to mount it. It avoids a startup failure that can happen when the filesystem tool mounts a completely empty S3 prefix.

**Data flow**: It receives a blob store connection and a conversation ID. It builds the workspace prefix, adds a trailing slash to make a directory-like marker key, writes empty bytes to that key, and returns the exact key it wrote. The blob bucket is changed by gaining that empty marker object.

**Call relations**: Mount preparation code calls this before the sandbox filesystem starts using the prefix. It relies on `workspace_key_prefix` for the canonical path and hands the key back so later cleanup code can delete exactly the object it created if needed.

*Call graph*: calls 2 internal fn (put, workspace_key_prefix).


##### `workspace_prefix_policy`  (lines 111–140)

```
def workspace_prefix_policy(bucket: str, key_prefix: str) -> str
```

**Purpose**: This writes the restrictive permission policy used when asking STS for sandbox credentials. The policy allows reading, writing, deleting, and listing only inside one workspace prefix.

**Data flow**: It receives a bucket name and a workspace key prefix. It builds a compact JSON policy that grants object access below that prefix and limits bucket listing to that same prefix. The result is a JSON string sent to STS as the rulebook for the temporary credentials.

**Call relations**: During credential minting, `SandboxFsCredentialMinter._mint` calls this so STS returns keys that are useful for the sandbox mount but useless outside that workspace. It works together with `workspace_key_prefix` to enforce the security boundary.

*Call graph*: called by 1 (_mint); 1 external calls (dumps).


##### `issue_sandbox_fs_gate_token`  (lines 143–150)

```
def issue_sandbox_fs_gate_token(conversation_id: UUID, token_secret: bytes, expires_at: datetime) -> str
```

**Purpose**: This creates a short-lived signed token for a deploy-time or gate-style workspace credential check. It is separate from normal turn tokens because it expires by time rather than by checking a live conversation turn.

**Data flow**: It takes a conversation ID, a secret signing key, and an expiration time. It stores the conversation ID and expiration timestamp as token claims, signs them so they cannot be changed without detection, and returns the signed token text.

**Call relations**: The refresh path in `SandboxFsCredentialMinter.refresh` later recognizes this as a gate token. Instead of asking whether a run turn is live, refresh checks whether the token’s expiration time has passed before minting credentials.

*Call graph*: 3 external calls (__init__, timestamp, sign_token).


##### `_utc_now`  (lines 153–154)

```
def _utc_now() -> datetime
```

**Purpose**: This returns the current time in UTC, the standard time zone used for comparing token expiration times. Keeping it as a small function also makes the minter easier to test because its clock can be replaced.

**Data flow**: It reads the system clock, asks for the time in UTC, and returns a timezone-aware `datetime` value. It does not modify anything.

**Call relations**: A `SandboxFsCredentialMinter` uses this as its default clock. The refresh logic uses that clock when deciding whether a gate token has expired.

*Call graph*: 1 external calls (now).


##### `AwsStsClient.assume_role`  (lines 166–175)

```
async def assume_role(self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int) -> Any
```

**Purpose**: This is the real STS call used in production. It asks AWS STS or a MinIO-compatible STS endpoint to issue temporary credentials with the narrow policy supplied by the minter.

**Data flow**: It receives the role to assume, the session name, the restrictive policy, and the credential lifetime. It opens an STS client, sends the assume-role request, waits for the response, and returns the raw STS response containing temporary credentials.

**Call relations**: The credential minter calls this through the `StsClient` interface inside `SandboxFsCredentialMinter._mint`. This method delegates the actual client creation to `AwsStsClient._client`, then hands the STS response back to the minter for conversion into `SandboxFsCredentials`.

*Call graph*: calls 1 internal fn (_client).


##### `AwsStsClient._client`  (lines 177–180)

```
def _client(self) -> ClientCreatorContext
```

**Purpose**: This prepares an asynchronous STS client pointed at the configured AWS or MinIO endpoint. It is the small factory that knows the endpoint URL and region settings.

**Data flow**: It reads the `endpoint_url` and `region` stored on the `AwsStsClient`. It creates and returns a client context object for the `sts` service, using a default S3 region when none was configured.

**Call relations**: `AwsStsClient.assume_role` calls this right before sending an assume-role request. Keeping client creation here keeps the network setup details out of the higher-level credential minting logic.

*Call graph*: called by 1 (assume_role); 1 external calls (get_session).


##### `SandboxFsCredentialMinter.issue`  (lines 201–211)

```
def issue(self, conversation_id: UUID, run: RunToken) -> str
```

**Purpose**: This creates the normal signed token for a sandbox run turn. The token names the conversation, workspace, and turn, so later refreshes can be allowed only while that specific turn is still live.

**Data flow**: It receives a conversation ID and a `RunToken`, which contains the workspace ID and turn ID. It puts those values into a claims object, signs the serialized claims with the deploy secret, and returns an opaque token string. It does not reveal credentials yet.

**Call relations**: Workspace mount setup calls this when preparing a sandbox. The returned token is later presented to `SandboxFsCredentialMinter.refresh`, which verifies it and turns it into actual temporary storage credentials only if the run is still authorized.

*Call graph*: called by 1 (_workspace_mount); 2 external calls (__init__, sign_token).


##### `SandboxFsCredentialMinter.refresh`  (lines 213–228)

```
async def refresh(self, token: str, authorize: Callable[[RunToken], Awaitable[bool]]) -> SandboxFsCredentials
```

**Purpose**: This redeems a signed sandbox filesystem token for fresh temporary credentials. It is the security checkpoint that verifies the token and confirms the sandbox is still allowed to access its workspace.

**Data flow**: It takes a token string and an authorization callback. First it verifies the signature and parses the token claims. If the token names a run turn, it rebuilds the `RunToken` and asks the callback whether that turn is still valid. If the token is a gate token, it checks the expiration time. If anything fails, it raises `InvalidSandboxFsToken`; if everything passes, it mints and returns `SandboxFsCredentials` for the conversation workspace.

**Call relations**: The sandbox proxy calls this when the filesystem needs credentials or a refresh. Once the token checks pass, this function hands off to `SandboxFsCredentialMinter._mint`, which performs the STS request and returns the actual temporary keys.

*Call graph*: calls 1 internal fn (_mint); 3 external calls (__init__, __init__, verify_token).


##### `SandboxFsCredentialMinter._mint`  (lines 230–244)

```
async def _mint(self, conversation_id: UUID) -> SandboxFsCredentials
```

**Purpose**: This asks STS for the actual temporary access keys limited to one conversation’s workspace. It is where the verified permission to refresh becomes real storage credentials.

**Data flow**: It receives a conversation ID. It builds the workspace prefix, builds a policy that only allows that prefix, calls STS with the role, session name, policy, and lifetime, then pulls the credential fields out of the STS response. It returns a frozen `SandboxFsCredentials` object containing the temporary keys and expiration time.

**Call relations**: `SandboxFsCredentialMinter.refresh` calls this only after token verification and authorization have succeeded. This function uses `workspace_key_prefix` and `workspace_prefix_policy` so the returned STS credentials line up exactly with the sandbox’s mounted workspace.

*Call graph*: calls 2 internal fn (workspace_key_prefix, workspace_prefix_policy); called by 1 (refresh); 1 external calls (__init__).


##### `sandbox_fs_minter`  (lines 247–265)

```
def sandbox_fs_minter(blob: BlobConfig) -> SandboxFsCredentialMinter | None
```

**Purpose**: This builds a ready-to-use `SandboxFsCredentialMinter` from blob storage configuration. It also refuses unsafe or incomplete setup, such as missing S3 bucket settings or a missing token-signing secret.

**Data flow**: It reads the blob backend configuration. If the backend is not S3, it returns `None` because this credential flow is not needed. For S3, it checks that the bucket, S3 URL, role ARN, and signing secret are present. It then creates an `AwsStsClient` and wraps it in a configured `SandboxFsCredentialMinter`, returning that minter.

**Call relations**: Startup or setup code uses this to decide whether sandbox filesystem credential minting is available. It wires together configuration, the real STS client, and the token secret so later mount setup and refresh handling can call the minter directly.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/sandbox/fs_mount.py`

`io_transport` · `sandbox startup and attach-time mount checking`

A sandbox needs a working folder where conversation files live. In this system, those files are stored in S3, which is a cloud object store. This file describes how to make that S3 area appear inside the sandbox as a normal folder at `/workspace`, using `s3fs`, a tool that presents S3 like a filesystem.

The file is careful because mounting storage is a privileged operation. It prepares commands for several steps: making room for a private token, installing that token with strict permissions, preparing Linux FUSE access, starting a small local credential relay, running `s3fs`, and checking whether the mount is still healthy. FUSE means “Filesystem in Userspace”; it lets a normal program provide a filesystem instead of the operating system kernel doing all the work.

A key idea is that the sandbox does not give `s3fs` long-lived cloud credentials directly. Instead, it runs a local relay that reads a root-owned token file and exposes temporary credentials through a private local URL. This lets later sandbox attaches rotate the token without tearing down the mount.

Most functions return shell strings. They quote inserted values with `shlex.quote`, which is like putting fragile package labels on command parts so spaces or special characters cannot accidentally change what the shell runs.

#### Function details

##### `s3fs_command`  (lines 26–58)

```
def s3fs_command(bucket: str, key_prefix: str, mountpoint: str, s3_url: str, region: str, path_style: bool) -> str
```

**Purpose**: Builds the `s3fs` command that will mount one S3 bucket prefix at a local folder. It includes the options needed for this sandbox, such as allowing other users to read the mount and making S3 keys behave more like normal directories.

**Data flow**: It receives the S3 bucket name, the key prefix inside that bucket, the local mount folder, the S3 service URL, the region, and whether to use path-style S3 requests. It shell-quotes the values that will be inserted into the command, adds the required `s3fs` options, optionally adds the path-style option, and returns one complete command string. It does not touch S3 or the local filesystem itself.

**Call relations**: This is one of the command builders used when a carrier needs to mount a sandbox workspace. Its only direct helper is `shlex.quote`, used to make the command safe to pass through a shell. The command it returns is later fed into the larger mount script built by `mount_scripts`.

*Call graph*: 1 external calls (quote).


##### `prepare_token_staging_command`  (lines 61–63)

```
def prepare_token_staging_command() -> str
```

**Purpose**: Builds a command that creates the private directory where a new filesystem token can be staged before it is installed. This keeps token setup predictable and locked down to root access.

**Data flow**: It reads the fixed staging directory path from this file’s constants, quotes it for shell use, and returns an `install -d` command that creates the directory with owner `root` and permission mode `700`. The output is only command text; no directory is created until a carrier runs it.

**Call relations**: This supports the credential setup flow before mounting. It calls `shlex.quote` so the fixed directory path is still treated safely as a shell argument. A carrier can run the returned command before writing or rotating the sandbox filesystem token.

*Call graph*: 1 external calls (quote).


##### `install_token_command`  (lines 66–69)

```
def install_token_command() -> str
```

**Purpose**: Builds a command that moves a staged token into its final private location. It also tightens ownership and permissions so only root can read the token.

**Data flow**: It takes no caller-provided input. It reads the fixed staging token path and final token path, shell-quotes both, and returns a command that changes the staged file to root ownership, sets it to permission mode `600`, and atomically moves it into place. The result is a shell command string; the actual token file changes only happen when that command is executed.

**Call relations**: This is part of the token rotation path used before or during sandbox attachment. It relies on `shlex.quote` for safe shell text. The mounted filesystem’s local credential relay later reads the installed token path when `s3fs` asks for refreshed credentials.

*Call graph*: 1 external calls (quote).


##### `mount_scripts`  (lines 72–121)

```
def mount_scripts(mountpoint: str, s3fs: str, credential_url: str) -> tuple[str, str]
```

**Purpose**: Builds the two main root shell commands for mounting the workspace: one command to prepare the environment, and one command to actually start the credential relay and `s3fs` mount. The commands are designed to be safe to run again when reusing an existing sandbox.

**Data flow**: It receives the mountpoint path, a ready-made `s3fs` command string, and the credential endpoint URL. It quotes the shell-sensitive inputs, then creates a `prepare` command that opens FUSE access, enables `allow_other`, and lazily unmounts anything already at the mountpoint. It also creates a `mount` command that makes the mount folder, protects the token file, creates or reuses a relay secret, starts the local credential relay if needed, and runs `s3fs` as the unprivileged mount user. It returns both command strings as a pair.

**Call relations**: This is the central assembly point for the mount process. It expects a command such as the one produced by `s3fs_command`, wraps it with setup for FUSE and the local credential relay, and hands back scripts for the carrier to run. Its direct helper is `shlex.quote`, used throughout so paths and URLs are inserted safely into shell commands.

*Call graph*: 1 external calls (quote).


##### `mount_health_check`  (lines 124–136)

```
def mount_health_check(mountpoint: str) -> str
```

**Purpose**: Builds a probe command that decides whether an existing workspace mount is still usable. This lets the system skip an unnecessary unmount and remount when a sandbox is reused and the current mount is healthy.

**Data flow**: It receives the mountpoint path, quotes it, and reads the fixed relay secret path from this file’s constants. It returns a shell command that checks three things in order: the path is a mounted filesystem, the local credential relay answers using the stored secret, and listing the mountpoint succeeds through `s3fs`. The function only returns the test command; the health result is known only after a carrier runs it.

**Call relations**: This is used during attach-time decision making, before rerunning the heavier mount steps. It calls `shlex.quote` to safely include the mountpoint and secret path in the shell command. If the returned probe succeeds, the carrier can keep the current mount; if it fails, the carrier can run the prepare and mount commands again.

*Call graph*: 1 external calls (quote).


### Sandbox backends
Implements the local, Docker, and E2B carriers that create, reuse, execute within, transfer files to and from, and stop sandboxes.

### `core/src/ufo/sandbox/local.py`

`io_transport` · `sandbox setup, command execution, file transfer`

This file is the simple, no-container version of the project’s sandbox runner. A sandbox is normally a controlled place where the system can write files and run commands for one conversation. Here, that place is just a real folder on the host computer, and each command is started as a normal subprocess with that folder as its working directory.

The important tradeoff is safety. This local carrier is convenient, but it is not a strong security boundary. It does not stop a process at the operating-system level the way a container can. It relies on the rest of the tool layer to keep file paths inside the workspace.

The file also makes local execution behave like the more isolated carriers in one key way: internet access still goes through the sandbox proxy. When a command runs, it receives proxy environment variables, temporary model API keys called sentinels, and a certificate file so HTTPS traffic can be checked and metered consistently. It also places helper programs, `sbx` and `sbxfs`, on the command path so the same file and egress tools work locally.

In short, this is the “run it here on my machine” backend. It creates a usable workspace, runs commands there, writes and exports files, and clearly refuses features that only make sense for remote sandboxes, such as exposing a per-port external host.

#### Function details

##### `_provision_scratch`  (lines 40–53)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates a temporary support folder for the local carrier. This folder holds helper command-line tools and a fake home directory, keeping this scaffolding separate from the user’s actual workspace.

**Data flow**: It starts with no caller-provided input. It creates a new temporary directory, adds `home` and `bin` subfolders, copies the bundled `sbx` and `sbxfs` helper programs into `bin`, marks them executable, and returns the path to this scratch directory.

**Call relations**: This is used as the default setup for `LocalCarrier` when a carrier object is created. Later, `LocalCarrier.create` uses the scratch directory to build the command environment, especially `HOME`, `PATH`, and the proxy certificate location.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `LocalCarrier.create`  (lines 60–88)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Prepares a local sandbox session for one conversation. It makes sure the workspace folder exists and builds the environment variables that every later command will inherit.

**Data flow**: It receives a `SandboxSpec`, which includes the conversation identity, workspace mount, proxy details, run token, and extra environment values. It turns the mount into a host folder, creates that folder if needed, writes the proxy certificate into the scratch area, builds proxy URLs and tool paths, and returns a `SandboxHandle` containing the workspace and environment for future operations.

**Call relations**: This is the setup step before commands can run. It relies on `_root` to find the host workspace folder, then hands back a `SandboxHandle` that `LocalCarrier.exec`, `write`, `export`, `destroy`, and `host` receive later.

*Call graph*: calls 1 internal fn (_root); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 90–116)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command locally inside the conversation workspace. It makes command arguments that mention `/workspace` point at the real host folder, then starts the command as a subprocess.

**Data flow**: It receives a sandbox handle, a command as an argument tuple, and a timeout in seconds. It finds the real workspace folder, rewrites any `/workspace` paths in the arguments to that folder, starts the subprocess with the prepared proxy-aware environment, captures standard output and standard error, and returns an `ExecResult` with text output and an exit code. If the command takes too long, it kills the process and returns a timeout result.

**Call relations**: This is the main workhorse after `LocalCarrier.create` has prepared the handle. It calls `_root` to locate the workspace and uses the environment stored in the handle so local subprocesses still go through the same egress proxy rules as other sandbox types.

*Call graph*: calls 1 internal fn (_root); 3 external calls (__init__, create_subprocess_exec, wait_for).


##### `LocalCarrier.write`  (lines 118–123)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into a file inside the local workspace. It is used when the system needs to place an input file where sandboxed commands can read it.

**Data flow**: It receives a sandbox handle, a logical workspace path, and raw file content. It converts the logical path into a real host path, creates any missing parent folders, writes the bytes to disk, and returns nothing.

**Call relations**: This function uses `_host_path` to safely translate from the project’s `/workspace` naming convention to the host filesystem. It complements `LocalCarrier.exec`: files written here can then be used by commands run through `exec`.

*Call graph*: calls 1 internal fn (_host_path); 1 external calls (to_thread).


##### `LocalCarrier.export`  (lines 125–129)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Sends a file from the local workspace into the project’s blob storage. This is for produced files or large attachments that should be stored outside the workspace.

**Data flow**: It receives a sandbox handle, a logical workspace path, a blob store, and a storage key. It converts the logical path to the real host path, then asks the blob store to stream that file into storage under the given key. It does not return a value.

**Call relations**: This function uses `_host_path` to find the file that a previous command or write operation created. It then hands the file path to `BlobStore.put_file`, which performs the actual storage work.

*Call graph*: calls 2 internal fn (put_file, _host_path).


##### `LocalCarrier.destroy`  (lines 131–133)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Finishes a local sandbox session without deleting the workspace. There is no container or remote machine to tear down in this carrier.

**Data flow**: It receives the sandbox handle but does not need to read or change anything. The workspace is a durable host directory, and the per-command environment lives on the handle, so the function simply completes.

**Call relations**: This matches the lifecycle interface used by other carriers, where cleanup may be necessary. For the local carrier, it intentionally does nothing because `create` did not allocate an isolated runtime that must be reclaimed.


##### `LocalCarrier.host`  (lines 135–143)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Rejects requests for an externally reachable host address for a sandbox port. Local subprocesses do not provide the kind of network endpoint that remote carriers can expose.

**Data flow**: It receives a sandbox handle and a port number, but instead of returning a host address, it raises an error explaining that this feature is unavailable for the local carrier.

**Call relations**: Code that wants to preview or connect to an in-sandbox service may call this through the common carrier interface. This implementation stops that flow and points callers toward a remote carrier, such as e2b, for per-port access.


##### `_root`  (lines 146–149)

```
def _root(mount: MountSpec | None) -> Path
```

**Purpose**: Finds the real host directory used as the local workspace. It also enforces that the local carrier must be given a filesystem mount.

**Data flow**: It receives a mount specification, checks that it exists and has a host path, and returns that host path as a `Path`. If the mount is missing, it raises an error instead of guessing.

**Call relations**: `LocalCarrier.create` and `LocalCarrier.exec` call this when they need the workspace folder directly. `_host_path` also calls it as the first step in converting logical `/workspace` paths into real host paths.

*Call graph*: called by 3 (create, exec, _host_path); 1 external calls (Path).


##### `_host_path`  (lines 152–155)

```
def _host_path(handle: SandboxHandle, path: str) -> Path
```

**Purpose**: Converts a logical sandbox path like `/workspace/file.txt` into the matching path on the host machine. This is the bridge between the project’s sandbox path convention and the local filesystem.

**Data flow**: It receives a sandbox handle and a logical path string. It finds the workspace root from the handle’s mount, strips the `/workspace` prefix from the logical path, appends the remaining relative path to the host workspace folder, and returns the resulting host path.

**Call relations**: `LocalCarrier.write` uses this before writing files into the workspace, and `LocalCarrier.export` uses it before reading files out for blob storage. It depends on `_root` to locate the base workspace directory.

*Call graph*: calls 1 internal fn (_root); called by 2 (export, write); 1 external calls (PurePosixPath).


### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox lifecycle and command execution`

This file is the Docker “carrier” for sandboxes. A carrier is the part of the system that provides a safe place to run commands for an agent. Here, that safe place is a Docker container, which is like a disposable mini-computer. Each conversation gets a container named from its conversation ID, so later turns can reconnect to the same workspace if the container is still alive.

The file’s main job is to keep container work isolated while still letting it use a durable workspace. For local development, that workspace can be a folder mounted from the host. For cloud-style use, it can be mounted from S3, an object storage service, through s3fs, which makes S3 look like a normal folder.

A key safety detail is internet access. Commands inside the container do not get raw API keys. Instead, every command is run with temporary proxy settings for that specific turn. The proxy sees the run token, decides what outbound requests are allowed, and swaps a harmless placeholder key for the real key only outside the sandbox. This prevents an old container from accidentally reusing a previous turn’s credentials.

Without this file, the Docker sandbox backend could not create containers, execute agent commands in them, mount workspaces, install the proxy certificate, or clean up after a conversation.

#### Function details

##### `_docker`  (lines 63–77)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line tool asynchronously and captures its exit code, standard output, and error output. It is the shared doorway through which the rest of this file talks to Docker.

**Data flow**: It receives Docker command arguments, optional bytes to send as standard input, and a timeout. It starts a Docker subprocess, waits for it to finish, and returns a three-part result: numeric exit code, output bytes, and error bytes. If the command takes too long, it kills the process and returns a timeout-style failure.

**Call relations**: Most DockerCarrier methods rely on this helper whenever they need Docker to do something: list containers, run a new one, execute commands inside it, create networks, remove resources, or install files. It wraps asyncio’s subprocess tools so callers can stay focused on sandbox steps rather than process plumbing.

*Call graph*: called by 9 (_ensure_network, _install_ca, _mount_healthy, _mount_s3, _running_id, create, destroy, exec, write); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 84–147)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a Docker container for a conversation, or reconnects to the existing running one. It also prepares the container so commands inside it use the correct per-turn proxy settings and workspace mount.

**Data flow**: It receives a SandboxSpec containing the conversation ID, image name, proxy details, environment variables, and workspace mount information. It builds the container name and the proxy environment for this turn, checks whether the container is already running, and either returns a handle to it or creates a fresh container and network. After creation, it installs the proxy certificate and mounts S3 storage if needed. The result is a SandboxHandle that later methods use to run commands and find the workspace.

**Call relations**: This is the main setup path for the Docker carrier. It asks _running_id whether reuse is possible, uses _network_name and _ensure_network when a new container is needed, calls _install_ca so HTTPS traffic can trust the proxy, calls _credential_url and _mount_s3 for S3 workspaces, and uses _docker for the actual Docker run and cleanup commands.

*Call graph*: calls 7 internal fn (_credential_url, _ensure_network, _install_ca, _mount_s3, _network_name, _running_id, _docker); 1 external calls (__init__).


##### `DockerCarrier.exec`  (lines 149–162)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside an existing sandbox container. It makes sure that this command gets the current turn’s proxy and API-key placeholder environment, rather than any old values baked into the container.

**Data flow**: It receives a SandboxHandle, a command as a tuple of strings, and a timeout. It turns the handle’s environment values into Docker --env arguments, runs docker exec inside the target container, decodes the output bytes into text, and returns an ExecResult with stdout, stderr, and the exit code.

**Call relations**: After create returns a handle, higher-level sandbox code can call this method for each agent command. Internally it hands the actual Docker work to _docker and wraps the result in the standard ExecResult shape expected by the rest of the system.

*Call graph*: calls 1 internal fn (_docker); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 164–180)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file inside the sandbox container. It streams the file content through standard input, so the content does not have to be placed on the shell command line.

**Data flow**: It receives a SandboxHandle, a destination path inside the container, and the bytes to write. It runs a shell command in the container that creates the parent directory and copies standard input into the target file. If Docker reports failure, it raises an OSError; otherwise there is no return value.

**Call relations**: This is used when the system needs to place a file into the sandbox before or during work. It delegates the container execution to _docker, using docker exec -i so the bytes can flow safely through standard input.

*Call graph*: calls 1 internal fn (_docker).


##### `DockerCarrier.export`  (lines 182–199)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Copies a file produced in the sandbox workspace into the project’s blob store, which is where larger outputs can be kept. It is designed so the host process does not need to load the whole file into memory.

**Data flow**: It receives a SandboxHandle, a path under the workspace, a BlobStore, and the destination key. It checks what kind of workspace mount is in use. For an S3 mount, it asks the blob store to copy from the workspace prefix to the destination key inside storage. For a filesystem mount, it points the blob store at the file on the host-mounted folder. It raises an error if there is no supported workspace mount.

**Call relations**: This method is called after sandbox work has produced a file that needs to be saved outside the container. It does not call Docker; instead it relies on the mount information from the handle and hands the transfer to BlobStore.copy or BlobStore.put_file.

*Call graph*: calls 2 internal fn (copy, put_file); 2 external calls (Path, PurePosixPath).


##### `DockerCarrier._mount_s3`  (lines 201–267)

```
async def _mount_s3(self, handle: SandboxHandle, mount: MountSpec, credential_url: str) -> None
```

**Purpose**: Mounts an S3-backed workspace inside the container at /workspace when the sandbox uses S3 storage. Mounting means making remote object storage appear like a normal folder inside the container.

**Data flow**: It receives a SandboxHandle, a MountSpec, and a URL where the container can fetch scoped storage credentials. If the mount is not S3, it does nothing. For S3, it verifies all required mount details exist, writes a private credential token into the container as root, checks whether the mount already works, and if not prepares and runs the s3fs mount commands. It finishes by checking that the mounted workspace is healthy, or raises an error if any step fails.

**Call relations**: DockerCarrier.create calls this after either attaching to an existing container or starting a new one. This method uses _mount_healthy to avoid remounting an already-good workspace, uses sandbox helper functions to build the shell commands, and uses _docker to run those commands inside the container.

*Call graph*: calls 2 internal fn (_mount_healthy, _docker); called by 1 (create); 5 external calls (quote, install_token_command, mount_scripts, prepare_token_staging_command, s3fs_command).


##### `DockerCarrier._credential_url`  (lines 269–280)

```
def _credential_url(self, spec: SandboxSpec) -> str
```

**Purpose**: Builds the URL that the in-container S3 filesystem helper uses to fetch temporary storage credentials. It enforces HTTPS when a public proxy URL is configured, so credentials are encrypted while traveling over the network.

**Data flow**: It receives a SandboxSpec and reads its proxy settings. If a public proxy URL is present, it validates that it is HTTPS and has a hostname, then appends the credential endpoint path. If no public URL is set, it builds an internal URL using host.docker.internal and the local proxy port. The output is a credential URL string.

**Call relations**: DockerCarrier.create calls this before _mount_s3 so the S3 mount code knows where the container should request its storage credentials. It uses urlsplit to inspect public URLs and the shared SANDBOX_FS_CREDENTIAL_PATH constant for the endpoint path.

*Call graph*: called by 1 (create); 2 external calls (rstrip, urlsplit).


##### `DockerCarrier._mount_healthy`  (lines 282–294)

```
async def _mount_healthy(self, handle: SandboxHandle) -> bool
```

**Purpose**: Checks whether the workspace mount inside the container is working. This avoids doing risky or unnecessary remount work when an existing container already has a good mount.

**Data flow**: It receives a SandboxHandle. It runs a health-check shell command as root inside the container, aimed at the workspace directory. If the command exits successfully, it returns true; otherwise it returns false.

**Call relations**: DockerCarrier._mount_s3 calls this before and after mounting. It gets the actual health-check command from the sandbox helper mount_health_check and runs it through _docker.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_mount_s3); 1 external calls (mount_health_check).


##### `DockerCarrier.destroy`  (lines 296–302)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Removes the Docker container and network for a conversation. It uses the stable conversation-based name, so cleanup can work even if the caller does not have the latest container ID.

**Data flow**: It receives a SandboxHandle and reads the conversation ID. It asks Docker to force-remove the container with that conversation’s name, then removes the matching per-conversation network. It does not return anything and treats Docker’s remove operations as cleanup attempts.

**Call relations**: This is the teardown path for the Docker carrier. It uses _network_name to rebuild the network name from the conversation ID and _docker to run the Docker removal commands.

*Call graph*: calls 2 internal fn (_network_name, _docker).


##### `DockerCarrier.host`  (lines 304–311)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Reports that this Docker carrier cannot expose a service running inside the sandbox as an externally reachable host. For example, a web server started inside this Docker sandbox cannot be reached through this method.

**Data flow**: It receives a SandboxHandle and a port number, but does not use them to create a route. Instead it immediately raises a RuntimeError explaining that this carrier does not publish per-port hosts.

**Call relations**: Higher-level code may call this when it wants to access an in-sandbox service. For the Docker carrier, the story ends here with a clear error; deployments that need this behavior must use another carrier, such as the remote e2b carrier mentioned in the message.


##### `DockerCarrier._running_id`  (lines 313–324)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Checks whether a named Docker container is currently running and returns its container ID if it is. It distinguishes between “not found” and “Docker itself failed,” which makes errors clearer.

**Data flow**: It receives a container name. It runs docker ps with filters for the exact name and running status. If Docker reports an error, it raises a RuntimeError. If Docker succeeds, it returns the found container ID text, or None when no matching running container exists.

**Call relations**: DockerCarrier.create calls this at the beginning to decide whether it can attach to an existing container or must create a new one. The Docker query itself is performed through _docker.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._network_name`  (lines 326–327)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Docker network name for a conversation. The name combines the carrier’s base network name with the conversation ID, giving each conversation its own network namespace.

**Data flow**: It receives a conversation UUID and reads the carrier’s configured network prefix. It formats those into a deterministic string and returns that network name.

**Call relations**: DockerCarrier.create uses this when starting a new sandbox, and DockerCarrier.destroy uses it when cleaning up. It is a small naming helper that keeps setup and teardown using the same network name.

*Call graph*: called by 2 (create, destroy).


##### `DockerCarrier._ensure_network`  (lines 329–337)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure the Docker network for a sandbox exists before starting the container. A Docker network is like a private lane that controls how the container connects to other things.

**Data flow**: It receives a network name. It first asks Docker whether a network with that exact name already exists. If it does, it returns without changes. If it does not, it asks Docker to create it. Any Docker failure becomes a RuntimeError with the Docker error text.

**Call relations**: DockerCarrier.create calls this before docker run for a new container. It uses _docker both for the network lookup and for the network creation command.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._install_ca`  (lines 339–352)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the egress proxy’s certificate authority certificate inside the container. This lets HTTPS tools in the sandbox trust the proxy when it inspects and forwards allowed traffic.

**Data flow**: It receives a container ID and the certificate text. It streams the certificate into a standard trusted-certificate location inside the container as root, then runs update-ca-certificates. If that command fails, it raises a RuntimeError.

**Call relations**: DockerCarrier.create calls this right after starting a new container and before returning the sandbox handle. It uses _docker to run the root command inside the container and pass the certificate through standard input.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `manifest`  (lines 355–360)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO plugin system. It says that this file provides a carrier named docker and that DockerCarrier is the factory for creating it.

**Data flow**: It takes no input. It creates a Manifest object containing the extension name, version, and one CarrierSpec that points at DockerCarrier. The Manifest is returned to whoever is loading extensions.

**Call relations**: The extension loader calls this function to discover what the file contributes. It hands back standard manifest and carrier-spec objects rather than doing any Docker work itself.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox lifecycle and request handling`

A sandbox is the safe workspace where a conversation can run commands, write files, and start services without touching the host machine directly. This file is the adapter that teaches the rest of the system how to use E2B for that job. Without it, choosing `[sandbox] backend = "e2b"` would have no effect because the core system would not know how to create or control an E2B sandbox.

The main class, `E2BCarrier`, acts like a remote-control handset for one sandbox per conversation. When a turn starts, it either reconnects to an existing sandbox or creates a fresh one from a known E2B template. It then installs the project’s proxy certificate, mounts the conversation workspace from S3 at `/workspace`, and returns a handle that the rest of the system can use.

Because E2B sandboxes run outside the cluster, their internet traffic must go through a public egress proxy. Think of the proxy as a metered toll booth: each command is given proxy settings and a run token so requests can be attributed to the current turn, while real model API keys stay outside the sandbox. Commands, file uploads, artifact exports, public service hostnames, and shutdown all pass through this carrier. Destroying a sandbox pauses it rather than fully deleting it, so the next turn can resume cheaply.

#### Function details

##### `_egress_env`  (lines 83–115)

```
def _egress_env(proxy: ProxyEndpoint, run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make commands inside the remote sandbox send web traffic through the project’s egress proxy. This matters because the sandbox is outside the cluster, so it needs a public HTTPS proxy URL and a run token for safe, metered network access.

**Data flow**: It receives a proxy description and the current run token. It checks that the proxy has a public HTTPS URL, parses that URL, and creates proxy-related environment variables plus placeholder model API keys and certificate settings. It returns a dictionary that can be attached to sandbox commands; if the proxy is missing or unsafe, it raises an error before an open sandbox can be used.

**Call relations**: `E2BCarrier.create` calls this at the start of sandbox setup. The result is stored in the returned sandbox handle, and later `E2BCarrier.exec` uses that handle so every command runs with the correct network route.

*Call graph*: called by 1 (create); 1 external calls (urlsplit).


##### `E2BCommands.run`  (lines 128–136)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None) -> E2BCommandResult
```

**Purpose**: Describes the E2B SDK method used to run a shell command inside a sandbox. It is a protocol method, meaning this file uses it as a contract for what the real SDK object must provide.

**Data flow**: It takes a command string plus optional working directory, environment variables, user name, and timeout. The real E2B SDK runs that command in the sandbox and returns standard output, standard error, and an exit code, or raises an SDK exception for failures such as non-zero exits or timeouts.

**Call relations**: `E2BCarrier` relies on this contract during certificate installation, S3 mounting, health checks, and normal command execution. The method itself is supplied by the E2B SDK, not implemented in this file.


##### `E2BFiles.make_dir`  (lines 140–140)

```
async def make_dir(self, path: str, *, user: str | None=None) -> bool
```

**Purpose**: Describes the E2B SDK method used to create a directory inside the sandbox filesystem. This is needed when a newly created sandbox needs its workspace directory prepared.

**Data flow**: It receives a path and optional user name. The real SDK creates the directory in the sandbox and reports whether that succeeded.

**Call relations**: `E2BCarrier.create` uses this through the sandbox’s file interface when it creates a brand-new sandbox. The method is part of the expected SDK shape defined by the protocol.


##### `E2BFiles.write`  (lines 142–142)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: Describes the E2B SDK method used to write text or bytes into a file inside the sandbox. It is the safe path for sending raw file contents, because command execution only accepts a shell string.

**Data flow**: It receives a destination path, the data to write, and optionally the user to write as. The real SDK sends that data to the sandbox filesystem and returns whatever write result the SDK provides.

**Call relations**: `E2BCarrier._install_ca`, `E2BCarrier._mount_s3`, and `E2BCarrier.write` depend on this method to place certificates, tokens, or user-provided file content in the sandbox.


##### `E2BSandbox.pause`  (lines 151–151)

```
async def pause(self, **opts: object) -> bool
```

**Purpose**: Describes the E2B SDK method used to pause a sandbox instead of deleting it outright. Pausing preserves the sandbox cheaply so a later turn can resume it.

**Data flow**: It receives provider options, such as the API key. The real SDK asks E2B to pause the sandbox and returns whether that worked.

**Call relations**: `E2BCarrier.destroy` calls this when a conversation’s sandbox is being reclaimed. The method is supplied by the SDK object that matches the `E2BSandbox` protocol.


##### `E2BSandbox.get_host`  (lines 153–153)

```
def get_host(self, port: int) -> str
```

**Purpose**: Describes the E2B SDK method that turns an in-sandbox port into a public hostname. This is used when something running inside the sandbox, such as a web server or browser debugging endpoint, needs to be reached from outside.

**Data flow**: It receives a port number. The real SDK formats or returns the public hostname for that sandbox port.

**Call relations**: `E2BCarrier.host` calls this after finding the right sandbox. The SDK supplies the actual behavior.


##### `E2BSdk.create`  (lines 157–165)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the E2B SDK method used to create a new cloud sandbox from a template. The carrier uses this when there is no existing sandbox to resume for a conversation.

**Data flow**: It receives the template name, startup timeout, metadata, lifecycle settings, and API key. The real SDK creates the sandbox in E2B and returns an object that can run commands, write files, expose ports, and be paused.

**Call relations**: `E2BCarrier.create` calls this only when it cannot reuse a live or remembered sandbox. The method is part of the protocol so tests or alternate SDK-like objects can fit the same shape.


##### `E2BSdk.connect`  (lines 167–173)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the E2B SDK method used to reconnect to an existing sandbox by its ID. This is what lets a new process continue using a sandbox created by an earlier process.

**Data flow**: It receives a sandbox ID, timeout, and API key. The real SDK returns a sandbox object if E2B still has that sandbox, or raises an error if it cannot be found.

**Call relations**: `E2BCarrier.create`, `E2BCarrier.destroy`, and `E2BCarrier._sandbox` use this whenever the carrier has an ID but not a live in-memory sandbox object.


##### `E2BCarrier.create`  (lines 187–218)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Starts or resumes the E2B sandbox for a conversation and prepares it for work. It returns a `SandboxHandle`, which is the system’s ticket for later command runs, file writes, exports, hosting, and cleanup.

**Data flow**: It receives a sandbox specification containing the conversation ID, possible previous sandbox ID, proxy details, mount details, run token, and extra environment variables. It builds proxy environment settings, reconnects to an existing sandbox or creates a new one, records it in memory, installs the proxy certificate, mounts the S3 workspace if needed, and returns a handle containing the sandbox ID, mount information, traffic token, and final command environment.

**Call relations**: This is the main setup step the core sandbox system calls when it needs an E2B sandbox. It calls `_egress_env` for safe network settings, `_install_ca` so HTTPS proxying is trusted, and `_mount_s3` so `/workspace` points at the durable conversation workspace.

*Call graph*: calls 3 internal fn (_install_ca, _mount_s3, _egress_env); 3 external calls (__init__, cast, rstrip).


##### `E2BCarrier._install_ca`  (lines 220–228)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Installs the project’s proxy certificate inside the sandbox so commands can trust HTTPS traffic that passes through the egress proxy. Without this, tools in the sandbox could reject proxied secure connections.

**Data flow**: It receives a sandbox object and the certificate text. It writes the certificate to a staging path as root, then runs a root command that installs it into the system certificate location and refreshes the trusted certificate bundle. If the install command fails, it turns the SDK error into a clear runtime error with the command output.

**Call relations**: `E2BCarrier.create` calls this every time a sandbox is created or resumed. It uses the sandbox file API to place the certificate and the command API to install it.

*Call graph*: called by 1 (create).


##### `E2BCarrier._mount_s3`  (lines 230–274)

```
async def _mount_s3(self, sandbox: E2BSandbox, mount: MountSpec, credential_url: str) -> None
```

**Purpose**: Mounts the conversation’s S3-backed workspace at `/workspace` inside the sandbox. This makes files durable outside the disposable cloud sandbox, like keeping the real notebook in a locker while using a temporary desk.

**Data flow**: It receives a sandbox, a mount description, and a credential endpoint URL. If the mount is not S3, it does nothing. For S3, it checks that all required bucket, prefix, token, endpoint, and region values are present, stages and installs a private credential token, checks whether the mount is already healthy, and if not builds and runs the preparation and mount commands. Afterward it checks health again and raises a clear error if the mount failed.

**Call relations**: `E2BCarrier.create` calls this after installing the certificate. It calls `_mount_healthy` before and after mounting, and uses helper commands from the sandbox SDK layer to prepare credentials, build the `s3fs` command, and run mount scripts.

*Call graph*: calls 1 internal fn (_mount_healthy); called by 1 (create); 4 external calls (install_token_command, mount_scripts, prepare_token_staging_command, s3fs_command).


##### `E2BCarrier._mount_healthy`  (lines 276–285)

```
async def _mount_healthy(self, sandbox: E2BSandbox) -> bool
```

**Purpose**: Checks whether the `/workspace` mount is working. It prevents unnecessary remounting and catches broken storage setup early.

**Data flow**: It receives a sandbox object. It runs a small health-check command as root with a short timeout. If the command succeeds, it returns `true`; if the command exits badly or times out, it returns `false` instead of raising.

**Call relations**: `E2BCarrier._mount_s3` calls this before attempting a mount and again after mounting. The health-check command itself comes from the shared sandbox helpers.

*Call graph*: called by 1 (_mount_s3); 1 external calls (mount_health_check).


##### `E2BCarrier.exec`  (lines 287–309)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command inside the E2B sandbox and translates the result into the project’s standard `ExecResult`. It is the normal path for tools or agents to execute work in the remote workspace.

**Data flow**: It receives a sandbox handle, an argument list, and a timeout. It finds or reconnects the sandbox, quotes the argument list into a shell command string, and runs it in `/workspace` with Node/Playwright settings plus the saved egress proxy environment. It returns stdout, stderr, and an exit code; non-zero command exits and SDK timeouts are converted into ordinary execution results.

**Call relations**: The core sandbox layer calls this when it wants to run a command. It calls `_sandbox` to obtain the live SDK sandbox and uses `shlex.join` so the argument list becomes one shell-safe command string for E2B.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, join).


##### `E2BCarrier.write`  (lines 311–316)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Uploads bytes directly into a file in the sandbox. This is used when content should be written as data, not squeezed through a shell command line.

**Data flow**: It receives a sandbox handle, destination path, and byte content. It finds or reconnects the sandbox, then asks the sandbox file API to write the bytes to that path. It returns nothing after the write completes.

**Call relations**: The wider sandbox system calls this when it needs to place a file in the sandbox. It calls `_sandbox` first, then hands the actual byte transfer to the E2B files API.

*Call graph*: calls 1 internal fn (_sandbox).


##### `E2BCarrier.export`  (lines 318–330)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Copies a produced workspace file into the artifact store without downloading it through the remote sandbox. This is efficient because the source workspace and destination artifact store are both in S3.

**Data flow**: It receives a sandbox handle, a workspace path, a blob store, and a destination key. It checks that the handle has an S3 workspace mount, turns the workspace path into a path relative to `/workspace`, builds the source S3 object key from the workspace prefix, and asks the blob store to copy that object to the artifact key.

**Call relations**: The artifact-sharing flow calls this after a file has been produced in the workspace. It does not need `_sandbox` because the copy happens server-side in storage through `BlobStore.copy`.

*Call graph*: calls 1 internal fn (copy); 1 external calls (PurePosixPath).


##### `E2BCarrier.host`  (lines 332–339)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Returns the public hostname for a service running on a port inside the E2B sandbox. This lets outside code reach things like a preview server or browser debugging endpoint started by a command.

**Data flow**: It receives a sandbox handle and port number. It finds or reconnects the sandbox, asks E2B for the public host for that port, and returns the host string.

**Call relations**: The core system calls this when it needs an externally reachable address for an in-sandbox service. It calls `_sandbox` to get the SDK object, then delegates the address formatting to `E2BSandbox.get_host`.

*Call graph*: calls 1 internal fn (_sandbox).


##### `E2BCarrier.destroy`  (lines 341–358)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Pauses a conversation’s E2B sandbox and removes it from this process’s live cache. It is cleanup, but it preserves the sandbox state when possible so later work can resume cheaply.

**Data flow**: It receives a sandbox handle. It first removes any live sandbox object for that conversation from memory. If this process does not have one but the handle has a container ID, it reconnects to that sandbox. If E2B says the sandbox no longer exists, it quietly returns. If it has a sandbox object, it asks E2B to pause it.

**Call relations**: The reaper or sandbox lifecycle code calls this when a conversation becomes idle or needs cleanup. It may use the stored sandbox ID to reclaim a sandbox created by another process, then hands the actual pause operation to the E2B SDK.


##### `E2BCarrier._sandbox`  (lines 360–368)

```
async def _sandbox(self, handle: SandboxHandle) -> E2BSandbox
```

**Purpose**: Finds the active SDK sandbox object for a handle, reconnecting to E2B if this process does not already have it cached. This keeps later operations simple because they can work with a live sandbox object.

**Data flow**: It receives a sandbox handle. It first looks in the in-memory cache by conversation ID. If found, it returns that sandbox. Otherwise it connects to E2B using the handle’s container ID, stores the result in the cache, and returns it.

**Call relations**: `E2BCarrier.exec`, `E2BCarrier.write`, and `E2BCarrier.host` call this before doing their work. It is the small bridge between the durable handle and the process-local SDK object.

*Call graph*: called by 3 (exec, host, write).


##### `build_e2b_carrier`  (lines 371–380)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: Constructs the E2B carrier selected by configuration. It fails at startup if the required E2B API key is missing, which is better than failing later on the first sandbox request.

**Data flow**: It reads the `E2B_API_KEY` environment variable. If the key is absent, it raises an error. If present, it creates and returns an `E2BCarrier` configured with that key and the standard E2B template name.

**Call relations**: The manifest registers this as the factory for the `e2b` sandbox backend. The server calls it when configuration chooses E2B.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 383–388)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO plugin system. It says there is a carrier named `e2b` and tells the system how to build it.

**Data flow**: It takes no input. It creates a manifest object containing the extension name, version, and one carrier specification that points to `build_e2b_carrier` and marks the carrier as off-cluster. It returns that manifest to the loader.

**Call relations**: The extension loader calls this to discover what the file provides. The returned carrier spec lets the core system select this E2B implementation without hard-coding E2B into the core code.

*Call graph*: 2 external calls (__init__, __init__).


### Egress proxy
Runs the shared outbound proxy, evaluates per-workspace network rules, injects or forwards credentials, and records access for audit and billing.

### `core/src/ufo/sandbox/proxy/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python projects, an `__init__.py` file tells Python that a directory should be treated as an importable package. That means code elsewhere can refer to this folder using names like `ufo.sandbox.proxy` and then import the real implementation files inside it. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself is only there so people and the system know the drawer exists and how to find it. If this file were missing in environments that rely on traditional Python packages, imports from this folder could fail or behave differently. Because the file is empty, it has no startup work, no settings, and no side effects beyond making the package structure explicit.


### `core/src/ufo/sandbox/proxy/rules.py`

`domain_logic` · `sandbox turn setup / egress proxy rule derivation`

A sandbox should not be able to freely call any website or see raw account secrets. This file builds the rule list that the egress proxy reads before traffic leaves the sandbox. Think of it like writing a temporary guest pass: it says which doors may be opened, whether a hidden key must be added at the door, and whether each visit should be counted.

The rules are small value objects. A ScopeRule says which exact host names are allowed. An InjectionRule says: if the sandbox sends a placeholder value, replace it on the wire with the real secret, so the secret never lives inside the sandbox. A MeterRule says traffic to a host should be counted under a spending dimension. An InternetRule allows broader public internet access for live turns that need it. A ForwardRule says a request should not be sent directly; instead, the broker executes it using a grant, so the credential stays server-side.

The file derives these rules from several sources. The selected AI model opens the right provider host, such as OpenAI or Anthropic, and injects the model key. Extension manifests may allow live internet. Credential slots open only the hosts for credentials that actually exist and are valid for the workspace. Grants allow connector provider hosts and file-transfer hosts. CLI credentials can be forwarded through the broker when the acting member is allowed to use the grant.

#### Function details

##### `provider_host`  (lines 91–95)

```
def provider_host(model: str) -> str
```

**Purpose**: Finds which provider host should be used for a model name. For example, model names starting with OpenAI-style prefixes map to the OpenAI API host, while Claude-style names map to Anthropic.

**Data flow**: It receives a model name as text, checks it against known prefixes, and returns the matching API host. If no prefix matches, it raises an error instead of guessing, because allowing the wrong host would be unsafe.

**Call relations**: derive_model_rules calls this first when building rules for the selected model. Its answer decides which provider-specific authentication header and network allowlist entry are created next.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 98–113)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: Builds the proxy rules needed for the sandbox to call the deploy's chosen AI model provider. It allows the provider host, replaces the sandbox's placeholder model key with the real key, and marks that provider traffic for token metering.

**Data flow**: It receives a model name and the real model API key. It asks provider_host which API host belongs to the model, chooses the correct authentication header format for that host, then returns a small rule bundle: allow that host, inject the real key where the sentinel placeholder appears, and meter requests as token usage.

**Call relations**: This function uses provider_host to identify the provider, then creates ScopeRule, InjectionRule, and MeterRule values. Those rules are later read by the egress proxy so model calls can work without exposing the real key inside the sandbox.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 116–118)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: Checks whether any extension manifest says the sandbox needs public internet during live turns. If so, it creates the rule that permits that broader internet access.

**Data flow**: It receives the deploy's manifests and looks for any manifest with sandbox internet enabled. If one exists, it returns an InternetRule; otherwise it returns an empty result, meaning no extra public internet permission is added.

**Call relations**: This function creates InternetRule only when the manifests require it. It is part of the broader rule-building step that combines model, credential, grant, and manifest permissions before the proxy starts enforcing them.

*Call graph*: 1 external calls (__init__).


##### `derive_credential_rules`  (lines 121–173)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: Builds rules for declared credential slots, such as API keys that an extension may need. It only opens network access when a real credential can be found or minted for the current workspace and its declared host is available.

**Data flow**: It receives credential slot declarations, a workspace ID, and a credential store. For each slot that asks for network injection, it tries to get the real secret. If minting fails, it logs a warning and skips that slot. If no secret or no valid host is available, it also skips it. When a usable secret and host exist, it creates an InjectionRule for that slot. If the target needs HTTP Basic authentication for git, it combines the user name and secret into the expected encoded header value. Finally, it groups rules by host so each host is allowed and metered once, even if several headers are injected for it.

**Call relations**: This function calls slot_secret to fetch or mint the secret, credential_host to resolve the actual allowed host, b64encode when a git-style Basic header must be built, and warn when a credential cannot be used. It then creates InjectionRule, ScopeRule, and MeterRule values that the proxy will enforce during sandbox traffic.

*Call graph*: 7 external calls (__init__, __init__, __init__, b64encode, credential_host, slot_secret, warn).


##### `derive_grant_rules`  (lines 176–193)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: Builds network rules for active connector grants. A grant allows traffic to the connector provider's host and, when needed, to broker file-store hosts used for uploading inputs or downloading outputs.

**Data flow**: It receives grants and optionally a ConnectorTransferHosts lookup. For each grant, it gathers the grant's provider host plus any extra transfer hosts for that provider, removes blanks and duplicates, then returns rules that allow those hosts and meter each host under request counting.

**Call relations**: When transfer host information is supplied, this function asks ConnectorTransferHosts.of for the provider's file-store hosts. It then creates ScopeRule and MeterRule values. Unlike credential rules, it does not create injection rules, because the broker keeps the account token and performs granted connector work server-side.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 196–216)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

**Purpose**: Builds forwarding rules for connector CLI credentials. These rules let certain sentinel-bearing requests go through the broker, but only when the acting member is allowed to use the grant.

**Data flow**: It receives active grants, the acting member ID if there is one, and a mapping of connector providers to CLI credential definitions. For each grant with a matching CLI credential, it checks access: shared grants are allowed, and private grants are allowed only for their grantor. Allowed grants become ForwardRule values containing the target host, header, sentinel, account ID, and forwarding callback.

**Call relations**: This function calls grant_sentinel to compute the placeholder value expected in the request, then creates ForwardRule values. Those rules tell the proxy to hand matching requests to the broker's forwarder instead of sending them directly upstream.

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `ConnectorTransferHosts.of`  (lines 230–231)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Looks up which broker file-transfer hosts should be allowed for a connector provider. It uses provider-specific hosts when known, otherwise it falls back to the open connector namespace defaults.

**Data flow**: It receives a provider name. It checks the explicit provider-to-hosts mapping; if that provider is present, it returns those hosts, even if the list is empty. If the provider is not present, it returns the default host list.

**Call relations**: derive_grant_rules uses this lookup while building grant-based network permissions. This keeps the grant logic simple: it asks for extra hosts for a provider, then adds them to the same allow-and-meter rule bundle.


##### `connector_transfer_hosts`  (lines 234–245)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: Builds the lookup table that says which file-transfer hosts belong to which connector providers. This matters because connector tools may need broker storage hosts for passing files in and out of the sandbox.

**Data flow**: It receives the deploy's manifests. It scans registered connectors and records each connector provider's declared transfer hosts. Then it asks open_connector_namespace for the namespace-wide default transfer hosts, if there is one. It returns a ConnectorTransferHosts object containing both the explicit provider mapping and the fallback defaults.

**Call relations**: This function calls open_connector_namespace to find default transfer-host settings, then creates ConnectorTransferHosts. The resulting object is later used by derive_grant_rules to decide which extra broker file-store hosts a granted connector should admit.

*Call graph*: 2 external calls (__init__, open_connector_namespace).


### `core/src/ufo/sandbox/proxy/server.py`

`io_transport` · `main loop and request handling`

A sandboxed agent is not allowed to talk directly to the internet. Instead, its web traffic goes through this proxy, like a guarded front desk for outgoing requests. The proxy reads a signed run token from each request, checks that the turn is still running, and builds the exact set of rules for that agent and workspace. If a request is not allowed, it is refused by default.

For ordinary allowed hosts, the proxy can simply open a tunnel and pass encrypted bytes through without looking inside. For hosts that need a secret, it performs controlled TLS interception: it presents a temporary certificate trusted by the sandbox, reads the HTTP request, replaces only the exact placeholder token with the real credential, and then sends the request onward over verified TLS. Some grant-backed requests are not sent directly at all; they are sent through a broker so the credential never enters the sandbox or proxy request stream.

The same listener also answers a special local credential endpoint used by workspace file mounts, returning narrowly scoped, short-lived storage credentials. Along the way, the proxy meters allowed egress and, for model APIs, parses streamed or JSON responses to count tokens. Without this file, sandbox agents would either have unsafe broad internet access or no practical way to call approved services.

#### Function details

##### `_ContentDecoder.unconsumed_tail`  (lines 146–146)

```
def unconsumed_tail(self) -> bytes
```

**Purpose**: This protocol property describes the unread compressed bytes left inside a decompressor. It lets the token parser work with gzip or deflate objects without depending on one concrete class.

**Data flow**: A decompressor object has internal leftover bytes after a partial decode. This property exposes those bytes so the caller can continue decoding from the right place. It does not change anything by itself.

**Call relations**: HttpTokenUsage uses objects shaped like _ContentDecoder while decoding compressed model responses. This property is part of that expected shape.


##### `_ContentDecoder.decompress`  (lines 148–148)

```
def decompress(self, data: bytes, max_length: int=0) -> bytes
```

**Purpose**: This protocol method describes how a decompressor turns compressed response bytes into plain bytes. It exists so the response parser can treat gzip and deflate decoders the same way.

**Data flow**: Compressed bytes and an optional output limit go in. The decompressor returns decoded bytes and may keep some unread input internally. The parser then feeds the decoded result into its body reader.

**Call relations**: HttpTokenUsage._decode calls this behavior through the protocol when a model response is compressed.


##### `_ContentDecoder.flush`  (lines 150–150)

```
def flush(self) -> bytes
```

**Purpose**: This protocol method describes how to ask a decompressor for any final decoded bytes at the end of a response. It prevents token usage from being missed in compressed response tails.

**Data flow**: The decompressor's current state goes in implicitly. It returns remaining decoded bytes, if any. The parser then treats those bytes as the end of the body.

**Call relations**: HttpTokenUsage._finish_decoder uses this behavior when usage() is requested or when a chunked response ends.


##### `generate_ca`  (lines 156–179)

```
async def generate_ca() -> tuple[str, str]
```

**Purpose**: This creates a temporary certificate authority, meaning a root certificate and key that this proxy can use to sign fake per-host certificates for safe TLS interception inside the sandbox.

**Data flow**: No project data goes in. It creates temporary files, asks the openssl command-line tool to make a self-signed certificate, reads the certificate and key text back, and returns them.

**Call relations**: Startup code can call this before constructing EgressProxy. It relies on _openssl to run the external openssl command.

*Call graph*: calls 1 internal fn (_openssl); 2 external calls (Path, TemporaryDirectory).


##### `_openssl`  (lines 182–188)

```
async def _openssl(*argv: str) -> None
```

**Purpose**: This is the small wrapper for running the openssl command-line program. The proxy uses it instead of a Python cryptography library to create certificates and keys.

**Data flow**: OpenSSL arguments go in. The function starts the openssl process, waits for it to finish, ignores normal output, and raises an error if openssl reports failure. It returns nothing on success.

**Call relations**: generate_ca uses it to make the root certificate. EgressProxy.start uses it to make a reusable leaf key. EgressProxy._leaf_context uses it to create per-host certificates.

*Call graph*: called by 3 (_leaf_context, start, generate_ca); 1 external calls (create_subprocess_exec).


##### `PerAgentRules.resolve`  (lines 214–234)

```
async def resolve(self, run: RunToken | None) -> tuple[Rule, ...]
```

**Purpose**: This builds the exact network rule list for one run token. It combines base rules, optional internet access, workspace credentials, OAuth grants, and CLI credentials, but only for the agent named by the token.

**Data flow**: A run token, or no token, goes in. The function looks up the turn's agent and acting member, reads relevant stores for credentials and grants, and returns a tuple of rules. If the token is missing or the turn is unknown, it returns only the base rules.

**Call relations**: EgressProxy calls this through its configured rule resolver when it needs to decide whether a CONNECT request is allowed. It asks _turn_of for the database facts and hands grant and credential data to rule-derivation helpers.

*Call graph*: calls 1 internal fn (_turn_of); 3 external calls (derive_cli_rules, derive_credential_rules, derive_grant_rules).


##### `PerAgentRules._turn_of`  (lines 236–268)

```
async def _turn_of(self, run: RunToken) -> tuple[UUID, UUID | None, bool] | None
```

**Purpose**: This looks up which agent and human member a run token belongs to, and whether that agent is allowed broad internet access.

**Data flow**: A run token goes in. The function reads the turn and agent rows from the workspace database, chooses the acting member from speaker or on-behalf-of fields, and returns agent id, member id, and internet permission. If no row matches, it returns nothing.

**Call relations**: PerAgentRules.resolve calls this first so it can build rules for the correct agent and workspace rather than using someone else's permissions.

*Call graph*: called by 1 (resolve); 2 external calls (select, workspace_tx).


##### `PerAgentRules.turn_live`  (lines 270–286)

```
async def turn_live(self, run: RunToken) -> bool
```

**Purpose**: This checks whether the turn named by a run token is still running. It is the freshness check that prevents an old token from continuing to use credentials after a turn ends.

**Data flow**: A run token goes in. The function reads the turn status from the database and returns true only if the status is RUNNING. It changes no stored data.

**Call relations**: EgressProxy receives this as its authorizer callback. _handle calls the authorizer before allowing any outbound connection.

*Call graph*: 2 external calls (select, workspace_tx).


##### `EgressProxy.start`  (lines 315–331)

```
async def start(self, bind_host: str=PROXY_BIND_HOST, port: int=0, public_url: str | None=None) -> ProxyEndpoint
```

**Purpose**: This starts the proxy listener and prepares the certificate files needed for TLS interception. It returns the connection details that sandboxes need to use the proxy.

**Data flow**: A bind host, port, and optional public URL go in. The function creates a temporary working directory, writes the CA files, generates a leaf key, starts an asyncio TCP server, and returns a ProxyEndpoint containing the bound port and CA certificate.

**Call relations**: System startup calls this to make the proxy available. Incoming connections are then dispatched to EgressProxy._handle.

*Call graph*: calls 1 internal fn (_openssl); 4 external calls (__init__, start_server, Path, TemporaryDirectory).


##### `EgressProxy.stop`  (lines 333–361)

```
async def stop(self, graceful_shutdown_seconds: int=0) -> None
```

**Purpose**: This shuts the proxy down in a bounded way. It stops accepting new clients, gives current connections a chance to finish, cancels lingering work, flushes metering, and removes temporary files.

**Data flow**: A grace period goes in. The function closes the server, waits for tracked connection tasks, cancels unfinished ones, stops the meter worker with a sentinel value, and cleans the working directory. It returns nothing.

**Call relations**: Shutdown code calls this. It coordinates with tasks created by _handle and with the background metering loop started by _enqueue_meter.

*Call graph*: 2 external calls (gather, wait).


##### `EgressProxy._handle`  (lines 363–463)

```
async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None
```

**Purpose**: This is the per-connection traffic cop. It reads the client's first HTTP request, decides whether it is a workspace credential fetch or a proxy CONNECT, checks authorization and capacity, then chooses tunneling or inspected forwarding.

**Data flow**: A client reader and writer go in. The function reads headers, extracts the method, target, and proxy authorization token, checks limits and permissions, resolves rules, may resolve public DNS, and either writes an error response or passes the connection to _serve_workspace_credentials, _tunnel, or _mitm. It also updates active connection counters.

**Call relations**: asyncio.start_server calls this for every accepted TCP connection. It is the central dispatcher that hands specialized work to the rest of EgressProxy.

*Call graph*: calls 7 internal fn (_mitm, _rules_for, _run_token, _serve_workspace_credentials, _tunnel, _read_request_head, _respond); 3 external calls (__init__, close, current_task).


##### `EgressProxy._serve_workspace_credentials`  (lines 465–492)

```
async def _serve_workspace_credentials(self, writer: asyncio.StreamWriter, target: str) -> None
```

**Purpose**: This serves the special local endpoint used by sandbox file mounts to fetch short-lived storage credentials. It refuses unknown paths or bad tokens.

**Data flow**: A response writer and requested path go in. The function checks the configured credential provider and path, extracts the path token, asks for SandboxFsCredentials, and writes JSON credentials or an HTTP error. It logs unexpected minting failures.

**Call relations**: EgressProxy._handle calls this when the request method is GET. It uses _respond for failures and the configured workspace_credentials callback for the actual credential minting.

*Call graph*: calls 1 internal fn (_respond); called by 1 (_handle); 3 external calls (drain, write, log).


##### `EgressProxy._rules_for`  (lines 494–509)

```
async def _rules_for(self, run: RunToken | None) -> tuple[Rule, ...]
```

**Purpose**: This returns the rule list for a run token, using a short-lived cache so repeated requests do not constantly reload grants and credentials.

**Data flow**: A run token, or no token, goes in. For no token it directly asks the resolver. For a token it returns a fresh cached result, starts or joins an in-progress resolution task, and returns the resolved rules.

**Call relations**: EgressProxy._handle calls this after authorizing a CONNECT. It delegates cache misses to _resolve_rules and shares concurrent misses with asyncio.shield.

*Call graph*: calls 1 internal fn (_resolve_rules); called by 1 (_handle); 3 external calls (create_task, shield, monotonic).


##### `EgressProxy._resolve_rules`  (lines 511–531)

```
async def _resolve_rules(self, run: RunToken) -> tuple[Rule, ...]
```

**Purpose**: This performs the actual rule lookup and stores the result in the cache. If lookup fails, it fails closed by falling back to tokenless base rules rather than allowing more access.

**Data flow**: A run token goes in. The function calls the configured resolver, logs failures, stores successful rules with an expiry time, evicts an old cache entry if needed, and returns the rules.

**Call relations**: _rules_for creates this as a task on cache misses. It removes its task marker when done so later requests can resolve again if needed.

*Call graph*: called by 1 (_rules_for); 4 external calls (__init__, current_task, monotonic, log).


##### `EgressProxy._run_token`  (lines 533–539)

```
def _run_token(self, proxy_auth: str) -> RunToken | None
```

**Purpose**: This decodes the Proxy-Authorization header into a trusted run token. Bad or missing authorization becomes no token, which the proxy will deny for egress.

**Data flow**: A header string goes in. The function asks the RunTokenCodec to parse it and returns a RunToken on success or None on missing or invalid input.

**Call relations**: EgressProxy._handle calls this before checking whether the turn is live and before loading per-agent rules.

*Call graph*: called by 1 (_handle).


##### `EgressProxy._resolve_public_address`  (lines 541–573)

```
async def _resolve_public_address(self, host: str, port: int) -> str
```

**Purpose**: This resolves a host for broad internet access while blocking private, local, multicast, IPv6, or otherwise non-global destinations. It helps prevent the sandbox from reaching internal infrastructure.

**Data flow**: A host and port go in. If the host is already an IPv4 address, it checks it directly; otherwise it performs an async DNS A-record lookup. It returns the first global IPv4 address or raises an error that leads to refusal.

**Call relations**: EgressProxy._handle uses this when a host is not exactly allowed by scoped rules but internet access is enabled. Tests or deployments can replace it through resolve_public.

*Call graph*: 1 external calls (IPv4Address).


##### `EgressProxy._tunnel`  (lines 575–602)

```
async def _tunnel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, run: RunToken, rules: tuple[Rule, ...], connect_host: str) -> None
```

**Purpose**: This opens an opaque CONNECT tunnel for an allowed host that does not need credential injection. The proxy cannot see the encrypted HTTP inside; it only relays bytes.

**Data flow**: Client streams, host details, run token, rules, and resolved connect host go in. The function opens the upstream TCP connection, sends a 200 CONNECT response, emits metrics and ledger work, then relays data both ways. If upstream cannot be reached, it writes a 502 response.

**Call relations**: EgressProxy._handle calls this when rules allow the host but there are no InjectionRule or ForwardRule entries. It uses _relay for byte copying and metering helpers for accounting.

*Call graph*: calls 4 internal fn (_meter, _meter_ledger, _relay, _respond); called by 1 (_handle); 4 external calls (drain, write, open_connection, wait_for).


##### `EgressProxy._mitm`  (lines 604–660)

```
async def _mitm(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, injections: list[InjectionRule], forwards: list[ForwardRule], run: RunToken, rules: tuple[Rule,
```

**Purpose**: This performs controlled TLS interception for allowed hosts where the proxy must inspect a request to inject a credential or route it through a broker.

**Data flow**: Client streams, host details, matching injection and forward rules, run token, and all rules go in. It creates or reuses a host certificate, upgrades the client side to TLS, reads one HTTP request, then either forwards through a broker, swaps a sentinel header for a real secret and sends upstream, or relays the response while optionally collecting token usage.

**Call relations**: EgressProxy._handle calls this for hosts with InjectionRule or ForwardRule entries. It calls _leaf_context, _start_tls_server, _forward_match, _forward_broker, _inject, _relay, and metering helpers.

*Call graph*: calls 11 internal fn (_forward_broker, _leaf_context, _meter, _meter_ledger, _meter_tokens, _forward_match, _inject, _read_request_head, _relay, _respond (+1 more)); called by 1 (_handle); 3 external calls (__init__, open_connection, wait_for).


##### `EgressProxy._forward_broker`  (lines 662–706)

```
async def _forward_broker(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, rule: ForwardRule, request: tuple[bytes, list[bytes]], host: str, run: RunToken, rules: tuple[
```

**Purpose**: This sends a sentinel-marked request through a grant broker instead of directly to the provider. That keeps the real account credential on the broker side.

**Data flow**: The client stream, matched ForwardRule, request head, host, run token, and rules go in. The function reads and bounds the whole request body, records metering, calls the broker with cleaned headers and URL, and writes the broker's reconstructed HTTP response back. If the body is unacceptable or broker fails, it writes an error.

**Call relations**: EgressProxy._mitm calls this when _forward_match finds a grant sentinel. It uses _read_request_body, _forward_headers, _forward_response_bytes, _respond, and _drain_refused_body.

*Call graph*: calls 7 internal fn (_meter, _meter_ledger, _drain_refused_body, _forward_headers, _forward_response_bytes, _read_request_body, _respond); called by 1 (_mitm); 3 external calls (drain, write, log).


##### `EgressProxy._leaf_context`  (lines 708–753)

```
async def _leaf_context(self, host: str) -> ssl.SSLContext
```

**Purpose**: This creates and caches a TLS server certificate for one upstream host. The sandbox trusts the proxy's CA, so this certificate lets the proxy read the request only for approved intercepted hosts.

**Data flow**: A host name goes in. The function checks the cache, writes certificate extension files, runs openssl to create and sign a certificate, loads it into an SSLContext, stores it, and returns it.

**Call relations**: EgressProxy._mitm calls this before starting TLS with the sandbox client. It uses _openssl and a lock so two requests do not mint the same host certificate at once.

*Call graph*: calls 1 internal fn (_openssl); called by 1 (_mitm); 2 external calls (Path, SSLContext).


##### `EgressProxy._meter`  (lines 755–758)

```
def _meter(self, host: str, rules: tuple[Rule, ...]) -> None
```

**Purpose**: This emits immediate in-process metrics for allowed egress. It is useful for observation even before database accounting is written.

**Data flow**: A host and rule list go in. For each MeterRule matching that host, it emits a sandbox_egress_total metric with the rule's dimension. It returns nothing.

**Call relations**: _tunnel, _mitm, and _forward_broker call this after an allowed connection or request is established.

*Call graph*: called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (emit_metric).


##### `EgressProxy._meter_ledger`  (lines 760–766)

```
async def _meter_ledger(self, host: str, run: RunToken, rules: tuple[Rule, ...]) -> None
```

**Purpose**: This queues durable accounting for non-token egress. It avoids writing to the database directly on the relay path.

**Data flow**: A host, run token, and rules go in. If a matching non-token MeterRule exists, it wraps the run in an _EgressMeter record and enqueues it. Otherwise it does nothing.

**Call relations**: _tunnel, _mitm, and _forward_broker call this next to metric emission. It hands work to _enqueue_meter.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (__init__).


##### `EgressProxy._meter_tokens`  (lines 768–774)

```
async def _meter_tokens(self, run: RunToken, accumulator: 'HttpTokenUsage') -> None
```

**Purpose**: This turns parsed model response usage into a queued token accounting record. It logs when the proxy expected usage but could not find it.

**Data flow**: A run token and HttpTokenUsage accumulator go in. The function asks the accumulator for model and usage numbers, then enqueues a _TokenMeter record if parsing succeeded.

**Call relations**: EgressProxy._mitm calls this after relaying a metered model response. It relies on _enqueue_meter for background database writing.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 1 (_mitm); 2 external calls (__init__, log).


##### `EgressProxy._enqueue_meter`  (lines 776–783)

```
async def _enqueue_meter(self, record: _MeterRecord) -> None
```

**Purpose**: This puts an accounting record onto the background metering queue, starting the worker if needed.

**Data flow**: An egress or token meter record goes in. The function ensures the meter loop task exists and is healthy, then places the record into the queue. The caller continues after the queue accepts it.

**Call relations**: _meter_ledger and _meter_tokens call this. It starts _meter_loop as the background consumer.

*Call graph*: calls 1 internal fn (_meter_loop); called by 2 (_meter_ledger, _meter_tokens); 1 external calls (create_task).


##### `EgressProxy._meter_loop`  (lines 785–817)

```
async def _meter_loop(self) -> None
```

**Purpose**: This background worker batches accounting records before writing them. Batching reduces database overhead during bursts of proxy traffic.

**Data flow**: Records arrive through the proxy's queue. The loop waits for a first item, briefly gathers more up to a limit, writes the batch, marks queue items done, and stops when it sees a None sentinel.

**Call relations**: _enqueue_meter starts this loop. It calls _write_meter_batch and is stopped by EgressProxy.stop.

*Call graph*: calls 1 internal fn (_write_meter_batch); called by 1 (_enqueue_meter); 2 external calls (sleep, log_error).


##### `EgressProxy._write_meter_batch`  (lines 819–862)

```
async def _write_meter_batch(self, records: list[_MeterRecord]) -> None
```

**Purpose**: This writes grouped egress and token usage records to the workspace ledger. It combines multiple records for the same run before touching the database.

**Data flow**: A list of meter records goes in. The function sums egress counts and token usage by run and model, opens each workspace context, writes accounting rows, and logs failures per run without crashing the worker.

**Call relations**: _meter_loop calls this for each batch. It hands final counts to record_egress_request and record_sandbox_tokens.

*Call graph*: called by 1 (_meter_loop); 6 external calls (__init__, record_egress_request, record_sandbox_tokens, workspace_tx, log_error, ws).


##### `_start_tls_server`  (lines 865–881)

```
async def _start_tls_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]
```

**Purpose**: This sends the CONNECT success response and upgrades the existing client connection into TLS, with the proxy acting as the server.

**Data flow**: The current reader, writer, and SSL context go in. It pauses plaintext reading, writes the CONNECT 200 message, asks the event loop to wrap the transport in TLS, patches the stream objects to use the TLS transport, and returns the same reader and writer for decrypted traffic.

**Call relations**: EgressProxy._mitm calls this after obtaining a per-host certificate from _leaf_context.

*Call graph*: called by 1 (_mitm); 3 external calls (drain, write, get_running_loop).


##### `_read_request_head`  (lines 884–917)

```
async def _read_request_head(reader: asyncio.StreamReader) -> tuple[bytes, list[bytes]] | _HeaderRefusal | None
```

**Purpose**: This reads an HTTP request line and headers with size and time limits. It protects the proxy from clients that send endless or very slow headers.

**Data flow**: A stream reader goes in. The function reads the request line and header lines until the blank line, returning the raw request line and headers, None for a closed connection, or a _HeaderRefusal explaining timeout or oversize headers.

**Call relations**: EgressProxy._handle uses it for the initial proxy request. EgressProxy._mitm uses it again after TLS starts to read the inside HTTP request.

*Call graph*: called by 2 (_handle, _mitm); 3 external calls (__init__, readline, timeout).


##### `_forward_match`  (lines 920–934)

```
def _forward_match(headers: list[bytes], candidates: list[ForwardRule]) -> ForwardRule | None
```

**Purpose**: This checks whether a request carries one of the special grant sentinel values that should be sent through a broker.

**Data flow**: Request headers and candidate ForwardRule objects go in. The function scans headers for the rule's configured header name and exact sentinel token, allowing common scheme prefixes like Bearer. It returns the matching rule or None.

**Call relations**: EgressProxy._mitm calls this before deciding between broker forwarding and direct upstream credential injection.

*Call graph*: called by 1 (_mitm).


##### `_read_request_body`  (lines 951–1004)

```
async def _read_request_body(reader: asyncio.StreamReader, headers: list[bytes]) -> bytes | _Refusal
```

**Purpose**: This reads the complete body for a broker-forwarded request, but only when it is safely bounded by Content-Length.

**Data flow**: A stream reader and headers go in. The function parses Content-Length, rejects chunked, missing, invalid, negative, too-large, or truncated bodies with a _Refusal, and otherwise returns the exact body bytes.

**Call relations**: EgressProxy._forward_broker calls this because broker forwarding is one enveloped request, not an open-ended stream.

*Call graph*: called by 1 (_forward_broker); 2 external calls (__init__, readexactly).


##### `_drain_refused_body`  (lines 1007–1024)

```
async def _drain_refused_body(reader: asyncio.StreamReader, pending: int) -> None
```

**Purpose**: This discards remaining upload bytes after the proxy has already refused a forwarded request body. It helps the client finish sending and then read the useful error response.

**Data flow**: A reader and maximum pending byte count go in. The function reads and throws away bytes up to a cap, until EOF, or until a short timeout expires. It returns nothing.

**Call relations**: EgressProxy._forward_broker calls this after sending a refusal returned by _read_request_body.

*Call graph*: called by 1 (_forward_broker); 2 external calls (read, timeout).


##### `_forward_headers`  (lines 1027–1039)

```
def _forward_headers(headers: list[bytes], rule: ForwardRule) -> dict[str, str]
```

**Purpose**: This prepares the safe header set to send to a grant broker. It removes headers that the proxy or broker must control, including the sentinel credential header.

**Data flow**: Original request headers and the matched ForwardRule go in. The function skips connection, host, content-length, and sentinel-bearing headers, decodes the rest, and returns a dictionary for the broker call.

**Call relations**: EgressProxy._forward_broker calls this immediately before invoking the broker's forward method.

*Call graph*: called by 1 (_forward_broker).


##### `_forward_response_bytes`  (lines 1042–1060)

```
def _forward_response_bytes(response: ForwardedResponse) -> bytes
```

**Purpose**: This turns a broker's response object back into a single HTTP/1.1 response for the sandbox client.

**Data flow**: A ForwardedResponse goes in. The function builds a status line, copies safe headers while dropping dangerous or re-derived ones, adds content-length and connection close, and appends the body bytes.

**Call relations**: EgressProxy._forward_broker calls this after the broker returns. It uses _has_crlf to prevent response header injection.

*Call graph*: calls 1 internal fn (_has_crlf); called by 1 (_forward_broker); 1 external calls (HTTPStatus).


##### `_has_crlf`  (lines 1063–1064)

```
def _has_crlf(value: str) -> bool
```

**Purpose**: This detects carriage-return or newline characters in a header name or value. Those characters could split one header into multiple wire lines.

**Data flow**: A string goes in. The function returns true if it contains '\r' or '\n', otherwise false. It changes nothing.

**Call relations**: _forward_response_bytes calls this while filtering broker-provided response headers.

*Call graph*: called by 1 (_forward_response_bytes).


##### `_inject`  (lines 1067–1092)

```
def _inject(headers: list[bytes], candidates: list[InjectionRule]) -> bytes
```

**Purpose**: This rewrites request headers for direct upstream calls by replacing exact sentinel values with the real credential for the matching rule.

**Data flow**: Original request headers and candidate InjectionRule objects go in. The function copies most headers, drops proxy-owned connection headers, substitutes only exact header-and-sentinel matches, adds connection close, and returns the rebuilt header block as bytes.

**Call relations**: EgressProxy._mitm calls this after deciding the request should go directly upstream rather than through a broker.

*Call graph*: called by 1 (_mitm).


##### `_relay`  (lines 1095–1129)

```
async def _relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, upstream_reader: asyncio.StreamReader, upstream_writer: asyncio.StreamWriter, on_downstream: Callable[[bytes]
```

**Purpose**: This copies bytes in both directions between the sandbox client and the upstream server. It also keeps a responding upstream alive after the client has finished sending its request.

**Data flow**: Client and upstream stream pairs go in, plus an optional callback for response chunks. The function starts two pump tasks, watches which side finishes first, optionally sends EOF upstream, waits for downstream progress within an idle timeout, then cancels pumps and closes upstream.

**Call relations**: _tunnel uses this for opaque CONNECT traffic. _mitm uses it for direct intercepted requests and may pass HttpTokenUsage.feed as the downstream callback.

*Call graph*: calls 1 internal fn (_pump); called by 2 (_mitm, _tunnel); 7 external calls (Event, can_write_eof, close, write_eof, create_task, timeout, wait).


##### `_pump`  (lines 1132–1147)

```
async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, on_chunk: Callable[[bytes], None] | None=None, on_progress: Callable[[], None] | None=None) -> None
```

**Purpose**: This is the one-way byte copier used by _relay. It reads chunks from one stream and writes them to another.

**Data flow**: A reader, writer, and optional callbacks go in. The function repeatedly reads fixed-size chunks, writes and drains them, reports progress, and passes chunks to the callback if provided. It quietly stops on socket errors or cancellation.

**Call relations**: _relay creates two _pump tasks, one upstream-to-client and one client-to-upstream.

*Call graph*: called by 1 (_relay); 3 external calls (read, drain, write).


##### `_int_field`  (lines 1150–1152)

```
def _int_field(usage: dict[str, object], name: str) -> int
```

**Purpose**: This safely extracts an integer usage field from a JSON-like dictionary. It treats missing values, booleans, and non-integers as zero.

**Data flow**: A usage dictionary and field name go in. The function reads the value and returns it only if it is a real integer, otherwise 0.

**Call relations**: HttpTokenUsage._absorb_anthropic and HttpTokenUsage._openai use this to avoid bad provider data corrupting token counts.

*Call graph*: called by 2 (_absorb_anthropic, _openai).


##### `HttpTokenUsage.feed`  (lines 1178–1215)

```
def feed(self, chunk: bytes) -> None
```

**Purpose**: This accepts raw response bytes from a model API and begins turning them into parseable body data. It understands HTTP headers, chunked transfer, and gzip or deflate compression.

**Data flow**: A response chunk goes in. The function accumulates headers until complete, detects transfer and content encoding, creates a decompressor if needed, and passes body bytes to _feed_wire_body. If limits or encodings are unsafe, it marks parsing failed.

**Call relations**: EgressProxy._mitm passes this as the downstream callback to _relay for token-metered model hosts.

*Call graph*: calls 2 internal fn (_fail, _feed_wire_body); 1 external calls (decompressobj).


##### `HttpTokenUsage.usage`  (lines 1217–1228)

```
def usage(self) -> tuple[str, Usage] | None
```

**Purpose**: This returns the model name and token counts parsed from the response, if any were found.

**Data flow**: The accumulator's stored response state goes in implicitly. It finishes decompression, tries to parse any leftover body as JSON, and returns a model plus Usage object or None if no valid usage was seen.

**Call relations**: EgressProxy._meter_tokens calls this after relaying the response stream.

*Call graph*: calls 2 internal fn (_finish_decoder, _maybe_json_body); 1 external calls (__init__).


##### `HttpTokenUsage._feed_wire_body`  (lines 1230–1276)

```
def _feed_wire_body(self, chunk: bytes) -> None
```

**Purpose**: This converts the HTTP wire body into actual payload bytes. It handles chunked transfer framing when present.

**Data flow**: Raw body bytes go in. If the response is not chunked, bytes go straight to _decode. If chunked, the function parses chunk sizes, extracts payload bytes, validates chunk separators, finishes on the zero-size chunk, and fails on malformed framing.

**Call relations**: HttpTokenUsage.feed calls this after headers are known. It sends payload bytes to _decode and may call _finish_decoder at the end.

*Call graph*: calls 3 internal fn (_decode, _fail, _finish_decoder); called by 1 (feed).


##### `HttpTokenUsage._decode`  (lines 1278–1294)

```
def _decode(self, chunk: bytes) -> None
```

**Purpose**: This decompresses body payload bytes when needed, then passes plain bytes onward for line and JSON parsing.

**Data flow**: Payload bytes go in. If there is no decompressor, they go directly to _feed_body. Otherwise the function repeatedly decompresses within the parser's buffer limit, feeds decoded bytes onward, and fails on decompression errors or no progress.

**Call relations**: HttpTokenUsage._feed_wire_body calls this for each body payload chunk.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 1 (_feed_wire_body).


##### `HttpTokenUsage._finish_decoder`  (lines 1296–1305)

```
def _finish_decoder(self) -> None
```

**Purpose**: This finishes a compressed response stream so no final usage bytes are left inside the decompressor.

**Data flow**: The accumulator's decompressor state goes in implicitly. The function flushes remaining decoded bytes once, feeds them to _feed_body, or marks parsing failed on decompression errors.

**Call relations**: HttpTokenUsage._feed_wire_body calls it when chunked data ends. HttpTokenUsage.usage calls it before returning results.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 2 (_feed_wire_body, usage).


##### `HttpTokenUsage._feed_body`  (lines 1307–1318)

```
def _feed_body(self, chunk: bytes) -> None
```

**Purpose**: This stores decoded response body bytes and processes complete lines. Server-sent events, often used for streaming model responses, are line-based.

**Data flow**: Decoded bytes go in. The function appends them to a body buffer, sends each complete line to _consume, removes consumed bytes, and fails if the buffer grows beyond the safety limit.

**Call relations**: HttpTokenUsage._decode and _finish_decoder call this after producing plain body bytes.

*Call graph*: calls 2 internal fn (_consume, _fail); called by 2 (_decode, _finish_decoder).


##### `HttpTokenUsage._consume`  (lines 1320–1337)

```
def _consume(self, line: bytes) -> None
```

**Purpose**: This examines one decoded response line for model usage information. It understands server-sent-event lines that begin with data: and also tries plain JSON lines.

**Data flow**: One line of bytes goes in. The function strips it, parses JSON payloads when present, ignores malformed or irrelevant lines, and dispatches provider-specific events to _anthropic or _openai.

**Call relations**: HttpTokenUsage._feed_body calls this for each complete line. It may call _maybe_json_body for non-SSE JSON.

*Call graph*: calls 3 internal fn (_anthropic, _maybe_json_body, _openai); called by 1 (_feed_body); 1 external calls (loads).


##### `HttpTokenUsage._maybe_json_body`  (lines 1339–1354)

```
def _maybe_json_body(self, payload: bytes) -> None
```

**Purpose**: This tries to parse a whole JSON response body for token usage, for non-streaming model API responses.

**Data flow**: A candidate JSON payload goes in. If usage has not already been found and the payload looks like JSON, it parses the object and extracts usage according to the configured host. It updates the accumulator if successful.

**Call relations**: HttpTokenUsage._consume calls this for non-SSE lines, and HttpTokenUsage.usage calls it as a final attempt on leftover body bytes.

*Call graph*: calls 2 internal fn (_absorb_anthropic, _openai); called by 2 (_consume, usage); 1 external calls (loads).


##### `HttpTokenUsage._anthropic`  (lines 1356–1366)

```
def _anthropic(self, event: dict[str, object]) -> None
```

**Purpose**: This handles Anthropic streaming event objects and extracts model and token usage from the event types Anthropic sends.

**Data flow**: An event dictionary goes in. For message_start, it reads the nested model and initial usage. For message_delta, it reads updated usage. It stores the latest counts in the accumulator.

**Call relations**: HttpTokenUsage._consume calls this when the metered host is Anthropic. It delegates field extraction to _absorb_anthropic.

*Call graph*: calls 1 internal fn (_absorb_anthropic); called by 1 (_consume).


##### `HttpTokenUsage._absorb_anthropic`  (lines 1368–1378)

```
def _absorb_anthropic(self, usage: object, initial: bool) -> None
```

**Purpose**: This copies Anthropic usage fields into the accumulator. It separates input, output, cache-read, and cache-write token counts.

**Data flow**: A usage object and a flag saying whether this is initial usage go in. If the object is a dictionary, the function reads integer fields, updates stored counts, and marks usage as seen.

**Call relations**: HttpTokenUsage._anthropic and _maybe_json_body call this. It uses _int_field for safe integer extraction.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_anthropic, _maybe_json_body).


##### `HttpTokenUsage._openai`  (lines 1380–1389)

```
def _openai(self, event: dict[str, object]) -> None
```

**Purpose**: This extracts model and token usage from OpenAI-style response objects.

**Data flow**: An event dictionary goes in. The function records the model string if present, reads prompt and completion token counts from the usage object, and marks usage as seen when usage is valid.

**Call relations**: HttpTokenUsage._consume and _maybe_json_body call this when the metered host is OpenAI. It uses _int_field to read counts safely.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_consume, _maybe_json_body).


##### `HttpTokenUsage._fail`  (lines 1391–1395)

```
def _fail(self) -> None
```

**Purpose**: This marks token parsing as failed and clears buffered data. It is a safety stop when the response is malformed, unsupported, or too large.

**Data flow**: The accumulator's current state goes in implicitly. The function sets the overflow/failure flag and clears header, body, and chunk buffers. After this, feed ignores more data and usage will not report counts.

**Call relations**: HttpTokenUsage.feed, _feed_wire_body, _decode, _finish_decoder, and _feed_body call this whenever parsing can no longer be trusted.

*Call graph*: called by 5 (_decode, _feed_body, _feed_wire_body, _finish_decoder, feed).


##### `_respond`  (lines 1398–1416)

```
async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None
```

**Purpose**: This writes a complete HTTP error or refusal response to a client. It includes the proxy's plain-language reason in the body so callers can see why the request failed.

**Data flow**: A stream writer, status code, and message go in. The function builds an HTTP/1.1 response with content type, content length, connection close, and the message body, writes it, and drains the writer. It ignores routine socket errors.

**Call relations**: EgressProxy._handle, _serve_workspace_credentials, _tunnel, _mitm, and _forward_broker use this whenever they need to refuse or report a failed request.

*Call graph*: called by 5 (_forward_broker, _handle, _mitm, _serve_workspace_credentials, _tunnel); 3 external calls (drain, write, HTTPStatus).


### `core/src/ufo/proxy_serve.py`

`entrypoint` · `startup and long-running proxy service`

A sandbox often needs to call outside services, such as model providers or connector APIs, but it should not get direct, unrestricted internet access or raw secret keys. This file is the startup point for a shared egress proxy: an outgoing-network gatekeeper that sits between sandboxes and the outside world. Think of it like a building reception desk: everyone exits through the same desk, but the receptionist checks each person’s badge before deciding which doors they can use and which keys they may borrow.

The file first loads the project configuration and extension manifests, which describe what connectors and credential slots are available. It then gathers several required secrets from the environment: the database owner connection string, the shared certificate authority used to sign proxy certificates, and possibly the encryption key used to read stored credentials. It fails early if a required piece is missing, because otherwise sandboxes would appear to run but would be unable to reach their allowed services safely.

The main `ProxyServe` object opens the database with an owner-level connection, but every lookup is still scoped by the workspace ID carried in a run token. It builds rules for model providers, connector hosts, credential injection, file-system credentials, and usage pricing. Finally, it starts the proxy server, waits for a shutdown signal, and stops cleanly.

#### Function details

##### `model_rule_base`  (lines 42–61)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the basic network rules that let sandboxes reach configured model providers, such as Anthropic or OpenAI. It only enables a provider if the provider’s API key is present in the process environment, and it refuses to continue if no model provider is available at all.

**Data flow**: It takes the loaded configuration as input, reads the named API key environment variables, and asks the model-rule builder what network hosts and key-replacement rules are needed. It combines all allowed provider hosts into one scope rule and keeps the other rules that swap placeholder keys for real keys on the wire. The result is a tuple of proxy rules; if no usable provider key is found, it raises an error instead of returning unusable rules.

**Call relations**: When `ProxyServe.serve` is assembling the live proxy, it calls this function to create the shared model-provider rule base. This base is then given to `PerAgentRules`, which combines it with per-workspace grants and credentials whenever a sandbox request needs to be checked.

*Call graph*: called by 1 (serve); 2 external calls (__init__, derive_model_rules).


##### `run`  (lines 64–85)

```
def run() -> None
```

**Purpose**: Starts the standalone shared proxy process. This is the top-level boot sequence for `ufoctl proxy`: it loads configuration, prepares secrets and telemetry, creates `ProxyServe`, and runs it forever until shutdown.

**Data flow**: It reads the project configuration, initializes observability output, loads extension manifests, fetches the shared proxy certificate authority, chooses the owner database connection string, and opens a credential store if the active pack needs one. It also builds pricing information for model usage. All of that is packaged into a `ProxyServe` instance with a shutdown event, then passed into the asynchronous event loop with `asyncio.run`. The visible outcome is a listening proxy service, or a clear startup error if required setup is missing.

**Call relations**: This function is the outermost coordinator in the file. It calls `_egress_ca`, `_owner_dsn`, and `_credential_store` to collect required runtime inputs, then hands the prepared values to `ProxyServe.serve`, which performs the actual database initialization and proxy startup.

*Call graph*: calls 3 internal fn (_credential_store, _egress_ca, _owner_dsn); 9 external calls (__init__, Event, run, load_config, injecting_slots, load_manifests, model_registry, init_o11y, log).


##### `_egress_ca`  (lines 88–99)

```
def _egress_ca() -> tuple[str, str]
```

**Purpose**: Reads the shared certificate authority used by the proxy to sign temporary certificates for sandbox traffic. This matters because sandboxes must trust the same certificate chain across proxy restarts.

**Data flow**: It reads the certificate and private key from the environment variables named by the sandbox session module. If both are present, it returns them as text. If either is missing, it raises an error explaining that the proxy cannot safely create certificates that sandboxes will trust.

**Call relations**: `run` calls this during startup before creating `ProxyServe`. The returned certificate and key are later passed into `EgressProxy`, which uses them when it intercepts and forwards sandbox network traffic securely.

*Call graph*: called by 1 (run).


##### `_owner_dsn`  (lines 102–115)

```
def _owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string that lets the shared proxy read data for all workspaces. It uses an owner-level database role, while relying on explicit workspace filters from run tokens to keep each request scoped correctly.

**Data flow**: It first checks the `UFO_OWNER_DSN` environment variable, then falls back to the owner database URL in the loaded configuration. If neither is set, it raises a startup error. If a value is found, it rewrites a plain PostgreSQL URL prefix so the application uses the expected asynchronous PostgreSQL driver, then returns that adjusted connection string.

**Call relations**: `run` calls this before constructing `ProxyServe`. Later, `ProxyServe.serve` passes the returned string to the database initializer so the proxy can resolve workspace-specific grants and credentials while serving requests.

*Call graph*: called by 1 (run).


##### `_credential_store`  (lines 118–132)

```
def _credential_store(config: Config, slots: tuple[CredentialSlot, ...]) -> CredentialStore | None
```

**Purpose**: Creates the object used to decrypt stored connector credentials, but only when the active extension pack needs credential injection. It prevents a dangerous half-working setup where connectors require secrets but the proxy has no key to read them.

**Data flow**: It receives the loaded configuration and the credential slots declared by the active manifests. It reads the configured encryption-key environment variable. If the key is present, it creates a Fernet encryptor/decryptor and wraps it in a `CredentialStore`. If the key is absent but credential-injecting slots exist, it raises an error. If no such slots exist, it returns `None` because there is no credential store to open.

**Call relations**: `run` calls this after loading manifests and detecting which credential slots need injection. The returned store, if any, is passed into `ProxyServe`, and then into `PerAgentRules`, which uses it later to fetch and inject the right workspace’s secrets into outbound requests.

*Call graph*: called by 1 (run); 2 external calls (__init__, Fernet).


##### `ProxyServe.serve`  (lines 150–181)

```
async def serve(self) -> None
```

**Purpose**: Runs the actual proxy service after startup values have been collected. It initializes the database, builds the rule resolver, starts the network proxy, waits for a shutdown signal, and then stops the proxy gracefully.

**Data flow**: It starts by registering signal handlers so Ctrl+C or a termination request can set the shutdown event. It initializes the database connection using the owner DSN. Then it builds `PerAgentRules` from model rules, grant storage, credential slots, connector rules, transfer hosts, and connector command-line tools. It prepares optional workspace file-system credential refreshing, creates an `EgressProxy` with certificate material, run-token decoding, pricing, and authorization callbacks, and starts listening on the configured port. After that it waits until shutdown is requested, then tells the proxy to stop within the configured grace period.

**Call relations**: `run` creates the `ProxyServe` instance and hands control to this method through the event loop. Inside, it calls `model_rule_base` to get the shared model-provider access rules, builds the resolver that answers “what may this sandbox do?”, and passes the resolver’s methods into `EgressProxy` so each outbound request can be checked and enriched before it leaves.

*Call graph*: calls 2 internal fn (model_rule_base, from_env); 11 external calls (__init__, __init__, __init__, get_running_loop, init_db, connector_clis, injecting_slots, log, sandbox_fs_minter, connector_transfer_hosts (+1 more)).

## 📊 State Registers Touched

- `reg-config` — The effective deployment settings that tell the system how to start, what services to use, and what safety rules are enabled.
- `reg-credential-store` — The encrypted secrets and credential slots used to let tools and connectors act for a workspace without exposing raw secrets.
- `reg-tool-context` — The per-run authority envelope that gives tools only the workspace, credentials, cleanup hooks, and permissions they are allowed to use.
- `reg-sandbox-session` — The sandbox handle and lifecycle state for the safe workspace where code, files, browsers, and commands run.
- `reg-workspace-storage` — The shared file, blob, artifact, and mount state that stores workspace bytes and files shared back to users.
- `reg-egress-policy` — The network access and proxy state that decides which sandbox traffic is allowed, audited, billed, or given injected secrets.
- `reg-browser-session` — The browser automation connection state used when tools need a controlled browser for a turn.
- `reg-accounting-ledger` — The usage, price, spend-cap, billing, export, and cost records used to track and limit money spent by workspaces and turns.
- `reg-observability-context` — The shared tracing, metrics, structured logs, and trace-parent links used to understand work across processes and turns.
- `reg-agent-runtime-settings` — Persistent non-prompt agent configuration such as selected runtime profile, workflow/tool policy, internet-access setting, and conversation or surface agent bindings.
