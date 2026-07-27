# Deployment bundle and sandbox validation gates  `stage-1.1`

This stage is part of deployment preparation. It freezes what will be shipped, then checks that the outside services the system depends on really work before the release is accepted. Think of it like packing a travel kit and testing the roads before starting the trip.

`core/src/ufo/bundle.py` builds the deploy bundle for UFO. It creates a Docker build folder, copies in the chosen configuration, writes a fresh lockfile, and adds a Dockerfile. The lockfile is a record of exact extension choices, so the same bundle should start the same way on another machine.

`sandbox/build_template.py` builds the sandbox image, which is the prepared workroom where UFO can run tools such as scripts, browser automation, PDF utilities, and office converters. It keeps the hosted E2B sandbox and the local Docker image based on the same recipe.

The two gate files are safety tests. `sandbox/mount_gate.py` starts a real sandbox and checks that `/workspace` storage can be mounted and used. `sandbox/proxy_gate.py` checks that HTTPS traffic can pass through the expected proxy route.

## Files in this stage

### Deploy Bundle Assembly
Creates the frozen deploy bundle and shared sandbox image recipe that deployment will rely on.

### `core/src/ufo/bundle.py`

`orchestration` · `bundle creation`

This file is for turning a working `ufo` setup into a portable artifact. Think of it like packing a lunchbox: it copies the food you chose, writes down exactly what is inside, and includes instructions for how to open and use it later. Without this step, a deploy might depend on whatever extensions happen to be installed on the next machine, which could make it behave differently.

The main class, `Bundle`, takes three things: the path to the current config file, an optional extension catalog, and an output folder. When `build` runs, it first decides which extensions must be pinned. A pin is a record that names an extension and ties it to the installed version or digest, so startup can later check that the expected extension is really present. It starts from the current lockfile if one exists; otherwise it uses every discovered installed extension. If an extension catalog is available, it also adds entries marked as disabled in the store, because those are “bundle-only”: they are included during bundling rather than installed at runtime.

After that, the file creates the output folder, copies the config into it as `ufo.toml`, writes a new `ufo.lock`, and creates a Dockerfile. That Dockerfile installs the local `ufo` wheel, points the program at the bundled config and lockfile, and starts `ufoctl serve` by default.

#### Function details

##### `wheel_name`  (lines 25–28)

```
def wheel_name() -> str
```

**Purpose**: This function builds the expected filename of the local `ufo` Python wheel, which is the installable package the Docker image will copy and install. It uses the current `ufo` version so the Dockerfile points at the exact file produced for this release.

**Data flow**: It reads the current `ufo` version from the extension store helper, inserts that version into the standard wheel filename pattern, and returns the resulting string, such as a package filename ending in `.whl`. It does not write files or change state.

**Call relations**: When `Bundle._dockerfile` writes the Dockerfile text, it calls `wheel_name` so the generated `COPY`, `RUN pip install`, and cleanup lines all refer to the correct wheel file.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 49–62)

```
def build(self) -> BundleResult
```

**Purpose**: This is the main action for creating the bundle. It gathers extension pins, creates the output directory, copies the config, writes the lockfile, writes the Dockerfile, and returns a summary of what it produced.

**Data flow**: It starts with the `Bundle` object's config path, optional catalog, and output folder. It asks `_pins` for the exact extension list to freeze, creates the output folder if needed, copies the current config text into `ufo.toml`, writes a JSON lockfile containing the current `ufo` version and those pins, asks `_dockerfile` for Dockerfile text, writes that too, and finally returns a `BundleResult` containing the paths and pins.

**Call relations**: This is the top-level method other bundling code would call when it is time to produce an artifact. It delegates extension selection to `Bundle._pins`, delegates Dockerfile text generation to `Bundle._dockerfile`, and packages the finished file paths into `BundleResult` for the caller.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 64–78)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: This private helper decides which extensions must be frozen into the bundle. It makes sure the bundle includes what the current deploy already uses, plus any catalog entries that are meant to be added only at bundle time.

**Data flow**: It first asks the extension loader what extensions are currently installed and where the lockfile should be. If a lockfile already exists, it uses the extension names from that lockfile as the base list; if not, it uses all discovered installed extensions. If a catalog is available, it adds catalog extensions marked as disabled, because those are bundle-only additions. It removes duplicates while keeping order, then asks `pin_for` to create a verified pin for each name, returning the pins as a tuple.

**Call relations**: This method is called by `Bundle.build` before any files are written. It relies on extension loader helpers to inspect the current environment and on the store helper `pin_for` to turn each selected extension name into a concrete pin that can be written into the bundle lockfile.

*Call graph*: called by 1 (build); 4 external calls (discovered, lockfile_path, read_lockfile, pin_for).


##### `Bundle._dockerfile`  (lines 80–94)

```
def _dockerfile(self) -> str
```

**Purpose**: This private helper writes the text of the Dockerfile used to build the runnable image. The Dockerfile installs the bundled `ufo` wheel, copies in the frozen config and lockfile, and sets the default command to run the server.

**Data flow**: It takes no separate input beyond the module constants and the current `ufo` wheel name. It builds a series of Dockerfile lines: choose the Python base image, set `/app` as the working folder, point environment variables at the bundled config and lockfile, copy and install the wheel, copy the config and lockfile, and set the entrypoint and default command. It returns the complete Dockerfile as one string.

**Call relations**: This method is called by `Bundle.build` when the bundle is being written to disk. It calls `wheel_name` so the Dockerfile refers to the same wheel filename that the bundling command is expected to place beside the Docker build context.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### `sandbox/build_template.py`

`entrypoint` · `build and deployment time`

This script is the project’s sandbox-image builder. The sandbox is like a stocked workshop: before any task starts, it already contains the command-line tools, Python packages, Node packages, browser files, and UFO helper scripts that later code expects to find. Without this file, the E2B-hosted sandbox and the Docker-based sandbox could be built differently, causing a tool to work in one place but fail in another.

The key idea is “one recipe, two kitchens.” The shared recipe is applied by `apply_layers`: install system packages, build a pinned version of `s3fs` for stable cloud-storage mounting, install GitHub’s CLI, install Python and Node libraries, bake in UFO sandbox scripts, set environment variables, and choose the startup command. That same recipe is then used either on top of an E2B base template or on top of a Docker base image.

The file also protects against stale builds. It computes a digest, which is a fingerprint of the build recipe and script contents, and writes it into the image. The `--check` mode boots the live template and compares that baked fingerprint with the current source. The normal publish path also boots the freshly built template and runs a readiness probe, so missing tools are caught immediately instead of failing later during real work.

#### Function details

##### `build_definition_digest`  (lines 183–209)

```
def build_definition_digest() -> str
```

**Purpose**: Creates a fingerprint of everything important that goes into the sandbox image. This lets the project tell whether the live published sandbox was built from the same recipe as the current source code.

**Data flow**: It reads the fixed build settings in this file, such as base image names, users, package lists, environment variables, readiness command, and the contents of the sandbox helper scripts. It turns that information into a stable JSON text and hashes it with SHA-256, which is a standard way to make a short identifier from larger content. It returns a string like `sha256:...`, and does not change anything by itself.

**Call relations**: When `apply_layers` builds an image, it calls this function and writes the fingerprint into the sandbox. Later, `check_published_template` calls it again from the current source and compares the new fingerprint with the one baked into the live template.

*Call graph*: called by 2 (apply_layers, check_published_template); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 212–239)

```
def apply_layers(builder: object) -> object
```

**Purpose**: Applies the shared sandbox build recipe to a builder object. This is the central place that says what must be installed and configured inside both the E2B template and the Docker image.

**Data flow**: It receives a builder, which is an object that records image-building steps. It adds commands to install operating-system tools, remove sudo, build the pinned `s3fs` tool, install GitHub CLI, install Python and Node packages, install the Playwright browser, create UFO directories, write the build fingerprint, set environment variables, copy UFO helper scripts, fix permissions, switch to the runtime user, and set the long-running start command. It returns the same builder after adding all of these layers.

**Call relations**: Both `e2b_template` and `pod_dockerfile` call this function so they share the same image contents. Inside that flow, it calls `build_definition_digest` so the image carries proof of exactly what recipe produced it.

*Call graph*: calls 1 internal fn (build_definition_digest); called by 2 (e2b_template, pod_dockerfile).


##### `e2b_template`  (lines 242–244)

```
def e2b_template() -> object
```

**Purpose**: Creates the E2B version of the sandbox build definition. E2B is the hosted sandbox service used by this project.

**Data flow**: It starts with the repository root as the build context and chooses E2B’s `code-interpreter-v1` template as the base. It then passes that builder through `apply_layers`, which adds the shared UFO sandbox contents. It returns the completed E2B build definition, not a running sandbox.

**Call relations**: `main` calls this when the script is run in its default publish mode. The returned build definition is handed to E2B’s template builder so it can be built and published under the project’s template name.

*Call graph*: calls 1 internal fn (apply_layers); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 247–249)

```
def pod_dockerfile() -> str
```

**Purpose**: Creates the Dockerfile text for the Docker version of the same sandbox. This is useful when running the sandbox locally or in a Docker-based carrier instead of through E2B.

**Data flow**: It starts a builder from the public Docker base image, applies the same shared layers used for E2B, and then asks the E2B SDK to render those steps as a Dockerfile. It returns the Dockerfile as plain text.

**Call relations**: `main` calls this directly when the user asks for `--dockerfile`. `build_docker_image` also calls it so Docker can build an image from the generated file.

*Call graph*: calls 1 internal fn (apply_layers); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 252–263)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image from the shared recipe. This lets a Docker-only deployment build the sandbox without needing an E2B account.

**Data flow**: It asks `pod_dockerfile` for Dockerfile text, then sends that text to the local `docker build` command using the repository root as the build context. If Docker reports failure, it stops the script with an error. If the build succeeds, it prints the image tag that was created.

**Call relations**: `main` calls this when the user passes `--build-docker`. It relies on `pod_dockerfile` so the Docker image is generated from the same recipe as the E2B template.

*Call graph*: calls 1 internal fn (pod_dockerfile); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 266–281)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a freshly published E2B template really contains the required tools. It is a safety gate that prevents a broken sandbox image from being accepted as successful.

**Data flow**: It receives the name of an E2B template, starts a sandbox from that template, and runs the readiness command inside it. That command checks for tools such as Python, Node, UFO helper scripts, `s3fs`, PDF tools, LibreOffice, GitHub CLI, and a browser. The sandbox is killed afterward. If the command fails or exits unsuccessfully, the function raises an error; otherwise it returns nothing.

**Call relations**: `main` calls this after building and publishing the default E2B template. It does not build anything itself; it boots the result and proves that the important baked tools are actually present.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 284–305)

```
def check_published_template(name: str) -> None
```

**Purpose**: Checks whether the currently published E2B template is stale compared with the source recipe in this file. It is used as a drift check, meaning it catches when the live sandbox no longer matches the code.

**Data flow**: It receives a template name, computes the expected build fingerprint from the current source, starts a sandbox from the live template, and reads the fingerprint file baked into that sandbox. The sandbox is killed afterward. If the fingerprint is missing or different, it raises an error telling the user to republish; if they match, it returns normally.

**Call relations**: `main` calls this when the user passes `--check`. It calls `build_definition_digest` to know what fingerprint the live template should contain, then uses E2B to inspect the published template without publishing a new one.

*Call graph*: calls 1 internal fn (build_definition_digest); called by 1 (main); 1 external calls (create).


##### `main`  (lines 308–339)

```
def main() -> None
```

**Purpose**: Provides the command-line behavior for this script. It decides whether to print a Dockerfile, build a Docker image, check the live E2B template, or build and publish the E2B template.

**Data flow**: It reads command-line arguments from the user. With `--dockerfile`, it prints the generated Dockerfile. With `--build-docker`, it builds the local Docker image. With `--check`, it compares the live E2B template against the current source and prints an up-to-date message if all is well. With no option, it builds and publishes the E2B template, verifies it by booting it, and prints the resulting template ID.

**Call relations**: This is the top-level traffic controller for the file. Depending on the selected mode, it hands work to `pod_dockerfile`, `build_docker_image`, `check_published_template`, `e2b_template`, and `verify_published_template`; in the default path it also calls E2B’s template build operation.

*Call graph*: calls 5 internal fn (build_docker_image, check_published_template, e2b_template, pod_dockerfile, verify_published_template); 2 external calls (ArgumentParser, build).


### Sandbox Runtime Gates
Validates that the production sandbox can mount the workspace and reach the HTTPS proxy before deployment is accepted.

### `sandbox/mount_gate.py`

`entrypoint` · `deployment pipeline validation`

This script acts like a gate in a deployment pipeline: it proves that a newly deployed environment can give a sandbox a working `/workspace` directory backed by object storage. Without it, a deployment could look healthy while agents later fail because the sandbox cannot mount storage, cannot get credentials, or crashes during normal file use.

The script creates a throwaway workspace name, builds a short-lived probe token, and writes a small marker object into S3. That marker matters because the mount tool expects the workspace prefix to already exist, much like a doorway needing a visible sign before someone can enter.

It then creates an E2B sandbox from the published template, installs the outbound certificate used for secure network traffic, stages the probe token, and runs the same mount preparation commands the real service uses. After mounting, it runs a small shell exercise inside `/workspace`: create a file, list it, read it, change its permissions, delete it, and test a tricky case where a file is deleted while still open. That last case protects against a known class of FUSE/s3fs crashes; FUSE is the layer that lets a remote store behave like a local filesystem.

If anything fails, the script exits with an error so the deployment turns red. It still tries to clean up the sandbox and marker, but it does not let cleanup errors hide the real mount failure.

#### Function details

##### `_mount_gate_recipe`  (lines 85–124)

```
def _mount_gate_recipe(*, bucket: str, region: str, proxy_url: str, ca_cert: str | None, token_secret: str | None, conversation: UUID, now: datetime) -> _MountGateRecipe
```

**Purpose**: Builds the exact set of ingredients needed to mount the sandbox workspace for this gate check. It verifies required secrets are present, creates a temporary access token, and prepares the commands the sandbox must run as root.

**Data flow**: It receives the storage bucket and region, the proxy address for credentials, the certificate and token secret from the environment, a throwaway conversation ID, and the current time. It refuses to continue if the certificate or token secret is missing. It turns the conversation ID and secret into a short-lived token, builds the S3-backed mount command for that workspace prefix, creates the credential endpoint URL, and returns a small recipe object containing the certificate, token, staging command, and root commands to run inside the sandbox.

**Call relations**: The main deploy check calls this first, before any sandbox is created, so it has a complete plan for what to install and run. Inside the recipe it leans on the existing sandbox filesystem helpers to issue the token, choose the workspace storage prefix, build mount scripts, install the token, and create a health-check command. The result is handed back to `main`, which executes those steps in the real sandbox.

*Call graph*: called by 1 (main); 10 external calls (__init__, timedelta, rstrip, issue_sandbox_fs_gate_token, workspace_key_prefix, install_token_command, mount_health_check, mount_scripts, prepare_token_staging_command, s3fs_command).


##### `main`  (lines 127–186)

```
def main() -> None
```

**Purpose**: Runs the mount gate from the command line. It creates the temporary workspace, starts the sandbox, mounts `/workspace`, exercises the mounted filesystem, and reports success or fails the process.

**Data flow**: It reads command-line arguments for the S3 bucket, AWS region, and proxy URL, and reads required secrets from environment variables. It creates a fresh conversation ID, asks `_mount_gate_recipe` for the mount plan, creates an S3 store, writes the workspace marker, and starts an E2B sandbox. It then copies in the certificate and token, runs the mount setup commands, runs the filesystem exercise script, prints the exercise output, and finally kills the sandbox and deletes the marker. On failure, it still tries to clean up but re-raises the original error so the deployment failure is visible.

**Call relations**: This is the top-level flow for the file and is called when the script is run directly. It calls `_mount_gate_recipe` to prepare the mount plan, uses the S3 helper to create and delete the workspace marker, uses E2B to create and control the sandbox, and runs the generated commands inside that sandbox. Its success message is the final proof that the production credential endpoint, S3 access, mount tooling, and sandbox template all worked together.

*Call graph*: calls 1 internal fn (_mount_gate_recipe); 7 external calls (__init__, ArgumentParser, run, now, create, ensure_workspace_marker, uuid4).


### `sandbox/proxy_gate.py`

`entrypoint` · `deployment gate / startup validation`

This script acts like a gate at deployment time: it does not run the main product, but it checks that one important path is working before people rely on it. The path is HTTPS egress from an off-cluster sandbox through a proxy. In plain terms, it asks: “Can a fresh sandbox trust our certificate and contact the proxy correctly?”

The script receives a public proxy URL and reads a certificate from an environment variable. It then creates a temporary E2B sandbox, which is a remote disposable execution environment. Inside that sandbox it writes the certificate file and runs the project’s certificate-install command as root, so HTTPS tools inside the sandbox will trust the proxy’s TLS certificate.

After that, it repeatedly runs `curl` against a known HTTPS API through the proxy. It deliberately uses an invalid run token. Because the token is invalid, success is not a normal API response; success is the proxy replying with the expected HTTP CONNECT status, `403`, meaning “the proxy is reachable and rejected this credential as expected.” While the proxy is still starting, certain connection failures are treated as temporary and retried for several minutes.

Whether the check passes or fails, the temporary sandbox is killed at the end. Without this file, a broken proxy route or missing certificate setup could slip through deployment and only show up later when real sandbox workloads fail to make secure outbound connections.

#### Function details

##### `ProxyTlsGate.run`  (lines 45–109)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy TLS readiness check. It creates a temporary sandbox, installs the certificate inside it, probes the proxy through HTTPS, and only passes when the proxy gives the expected rejection for an invalid token.

**Data flow**: It starts with two pieces of information stored on the `ProxyTlsGate`: the public proxy URL and the certificate text. It checks that the proxy URL is really HTTPS, builds a `curl` command that sends traffic through that proxy, creates a sandbox, writes and installs the certificate there, then runs the probe repeatedly. If the probe returns the expected CONNECT status, it prints a pass message and returns. If the probe gives an unexpected status, malformed output, or keeps failing until the deadline, it raises an error. In all cases, it kills the temporary sandbox before leaving.

**Call relations**: This is called by `main` after command-line input and environment setup are complete. During its work it relies on E2B to create and control the sandbox, `urlsplit` to inspect the proxy URL, `shlex.join` to build a safe shell command, and time functions to retry patiently while the proxy may still be coming up.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 112–119)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the proxy gate. It gathers the proxy URL from the command line and the certificate from the environment, then starts the check.

**Data flow**: It reads `--proxy-url` from the user’s command-line arguments and reads the certificate from the configured environment variable. If the certificate is missing, it stops with an error because the sandbox could not trust the proxy without it. Otherwise it creates a `ProxyTlsGate` with those values and calls its `run` method.

**Call relations**: This function is the top-level driver when the file is run as a script. It uses `argparse.ArgumentParser` to define and read the required argument, then hands control to `ProxyTlsGate.run`, which performs the real sandbox and proxy validation work.

*Call graph*: 2 external calls (__init__, ArgumentParser).
