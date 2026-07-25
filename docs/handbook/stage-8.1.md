# Sandbox workspace and egress setup  `stage-8.1`

This stage prepares the safe “room” where a conversation can run commands. It is part of the setup before the main work begins. The goal is to give the command runner a workspace, tools, and controlled network access without letting it freely affect the host machine.

The workspace mount helper writes the instructions for attaching the conversation’s S3-backed files at /workspace inside the sandbox. S3 is cloud object storage, so this is like giving the room a locked filing cabinet with the right key. The Docker extension can create and manage a private local container for the conversation, while the E2B extension can use a remote cloud sandbox with the same general interface. The sandbox image builder keeps those local and cloud environments based on the same recipe, so commands see the same tools in either place. The sandbox Chrome extension starts a real headless Chrome browser inside the sandbox and exposes only a controlled debugging connection. Together, these pieces create a consistent, isolated workspace for executing code safely.

## Files in this stage

### Workspace mount contract
Defines the S3-backed workspace mount instructions and scoped credentials text that sandbox carriers consume.

### `core/src/ufo/sandbox/fs_mount.py`

`io_transport` · `sandbox startup and sandbox attach`

A sandbox needs a working folder where the agent can read and write files. In this system, that folder lives in S3, Amazon’s object storage service, but inside the sandbox it should feel like a normal directory at `/workspace`. This file is the recipe for making that happen with `s3fs`, a tool that presents an S3 bucket prefix as a filesystem.

The file builds four main pieces. First, it turns short-lived sandbox credentials into the standard AWS credentials-file format. Second, it builds the `s3fs` command that points at the right bucket, prefix, endpoint, and mount location. Third, it creates two shell snippets: one to prepare the machine as root, and one to mount as the sandbox user. The prepare step opens access to FUSE, the Linux feature that lets a user-space program act like a filesystem, enables sharing the mount with other users, and clears any old mount. The mount step creates the mount directory, protects the credentials file, and runs `s3fs`.

Finally, it builds a health-check command. This check is stricter than “does the mount exist?” It also tries to list the directory and checks that the credentials file is not too old. That matters because a paused or reused sandbox might still have a mount that looks present but has expired credentials underneath.

#### Function details

##### `aws_credentials_file`  (lines 22–28)

```
def aws_credentials_file(credentials: SandboxFsCredentials) -> str
```

**Purpose**: Builds the contents of the AWS credentials file that `s3fs` will read. This lets a freshly minted, limited-use sandbox credential become something standard AWS tools understand.

**Data flow**: It receives a `SandboxFsCredentials` object containing an access key, secret key, and session token. It places those values into the usual `[default]` AWS credentials-file layout. It returns one string that a carrier can write to `/home/user/.aws/credentials`.

**Call relations**: This function is one of the recipe pieces used before mounting. A carrier writes its returned text to disk, and the mount command produced elsewhere refers to the `default` profile in that file.


##### `s3fs_command`  (lines 31–54)

```
def s3fs_command(bucket: str, key_prefix: str, mountpoint: str, s3_url: str, region: str, path_style: bool) -> str
```

**Purpose**: Builds the shell command that mounts one S3 bucket prefix as a local directory. It includes the options needed for empty prefixes, shared access, custom S3 endpoints, and optional path-style S3 addressing.

**Data flow**: It receives the bucket name, key prefix, mountpoint, S3 URL, region, and a true-or-false choice for path-style requests. It safely shell-quotes values that will be inserted into a command, adds the needed `s3fs` options, and returns a complete command string. It does not execute the command.

**Call relations**: This command is handed to `mount_scripts`, which wraps it in the user-level mount step. Internally it uses `shlex.quote` so values become safe to place into a shell command.

*Call graph*: 1 external calls (quote).


##### `mount_scripts`  (lines 57–81)

```
def mount_scripts(mountpoint: str, s3fs: str) -> tuple[str, str]
```

**Purpose**: Builds the two shell commands needed to bring up the mount: one command for root preparation and one command for the sandbox user to perform the mount. These commands are designed to be safe to run repeatedly.

**Data flow**: It receives the mountpoint path and the already-built `s3fs` command. It quotes the mountpoint for shell safety, then creates a root `prepare` command that opens `/dev/fuse`, enables `user_allow_other`, and lazily unmounts anything already at the mountpoint. It also creates a user `mount` command that creates the directory, locks down the credentials file permissions, and runs `s3fs`. It returns both command strings as a pair.

**Call relations**: A carrier calls this when setting up or reattaching to a sandbox. The returned prepare command is meant to run with root rights, and the returned mount command is meant to run as the agent user. It uses `shlex.quote` to make the mountpoint safe inside shell text.

*Call graph*: 1 external calls (quote).


##### `mount_health_check`  (lines 84–98)

```
def mount_health_check(mountpoint: str) -> str
```

**Purpose**: Builds a shell probe that decides whether the existing `/workspace` mount can be reused or must be remounted. It checks not only that a mount exists, but also that it can actually read through `s3fs` and that its credentials are still fresh enough.

**Data flow**: It receives the mountpoint path. It quotes that path, then creates a shell expression that verifies three things: the path is a mountpoint, listing the directory succeeds, and the credentials file is less than half the credential lifetime old. It returns that shell expression as a string for a carrier to run.

**Call relations**: A carrier can run this probe before doing the prepare-and-mount work. If the probe succeeds, it can skip a redundant remount; if it fails, the carrier can refresh credentials and run the mount scripts. It uses `shlex.quote` to safely embed the mountpoint and credentials path in shell text.

*Call graph*: 1 external calls (quote).


### Sandbox carriers
Provides Docker and E2B execution backends that run each conversation in an isolated workspace while preserving the normal sandbox interface.

### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox lifecycle and command execution`

This file is the Docker version of a “carrier”: the part of the system that gives an agent a sandboxed place to run commands. Think of it like assigning each conversation its own disposable workshop. The tools and files can stay there between turns, but the workshop can be thrown away and rebuilt from the saved workspace if needed.

The main class, DockerCarrier, creates or re-attaches to a container named after the conversation. It starts the container with the right network setup, optionally connects the workspace either from the host filesystem or from S3, and installs the proxy’s certificate so HTTPS traffic can work through the proxy. Commands are run with `docker exec`, not by starting a new container each time.

A key safety detail is that proxy settings and API-key sentinels are passed only when a command is executed. They are not stored permanently in the container. This matters because a container may survive from one turn to the next, but each turn must use its own run token for metering and access control. The sandbox sees placeholder API keys, while the proxy swaps them for real credentials only on allowed outgoing requests.

The file also supports exporting produced files to blob storage without loading large files fully into the host process, and it has special setup for S3-backed workspaces using FUSE, a mechanism that lets a remote object store look like a normal folder.

#### Function details

##### `_docker`  (lines 57–71)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line tool asynchronously and returns its exit code, standard output, and error output. It is the one small doorway this file uses whenever it needs Docker to do something.

**Data flow**: It receives Docker arguments, optional input bytes, and a timeout. It starts a `docker ...` process, feeds the input to it, waits for it to finish, and returns the result as raw bytes. If Docker takes too long, it kills the process and returns a timeout-style exit code with an error message.

**Call relations**: All DockerCarrier operations use this helper instead of starting Docker commands themselves. Container creation, command execution, S3 mounting, network setup, certificate installation, lookup, and removal all pass through this single wrapper, so timeout behavior and output collection stay consistent.

*Call graph*: called by 8 (_ensure_network, _install_ca, _mount_healthy, _mount_s3, _running_id, create, destroy, exec); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 78–132)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a Docker sandbox for a conversation, or reuses the existing running one for that same conversation. It also prepares the per-turn network proxy environment that commands will use later.

**Data flow**: It takes a SandboxSpec containing the conversation ID, image, mount settings, proxy information, environment variables, and run token. It builds the container name and proxy environment, checks whether the container is already running, and either returns a handle for it or creates a new container. For a new container it ensures the Docker network exists, starts the container, installs the proxy certificate, mounts the workspace if needed, and returns a SandboxHandle that future calls use.

**Call relations**: This is the main setup path for the Docker carrier. It asks _running_id whether it can attach to an existing container, calls _ensure_network before starting a new one, uses _install_ca so HTTPS through the proxy will trust the proxy certificate, and calls _mount_s3 when the workspace lives in S3. It hands back the SandboxHandle that exec, export, destroy, and other carrier operations rely on.

*Call graph*: calls 5 internal fn (_ensure_network, _install_ca, _mount_s3, _running_id, _docker); 1 external calls (__init__).


##### `DockerCarrier.exec`  (lines 134–147)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command inside an already-created Docker sandbox. It makes sure that command gets the correct per-turn proxy settings and placeholder API keys.

**Data flow**: It receives a SandboxHandle, the command arguments, input bytes, and a timeout. It turns the handle’s environment values into Docker `--env` options, runs `docker exec -i` inside the container, decodes the command’s output, and returns an ExecResult with stdout, stderr, and the exit code.

**Call relations**: After create has returned a handle, higher-level sandbox code uses this method to actually run work inside the container. It delegates the Docker process work to _docker and wraps the result into the standard ExecResult shape used by the rest of the system.

*Call graph*: calls 1 internal fn (_docker); 1 external calls (__init__).


##### `DockerCarrier.export`  (lines 149–166)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Copies a file produced inside the sandbox workspace into blob storage, usually for large attachments or outputs. It avoids pulling the whole file into memory in this Python process.

**Data flow**: It receives a sandbox handle, a path inside `/workspace`, a blob store, and the destination key. It checks what kind of workspace mount is being used. For an S3-backed workspace, it asks the blob store to copy from the workspace prefix to the destination key inside storage. For a filesystem-backed workspace, it points the blob store at the corresponding host file path so it can stream the file in.

**Call relations**: This is used after commands have produced files in the workspace. It depends on create having set up a valid workspace mount. For S3 mounts it hands off to BlobStore.copy, and for filesystem mounts it hands off to BlobStore.put_file.

*Call graph*: calls 2 internal fn (copy, put_file); 2 external calls (Path, PurePosixPath).


##### `DockerCarrier._mount_s3`  (lines 168–232)

```
async def _mount_s3(self, handle: SandboxHandle, mount: MountSpec) -> None
```

**Purpose**: Mounts an S3-backed workspace inside the container at `/workspace`. In plain terms, it makes a folder in the sandbox behave like it is backed by a remote object-store prefix.

**Data flow**: It receives a SandboxHandle and MountSpec. If the mount is not S3, it does nothing. If an existing mount passes the health check, it also does nothing. Otherwise it verifies that all needed S3 details and temporary credentials are present, writes the credentials file inside the container, builds the s3fs command, prepares the container as root for FUSE mounting, then runs the mount command as the normal container user. If any step fails, it raises an error with Docker’s message.

**Call relations**: create calls this both when attaching to an existing container and after starting a new one, because a reused container may need its S3 mount refreshed. It first asks _mount_healthy whether work is needed, uses sandbox helper functions to format credentials and mount scripts, and runs each container-side step through _docker.

*Call graph*: calls 2 internal fn (_mount_healthy, _docker); called by 1 (create); 5 external calls (PurePosixPath, quote, aws_credentials_file, mount_scripts, s3fs_command).


##### `DockerCarrier._mount_healthy`  (lines 234–244)

```
async def _mount_healthy(self, handle: SandboxHandle) -> bool
```

**Purpose**: Checks whether the S3 workspace mount inside a container is still usable. This prevents unnecessary remounting when an existing container is still in good shape.

**Data flow**: It receives a SandboxHandle, builds a small health-check shell command for `/workspace`, and runs it inside the container. If the command exits successfully, it returns true; otherwise it returns false.

**Call relations**: _mount_s3 calls this before doing any heavier setup. The actual check command is supplied by the shared sandbox helper mount_health_check, while _docker runs it inside the container.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_mount_s3); 1 external calls (mount_health_check).


##### `DockerCarrier.destroy`  (lines 246–251)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Stops and removes the Docker container for a conversation. It is the cleanup path for a Docker sandbox.

**Data flow**: It receives a SandboxHandle, rebuilds the container name from the conversation ID, and runs `docker rm -f` on that name. It does not return anything important, and Docker treats a missing container as harmless here.

**Call relations**: Higher-level cleanup or reaper code can call this when a conversation sandbox should go away. It uses the stable conversation-based name rather than only the container ID, then delegates the Docker command to _docker.

*Call graph*: calls 1 internal fn (_docker).


##### `DockerCarrier.host`  (lines 253–260)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Explains that this Docker carrier cannot expose a service running inside the sandbox as an external host and port. Anyone needing that behavior must use another carrier that supports it.

**Data flow**: It receives a SandboxHandle and port number, but it does not use them to produce a route. Instead, it raises a RuntimeError telling the caller that Docker carrier port exposure is not available.

**Call relations**: This method exists because the carrier interface includes a way to ask for a reachable host for in-sandbox services. Unlike remote carriers such as e2b, this Docker implementation has no per-port external routing, so it stops the flow with a clear error.


##### `DockerCarrier._running_id`  (lines 262–273)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Looks up whether the conversation’s Docker container is already running. This lets create reuse a live sandbox instead of starting another one with the same name.

**Data flow**: It receives a container name, runs `docker ps` filtered to that exact name and running status, and reads Docker’s output. If Docker itself fails, it raises an error. If Docker succeeds but returns no ID, it returns None. If a matching container is found, it returns that container ID string.

**Call relations**: create calls this at the start of sandbox setup. A successful match sends create down the attach-and-refresh path; no match sends it toward network setup and container creation. The Docker lookup itself goes through _docker.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._ensure_network`  (lines 275–278)

```
async def _ensure_network(self) -> None
```

**Purpose**: Makes sure the Docker network used by sandbox containers exists. Without this, new containers might fail to start or be unable to reach the host-side proxy by the expected route.

**Data flow**: It reads the carrier’s configured network name, asks Docker to inspect that network, and if inspection fails, asks Docker to create it. It does not return a value; its effect is that the network should exist afterward.

**Call relations**: create calls this before starting a new container. It uses _docker for both the inspection and the possible creation command, keeping Docker interaction in the shared helper.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._install_ca`  (lines 280–293)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the proxy’s certificate authority certificate inside a new container. This lets programs in the sandbox make HTTPS requests through the project’s proxy without treating the proxy certificate as untrusted.

**Data flow**: It receives a container ID and certificate text. It runs a root command inside the container that writes the certificate into the system certificate directory and refreshes the trusted certificate list. If Docker reports failure, it raises an error with the command’s stderr text.

**Call relations**: create calls this only after starting a new container, before returning the handle. It uses _docker to run the privileged container command, so later exec calls can use the proxy environment successfully.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `manifest`  (lines 296–301)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO plugin system. It says that the carrier named `docker` is provided by DockerCarrier.

**Data flow**: It takes no input. It builds a Manifest object containing the extension name, version, and a CarrierSpec that points to the DockerCarrier class, then returns that manifest.

**Call relations**: The extension loader calls this when discovering available sandbox backends. The returned manifest is how the rest of the system learns that `[sandbox] backend = "docker"` can create DockerCarrier instances.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox startup, command execution, export, and teardown`

UFO needs a safe place to run user-requested tools and shell commands. By default that place is a Docker container, but this file teaches UFO how to use E2B instead: a cloud-hosted sandbox reached through E2B's software development kit, or SDK, which is a library for talking to E2B's service. The file is like an adapter plug. The rest of UFO asks for a sandbox in the usual way, and this adapter translates that into E2B actions.

When a conversation starts or resumes, E2BCarrier either creates a new E2B sandbox, reconnects to one already known in this process, or reconnects to one saved from an earlier process. It prepares the sandbox by installing UFO's proxy certificate, mounting the conversation workspace from S3 when needed, and returning a SandboxHandle that core code can use later.

Commands run through E2B's synchronous command API, so this file carefully moves those blocking calls into background threads so they do not freeze UFO's async event loop. Network access from inside the sandbox is routed through UFO's egress proxy, using a per-run token so traffic can be attributed and metered. When work is done, the sandbox is paused rather than fully deleted, making the next turn cheaper to resume.

#### Function details

##### `_egress_env`  (lines 83–115)

```
def _egress_env(proxy: ProxyEndpoint, run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make commands inside the remote sandbox send their web traffic through UFO's egress proxy. This matters because the sandbox is outside the cluster, so it needs a public HTTPS proxy address and must not receive real model API keys directly.

**Data flow**: It receives a proxy description and a run token. It checks that the proxy has a public HTTPS URL, turns that URL into proxy settings with the run token as the username, adds placeholder model keys and certificate settings, and returns a dictionary of environment variables. If the proxy URL is missing or unsafe, it raises an error before any open network route is created.

**Call relations**: E2BCarrier.create calls this after preparing or reconnecting a sandbox. The resulting environment is stored in the SandboxHandle so E2BCarrier.exec can later run commands with the correct metered network path.

*Call graph*: called by 1 (create); 1 external calls (urlsplit).


##### `E2BCommands.run`  (lines 125–133)

```
def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None) -> E2BCommandResult
```

**Purpose**: Describes the command-running method expected from an E2B sandbox. It is a protocol entry, meaning it tells type checkers what shape the external E2B object should have rather than providing the behavior itself.

**Data flow**: Callers provide a shell command plus optional working directory, environment variables, user, and timeout. The real E2B SDK runs that command in the sandbox and returns an object containing standard output, standard error, and an exit code.

**Call relations**: E2BCarrier uses this expected method in setup, mount checks, command execution, and certificate installation. The actual work is done by the E2B SDK object that satisfies this protocol.


##### `E2BFiles.make_dir`  (lines 137–137)

```
def make_dir(self, path: str, *, user: str | None=None) -> bool
```

**Purpose**: Describes the file API method used to create a directory inside an E2B sandbox. Here it is mainly needed to ensure the workspace directory exists in a newly created sandbox.

**Data flow**: A path and optional user name go in. The E2B SDK creates that directory inside the sandbox and reports success or failure as a boolean-like result.

**Call relations**: E2BCarrier.create uses this expected method when it creates a brand-new sandbox, before mounting or using the workspace.


##### `E2BFiles.write`  (lines 139–139)

```
def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: Describes the file API method used to write text or bytes into the sandbox. This file relies on it for writing the proxy certificate and S3 credentials into the remote machine.

**Data flow**: A destination path, data, and optional user name go in. The E2B SDK writes the content inside the sandbox and returns whatever confirmation object its API provides.

**Call relations**: E2BCarrier._install_ca and E2BCarrier._mount_s3 use this expected method during sandbox preparation. The method itself is supplied by the E2B SDK.


##### `E2BSandbox.pause`  (lines 148–148)

```
def pause(self, **opts: object) -> bool
```

**Purpose**: Describes the sandbox method that pauses an E2B sandbox instead of destroying it outright. Pausing keeps the sandbox cheap to resume for a later conversation turn.

**Data flow**: Optional provider-specific settings go in. The E2B SDK asks the remote sandbox to pause and returns whether that request succeeded.

**Call relations**: E2BCarrier.destroy calls this expected method when UFO is done with a sandbox or a reaper is reclaiming an idle one.


##### `E2BSandbox.get_host`  (lines 150–150)

```
def get_host(self, port: int) -> str
```

**Purpose**: Describes the method that gives a public host name for a service running on a port inside the sandbox. It lets outside code reach things like browser debugging endpoints or preview servers started in the sandbox.

**Data flow**: A port number goes in. The E2B SDK formats or returns a reachable host name for that port on the remote sandbox.

**Call relations**: E2BCarrier.host calls this after finding the right sandbox, then hands the address back to the rest of UFO.


##### `E2BSdk.create`  (lines 154–162)

```
def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the E2B SDK call used to create a fresh remote sandbox from a named template. The template is the prepared machine image UFO expects.

**Data flow**: The template name, timeout, metadata, lifecycle rules, and API key go in. E2B creates a sandbox in the cloud and returns an object that can run commands, write files, and expose ports.

**Call relations**: E2BCarrier.create uses this expected SDK call when there is no existing sandbox to resume. The real implementation comes from E2B's library.


##### `E2BSdk.connect`  (lines 164–164)

```
def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the E2B SDK call used to reconnect to an already existing sandbox by its ID. This is what makes conversation resumes and cleanup after process restarts possible.

**Data flow**: A sandbox ID, timeout, and API key go in. E2B returns a live sandbox object if that sandbox still exists, or raises an error if it cannot be found.

**Call relations**: E2BCarrier.create, E2BCarrier.destroy, and E2BCarrier._sandbox rely on this expected SDK call whenever this process needs to recover access to a sandbox it does not currently hold.


##### `E2BCarrier.create`  (lines 178–207)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Starts or resumes the E2B sandbox for a conversation and returns the handle UFO will use later. It is the main setup step that turns a SandboxSpec into a ready remote workspace.

**Data flow**: It receives a SandboxSpec containing the conversation ID, optional resume ID, mount details, proxy details, run token, and environment. It either reuses an in-memory sandbox, reconnects to a saved one, or creates a new one; then it installs the proxy certificate, mounts S3 if needed, builds egress environment variables, stores the live sandbox, and returns a SandboxHandle.

**Call relations**: Core sandbox orchestration calls this when it needs a sandbox for a conversation. Inside, it hands off certificate setup to E2BCarrier._install_ca, workspace mounting to E2BCarrier._mount_s3, and proxy environment construction to _egress_env.

*Call graph*: calls 3 internal fn (_install_ca, _mount_s3, _egress_env); 2 external calls (__init__, to_thread).


##### `E2BCarrier._install_ca`  (lines 209–220)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Installs UFO's egress proxy certificate into the remote sandbox so HTTPS traffic through the proxy is trusted. Without this, many tools inside the sandbox would reject proxied secure connections.

**Data flow**: It receives a sandbox and certificate text. It writes the certificate to a staging path as root, runs a root command to install it into the system certificate store, and returns nothing on success. If the install command fails, it turns the SDK error into a clear RuntimeError with the command's output.

**Call relations**: E2BCarrier.create calls this every time it prepares a sandbox. It uses the sandbox's file and command APIs through background threads because the E2B SDK is synchronous.

*Call graph*: called by 1 (create); 1 external calls (to_thread).


##### `E2BCarrier._mount_s3`  (lines 222–263)

```
async def _mount_s3(self, sandbox: E2BSandbox, mount: MountSpec) -> None
```

**Purpose**: Mounts the conversation's S3-backed workspace at the sandbox's workspace directory. This gives the remote sandbox access to the same durable files UFO uses outside the sandbox.

**Data flow**: It receives a sandbox and a mount description. If the mount is not S3, it does nothing; if the existing mount is healthy, it also does nothing. Otherwise it checks for required S3 credential and endpoint details, builds the s3fs mount command, writes credentials into the sandbox, runs preparation as root, runs the mount as the agent user, and raises a clear error if any command fails.

**Call relations**: E2BCarrier.create calls this after the certificate is installed. It first asks E2BCarrier._mount_healthy whether remounting is needed, then uses shared sandbox helpers to build the credential file and mount scripts.

*Call graph*: calls 1 internal fn (_mount_healthy); called by 1 (create); 4 external calls (to_thread, aws_credentials_file, mount_scripts, s3fs_command).


##### `E2BCarrier._mount_healthy`  (lines 265–274)

```
async def _mount_healthy(self, sandbox: E2BSandbox) -> bool
```

**Purpose**: Checks whether the workspace mount inside the sandbox is already usable. This avoids remounting over a working mount, which could disturb another task using the same sandbox.

**Data flow**: It receives a sandbox, runs a small health-check command inside it, and returns true if the command succeeds. If the command fails or times out, it returns false so the caller can rebuild the mount.

**Call relations**: E2BCarrier._mount_s3 calls this before doing any S3 mount setup. It delegates the actual shell test to the shared mount_health_check helper and runs the E2B command in a background thread.

*Call graph*: called by 1 (_mount_s3); 2 external calls (to_thread, mount_health_check).


##### `E2BCarrier.exec`  (lines 276–303)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command inside the E2B sandbox and returns its output in UFO's standard ExecResult shape. It is the main path used when the agent asks the sandbox to do work.

**Data flow**: It receives a SandboxHandle, command arguments, optional standard input bytes, and a timeout. It finds or reconnects the sandbox, safely quotes the command arguments, optionally base64-encodes stdin and pipes it into the command, runs the command in the workspace with proxy and tool environment variables, and returns stdout, stderr, and an exit code. Non-zero command exits and timeouts are converted into normal ExecResult values instead of leaking provider-specific exceptions.

**Call relations**: Sandbox users call this after E2BCarrier.create has returned a handle. It uses E2BCarrier._sandbox to recover the live E2B object, then calls the E2B command API through a background thread.

*Call graph*: calls 1 internal fn (_sandbox); 5 external calls (__init__, to_thread, b64encode, join, quote).


##### `E2BCarrier.export`  (lines 305–317)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Copies a file produced in the sandbox workspace into the artifact store. It does this as a server-side S3 copy, so the file bytes do not have to travel back through the remote sandbox process.

**Data flow**: It receives a handle, a workspace path, a BlobStore, and the destination artifact key. It checks that the workspace is an S3 mount, converts the absolute workspace path into a relative path under the mount prefix, and asks the blob store to copy that source object to the requested artifact key.

**Call relations**: Artifact-sharing code calls this after a sandbox command has produced a file. It relies on the mount information saved in the SandboxHandle created by E2BCarrier.create.

*Call graph*: calls 1 internal fn (copy); 1 external calls (PurePosixPath).


##### `E2BCarrier.host`  (lines 319–326)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Returns the public host name for a port exposed by the remote sandbox. This lets UFO or callers reach services started inside the sandbox.

**Data flow**: It receives a SandboxHandle and a port number. It finds or reconnects the sandbox, asks E2B for the host name for that port, and returns that address as a string.

**Call relations**: Higher-level code calls this when it needs to connect to an in-sandbox service. It shares the same sandbox lookup path as command execution by using E2BCarrier._sandbox.

*Call graph*: calls 1 internal fn (_sandbox).


##### `E2BCarrier.destroy`  (lines 328–348)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Pauses the sandbox for a conversation and removes it from this process's live cache. This is cleanup, but it preserves the possibility of cheap resume while also letting idle sandboxes be reclaimed.

**Data flow**: It receives a SandboxHandle. It removes any in-memory sandbox for the conversation; if none is present but the handle has a container ID, it tries to reconnect to that sandbox; if the sandbox exists, it pauses it. If the sandbox ID is empty or E2B says it no longer exists, it quietly finishes because there is nothing left to clean up.

**Call relations**: The sandbox lifecycle or reaper calls this when a sandbox should stop being active. It may operate on a sandbox this process already knows or reconnect to one left behind by another process.

*Call graph*: 1 external calls (to_thread).


##### `E2BCarrier._sandbox`  (lines 350–361)

```
async def _sandbox(self, handle: SandboxHandle) -> E2BSandbox
```

**Purpose**: Finds the live E2B sandbox object for a handle, reconnecting to E2B if this process does not already have it cached. It is a small helper that keeps exec and host from duplicating reconnect logic.

**Data flow**: It receives a SandboxHandle. It first checks the carrier's in-memory map by conversation ID; if found, it returns that sandbox. Otherwise it connects to E2B using the handle's container ID, stores the resulting sandbox in the map, and returns it.

**Call relations**: E2BCarrier.exec and E2BCarrier.host call this whenever they need the actual provider object behind a SandboxHandle. It talks to the E2B SDK through a background thread.

*Call graph*: called by 2 (exec, host); 1 external calls (to_thread).


##### `build_e2b_carrier`  (lines 364–373)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: Creates the E2BCarrier instance used when the deployment selects the E2B backend. It also fails early if the required E2B API key is missing.

**Data flow**: It reads the E2B_API_KEY environment variable. If the key is absent, it raises an error during startup; if present, it builds and returns an E2BCarrier configured with that key and UFO's expected E2B template name.

**Call relations**: The manifest points to this factory so UFO can construct the carrier only when the e2b backend is selected.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 376–381)

```
def manifest() -> Manifest
```

**Purpose**: Registers this extension with UFO's plugin system under the carrier name e2b. This is how the core system discovers that this file can provide a sandbox backend.

**Data flow**: It creates a Manifest containing the extension name, version, and a CarrierSpec that names the e2b carrier, points to build_e2b_carrier, and marks it as off-cluster. The manifest object is returned to the extension loader.

**Call relations**: UFO's extension loading code calls this to discover available capabilities. The returned carrier specification tells core code which factory to call when configuration asks for the E2B sandbox backend.

*Call graph*: 2 external calls (__init__, __init__).


### Browser runtime and image template
Adds in-sandbox Chrome access and maintains the shared sandbox image recipe used by local Docker and hosted E2B environments.

### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease setup`

This file solves a practical hosting problem: each conversation needs its own browser, but the main server process cannot simply reach into the sandbox and talk to Chrome directly. So this provider starts Chrome inside the sandbox, exposes Chrome’s debugging connection through the sandbox’s public port system, and returns a connection address that the rest of the browser system can use.

The flow is like setting up a private workshop and then putting a guarded service window in front of it. First, the provider makes sure Chrome is running in the sandbox on port 9222. It uses a pid file so it does not start a second Chrome if one is already alive. Next, it starts a small TCP proxy on port 9223. This proxy is important because Chrome’s DevTools protocol checks the HTTP Host header and rejects requests that do not look local. The proxy rewrites that header to look like localhost while passing the WebSocket traffic through unchanged.

Then the provider asks Chrome for its DevTools WebSocket address, keeps only the local path part, asks the sandbox for the public host for port 9223, and builds a public ws or wss URL. If the sandbox requires a traffic token, that token is attached as a connection header. The returned lease does not shut Chrome down when closed, because the browser is meant to live as long as the conversation sandbox does.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 194–195)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the Chrome DevTools endpoint that was prepared for this turn. Other code uses this endpoint to connect to the sandbox’s browser.

**Data flow**: It starts with the endpoint stored inside the lease. It does not change or recalculate anything. It simply gives that stored endpoint back to the caller.

**Call relations**: A SandboxChromeCdpLease is created by SandboxChromeCdpProvider.lease after Chrome, the proxy, and the public URL have been prepared. Later, browser code asks the lease for its endpoint so it can open the actual debugging connection.


##### `SandboxChromeCdpLease.token`  (lines 197–198)

```
async def token(self) -> str
```

**Purpose**: Returns a simple string that represents this lease: the endpoint URL itself. This is used as the durable handle for the connection, even though this provider cannot truly recreate the session from the token alone.

**Data flow**: It reads the URL from the stored endpoint object. It returns that URL as plain text and changes nothing.

**Call relations**: The lease is handed out by SandboxChromeCdpProvider.lease. When code asks for a token, this method gives back the URL of the endpoint that was already resolved from the live sandbox.


##### `SandboxChromeCdpLease.aclose`  (lines 200–201)

```
async def aclose(self) -> None
```

**Purpose**: Finishes the lease without shutting down Chrome or the proxy. This is intentional because both are meant to keep running inside the conversation’s sandbox across turns.

**Data flow**: It receives no new data beyond the lease itself. It performs no cleanup and returns nothing.

**Call relations**: Code that follows the general lease pattern may call this when a turn is over. For this provider, closing the lease is a no-op because SandboxChromeCdpProvider.lease starts reusable sandbox processes rather than short-lived ones.


##### `SandboxChromeCdpProvider.lease`  (lines 212–223)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Prepares and returns a usable Chrome DevTools connection for the current sandbox. It starts or reuses Chrome, starts or reuses the proxy, discovers the browser’s WebSocket path, and builds the public endpoint.

**Data flow**: It receives a SandboxSession, which represents the current conversation’s sandbox. If no sandbox is provided, it raises an error because this provider cannot work without one. It runs shell commands inside the sandbox to start Chrome and the proxy, reads Chrome’s local WebSocket URL, extracts the path, asks the sandbox for the public host of the proxy port, adds the sandbox traffic token as a header when needed, and returns a SandboxChromeCdpLease containing a CdpEndpoint.

**Call relations**: This is the main entry point for this provider during a browser turn. It calls _run three times to execute sandbox commands, uses _ws_path to convert Chrome’s local WebSocket URL into a safe path, asks SandboxSession.host for the public address of the proxy, and passes that host and path to _remote_ws_url. It then packages the result into CdpEndpoint and SandboxChromeCdpLease for the browser engine to use.

*Call graph*: calls 4 internal fn (host, _remote_ws_url, _run, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 225–226)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reports that an old token cannot be used by itself to restore this browser connection. A fresh lease must be made from a live sandbox instead.

**Data flow**: It receives a token string from a previous lease. Rather than using it to rebuild a connection, it raises SessionGone with that token to say the old session should be treated as unavailable.

**Call relations**: This fits the provider interface, but this implementation deliberately refuses token-only recovery. The file’s design depends on SandboxChromeCdpProvider.lease being called with the current SandboxSession so it can rediscover the live sandbox host and browser state.

*Call graph*: 1 external calls (__init__).


##### `_run`  (lines 229–233)

```
async def _run(sandbox: SandboxSession, command: str, what: str) -> str
```

**Purpose**: Runs a shell command inside the sandbox and turns failures into clear Python errors. It is the safe wrapper this file uses for starting Chrome, starting the proxy, and asking Chrome for its debugging URL.

**Data flow**: It receives a sandbox, a shell command, and a short description of what the command is trying to do. It sends the command to SandboxSession.bash with a timeout. If the command succeeds, it returns the command’s standard output. If it fails, it raises a RuntimeError that includes the command’s error text or output.

**Call relations**: SandboxChromeCdpProvider.lease calls this helper for each in-sandbox step. _run hands the actual execution off to SandboxSession.bash, then gives lease either the successful output it needs or a clear failure that stops the setup.

*Call graph*: calls 1 internal fn (bash); called by 1 (lease).


##### `_ws_path`  (lines 236–241)

```
def _ws_path(url: str) -> str
```

**Purpose**: Extracts the path part from Chrome’s local DevTools WebSocket URL. This keeps only the part that should be reused behind the sandbox proxy.

**Data flow**: It receives a WebSocket URL printed by Chrome, trims surrounding whitespace, and checks that it starts with one of the expected local Chrome addresses. If it does, it removes that local prefix and returns the remaining path. If the URL is not local, it raises an error because using an unexpected address would be unsafe or wrong.

**Call relations**: SandboxChromeCdpProvider.lease calls this after _run retrieves Chrome’s webSocketDebuggerUrl. The returned path is then combined with the public sandbox proxy host by _remote_ws_url.

*Call graph*: called by 1 (lease).


##### `_remote_ws_url`  (lines 244–250)

```
def _remote_ws_url(host: str, path: str) -> str
```

**Purpose**: Builds the final WebSocket URL that outside code can connect to. It converts the sandbox’s public HTTP-style host into the matching WebSocket form.

**Data flow**: It receives a public host and a WebSocket path. It first normalizes the host through _remote_url. If the resulting base starts with https://, it returns a wss:// URL, which is the secure WebSocket form. If it starts with http://, it returns a ws:// URL. If the host is not an HTTP or HTTPS address, it raises an error.

**Call relations**: SandboxChromeCdpProvider.lease calls this after it gets the sandbox’s public proxy host and Chrome’s WebSocket path. _remote_ws_url relies on _remote_url to make sure the host has a scheme before building the final endpoint URL.

*Call graph*: calls 1 internal fn (_remote_url); called by 1 (lease).


##### `_remote_url`  (lines 253–257)

```
def _remote_url(host: str) -> str
```

**Purpose**: Normalizes a sandbox host into a full HTTP or HTTPS URL. This lets later code reliably decide whether to use ws:// or wss:// for the WebSocket connection.

**Data flow**: It receives a host string. If the string already starts with http:// or https://, it returns it unchanged. If it looks like a local host, it adds http://. Otherwise, it assumes the host is a public remote address and adds https://.

**Call relations**: _remote_ws_url calls this before constructing the final WebSocket address. This helper keeps the URL guessing rules in one place so the rest of the provider can work with a predictable base URL.

*Call graph*: called by 1 (_remote_ws_url).


##### `manifest`  (lines 260–269)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the larger UFO system. It tells the system that a CDP provider named sandbox_chrome is available and how to build it.

**Data flow**: It reads the file’s constants for the extension name, version, and backend name. It creates a CdpProviderSpec whose build function returns a SandboxChromeCdpProvider, then wraps that in a Manifest object and returns it.

**Call relations**: The extension loading system calls manifest to discover what this file offers. The returned Manifest contains a CdpProviderSpec, and that spec gives core code a way to create SandboxChromeCdpProvider when configuration selects the sandbox_chrome backend.

*Call graph*: 2 external calls (__init__, __init__).


### `sandbox/build_template.py`

`entrypoint` · `build/deploy time`

This script is the build tool for UFO’s sandbox runtime: the prepared environment where code, document tools, browser tools, and helper commands are available. Without it, the E2B-hosted sandbox and the Docker-based sandbox could slowly drift apart, meaning a script might work in one place but fail in the other.

The file defines one shared set of image layers: operating-system packages, Python packages, Node.js packages, Playwright browser files, environment variables, and UFO’s own in-sandbox helper scripts named `sbx` and `sbxfs`. Think of it like one packing list used for two suitcases: one suitcase is the E2B template, and the other is a Docker image.

It also writes a build digest into the image. A digest is a fingerprint of the build recipe and the script contents. Later, the `--check` mode can boot the live published template, read that fingerprint, and confirm it still matches the source code here.

The command-line behavior is straightforward: with no flags, it builds and publishes the E2B template, then boots it and verifies the expected tools are present. With `--check`, it checks for drift without publishing. With `--dockerfile`, it prints a Dockerfile. With `--build-docker`, it builds the Docker image locally.

#### Function details

##### `build_definition_digest`  (lines 156–181)

```
def build_definition_digest() -> str
```

**Purpose**: Creates a fingerprint of everything that matters in the sandbox build recipe. This lets the project tell whether the published sandbox was built from the current source or from an older recipe.

**Data flow**: It reads the configured base template, users, startup command, readiness check, package lists, environment variables, and the contents of the sandbox helper scripts. It turns that information into a stable JSON string, hashes it with SHA-256, and returns a string like a labeled fingerprint.

**Call relations**: When the image layers are being prepared, `apply_layers` calls this function and writes the fingerprint into the sandbox image. Later, `check_published_template` calls it again to compute what the fingerprint should be now, then compares that to the fingerprint found in the live published template.

*Call graph*: called by 2 (apply_layers, check_published_template); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 184–210)

```
def apply_layers(builder: object) -> object
```

**Purpose**: Adds the shared UFO sandbox setup to an image builder. This is the central recipe that makes both the E2B template and the Docker image contain the same tools, scripts, environment variables, and startup command.

**Data flow**: It receives a builder object that represents an image being assembled. It switches to the build user, installs system tools, removes sudo access, installs GitHub CLI, Python packages, Node packages, and Playwright’s browser, writes the build fingerprint, copies the `sbx` and `sbxfs` scripts into the command path, sets permissions, switches back to the runtime user, and returns the builder with the start and readiness commands set.

**Call relations**: `e2b_template` calls this when preparing the hosted E2B template. `pod_dockerfile` calls it when preparing the Docker version. Inside this shared recipe, it calls `build_definition_digest` so the finished image carries proof of exactly what recipe created it.

*Call graph*: calls 1 internal fn (build_definition_digest); called by 2 (e2b_template, pod_dockerfile).


##### `e2b_template`  (lines 213–215)

```
def e2b_template() -> object
```

**Purpose**: Creates the build definition for the hosted E2B sandbox template. It starts from E2B’s base code-interpreter template, then applies UFO’s shared sandbox layers.

**Data flow**: It creates an E2B template builder using the repository root as the file context, chooses the configured E2B base template, passes that builder through `apply_layers`, and returns the completed template definition.

**Call relations**: `main` calls this in the normal publish path. The returned definition is handed to E2B’s template build function so the hosted sandbox image can be built and published.

*Call graph*: calls 1 internal fn (apply_layers); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 218–220)

```
def pod_dockerfile() -> str
```

**Purpose**: Produces a Dockerfile for the Docker-based sandbox image using the same shared recipe as the E2B template. This keeps local Docker sandboxes aligned with the hosted E2B sandbox.

**Data flow**: It creates an image builder from the public Docker base image, passes that builder through `apply_layers`, then asks the E2B SDK to render the result as Dockerfile text. The output is a string containing the Dockerfile.

**Call relations**: `main` calls this when the user asks for `--dockerfile`. `build_docker_image` calls it when it needs Dockerfile text to feed into `docker build`. It depends on `apply_layers` so the Docker image gets the same installed tools and scripts as the E2B template.

*Call graph*: calls 1 internal fn (apply_layers); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 223–234)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image that UFO’s Docker carrier runs. This path does not need an E2B account because it only renders a Dockerfile and uses the local Docker daemon.

**Data flow**: It asks `pod_dockerfile` for the Dockerfile text, sends that text into `docker build` through standard input, and uses the repository root as the Docker build context. If Docker reports failure, it stops the script with an error; if the build succeeds, it prints the Docker image tag.

**Call relations**: `main` calls this when the user passes `--build-docker`. It hands off the actual image construction to the external `docker build` command after getting the Dockerfile from `pod_dockerfile`.

*Call graph*: calls 1 internal fn (pod_dockerfile); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 237–252)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a newly published E2B template can actually start and contains the required baked-in tools. This prevents a broken or incomplete sandbox image from being treated as a successful release.

**Data flow**: It receives the template name, starts a sandbox from that template, runs the readiness command that checks for required tools such as Python, Node, `sbx`, `sbxfs`, browser tools, and document utilities, then shuts the sandbox down. If the command fails or exits unsuccessfully, it raises an error.

**Call relations**: `main` calls this immediately after building the E2B template in the normal publish path. It uses E2B’s sandbox creation API to boot the newly published image and acts as the final gate before the script prints the template id.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 255–276)

```
def check_published_template(name: str) -> None
```

**Purpose**: Checks whether the live E2B template still matches the current build recipe without publishing anything. It is a drift check: it catches the case where source code changed but the hosted sandbox was not rebuilt.

**Data flow**: It computes the expected digest from the current source using `build_definition_digest`, starts a sandbox from the named live template, reads the digest file baked into that sandbox, then shuts the sandbox down. If the digest is missing or different, it raises an error explaining that the template must be republished.

**Call relations**: `main` calls this when the user passes `--check`. It shares the same digest logic used by `apply_layers`, so the check compares the live template against the exact recipe that would be baked into a new image.

*Call graph*: calls 1 internal fn (build_definition_digest); called by 1 (main); 1 external calls (create).


##### `main`  (lines 279–310)

```
def main() -> None
```

**Purpose**: Provides the command-line interface for this build script. It decides whether to print a Dockerfile, build a Docker image, check the published E2B template, or build and verify a new E2B template.

**Data flow**: It reads command-line flags, chooses one mode, and runs the matching path. `--dockerfile` writes Dockerfile text to standard output; `--build-docker` builds the local Docker image; `--check` validates the live E2B template digest; with no flag, it builds the E2B template, verifies it by booting a sandbox, and prints the resulting template id.

**Call relations**: This is the top-level driver called when the file is run as a script. Depending on the chosen flag, it calls `pod_dockerfile`, `build_docker_image`, `check_published_template`, or the publish flow made from `e2b_template`, E2B’s build API, and `verify_published_template`.

*Call graph*: calls 5 internal fn (build_docker_image, check_published_template, e2b_template, pod_dockerfile, verify_published_template); 2 external calls (ArgumentParser, build).
