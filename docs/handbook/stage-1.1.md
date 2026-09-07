# Deployment bundle, sandbox validation, and Alembic runtime wiring  `stage-1.1`

This stage is part of getting UFO ready to run in a real deployment. It happens around build time and deploy time, before the main service work begins. Its job is to package the system, prove the sandbox is safe and consistent, and make sure database upgrades can connect correctly.

The bundle builder creates a self-contained deployment folder, like packing a travel kit with exactly the needed clothes and tools. It freezes the configuration, extension lockfile, runtime package, and sandbox client so another machine can build and run the same version.

The Alembic environment file connects the project’s database schema to Alembic, the tool that applies database structure changes. It tells Alembic where the database is and how to run migrations safely.

The sandbox build script creates the protected environment where UFO runs code. It can build either a cloud template or a local Docker image from the same recipe, keeping both in sync.

The proxy gate script performs a final safety test. It launches a fresh sandbox, installs the trusted certificate, and checks that HTTPS traffic goes through the proxy and is blocked as expected.

## Files in this stage

### Deployment bundle assembly
Builds the frozen deployment context that carries configuration, extension locks, runtime artifacts, and sandbox client code.

### `core/src/ufo/bundle.py`

`orchestration` · `build/package time`

This file is about making a deployment repeatable. Instead of relying on whatever extensions, config, or local files happen to exist on a machine at runtime, it writes out a small, pinned package of everything the Docker image needs. Think of it like packing a lunchbox: the runtime should not have to visit the store later and guess what to buy.

The main class, `Bundle`, takes a config file, an optional extension catalog, an output folder, the built `ufo` wheel file, and the sandbox client binary. When `build` runs, it creates the output folder, copies the config, writes a fresh lockfile, copies the client binary, and writes a Dockerfile.

The most important work is choosing and verifying extension pins. A “pin” records an extension name, version, and content digest, where the digest is a fingerprint of the exact files that will be installed. If there is already a lockfile, the bundle keeps those selected extensions. If not, it pins all currently discovered extensions. It also adds any catalog extensions marked as disabled, because those are “bundle-only”: they are installed into the image at bundle time, not fetched later at runtime.

The Dockerfile then describes how to build an image from a slim Python base, install the `ufo` wheel, copy in the config and lockfile, install the sandbox client, and start `ufoctl serve` by default.

#### Function details

##### `wheel_name`  (lines 35–37)

```
def wheel_name() -> str
```

**Purpose**: This function returns the expected filename of the `ufo` Python wheel that will be copied into the bundle. A wheel is a packaged Python distribution, similar to an installable archive.

**Data flow**: It reads the current `ufo` version from the extension store helper, puts that version into the standard wheel filename pattern, and returns the resulting string, such as a versioned `ufo-...-py3-none-any.whl` name.

**Call relations**: The Dockerfile builder calls this when writing the Dockerfile, so the image recipe knows exactly which wheel filename to copy and install.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 61–81)

```
def build(self) -> BundleResult
```

**Purpose**: This is the main action for creating a bundle. It gathers the extension pins, writes the bundle files into the output folder, and returns a summary of what it produced.

**Data flow**: It starts with the bundle inputs stored on the `Bundle`: the source config path, output folder, wheel path, sandbox client binary, and optional catalog. It asks `_pins` for the frozen extension list, creates the output folder, copies the config text, writes a JSON lockfile with the current `ufo` version and pins, copies the sandbox client bytes, writes the Dockerfile text from `_dockerfile`, and returns a `BundleResult` pointing to all produced files.

**Call relations**: This is the public build step that ties the file together. It relies on `_pins` to decide what extensions the artifact must contain, relies on `_dockerfile` to create the Docker image recipe, and packages the results into `BundleResult` for the caller to inspect or report.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 83–127)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: This function decides which extensions belong in the bundle and records a fingerprint of the exact extension files inside the built wheel. This is what makes the final image predictable instead of depending on local source files or later downloads.

**Data flow**: It first discovers extensions currently installed in the environment. If an existing lockfile is present, it uses that lockfile’s extension names as the starting set; otherwise it starts with all discovered extensions. If a catalog is available, it also adds catalog entries marked disabled, because those are meant to be included only during bundling. For each final extension name, it confirms the extension is installed, finds the matching package files inside the wheel, skips cache and compiled bytecode files, computes a content digest from those files, and creates an `ExtensionPin` with the extension name, version, and digest. It returns all pins as an immutable tuple. If an expected extension or package is missing, it raises an error instead of producing an incomplete bundle.

**Call relations**: `Bundle.build` calls this before writing the lockfile. It uses extension loader helpers to discover installed extensions, read any current lockfile, locate the lockfile path, and compute file digests. The pins it returns are then written into the bundled lockfile, which the runtime can later use to narrow the active extension set and verify the installed contents.

*Call graph*: called by 1 (build); 7 external calls (__init__, Path, discovered, extension_content_digest, lockfile_path, read_lockfile, ZipFile).


##### `Bundle._dockerfile`  (lines 129–145)

```
def _dockerfile(self) -> str
```

**Purpose**: This function writes the text of the Dockerfile used to turn the bundle folder into a runnable container image. The Dockerfile is the recipe Docker follows when building the image.

**Data flow**: It combines fixed bundle names, the base Python image, the generated wheel filename, config and lockfile locations, and the sandbox client install path into a sequence of Dockerfile lines. The result is one string that installs the `ufo` wheel, copies the frozen config and lockfile, installs the sandbox client as an executable, sets environment variables, and starts `ufoctl serve` by default.

**Call relations**: `Bundle.build` calls this after copying the bundle inputs, then writes the returned text to `Dockerfile` in the output folder. It calls `wheel_name` so the Dockerfile refers to the same versioned wheel filename that the bundle expects.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### Alembic runtime wiring
Connects Alembic to the project database and SQLAlchemy metadata so schema migrations can run safely.

### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `schema migration`

When the project needs to create or update database tables, Alembic runs this file. Think of it like the stage manager for a database upgrade: it opens the right database connection, points Alembic at the project’s table definitions, and then tells Alembic to run the pending migration scripts.

The file uses SQLAlchemy’s asynchronous engine, which means the database connection is opened using Python’s async style rather than blocking the whole program while waiting. Once connected, it switches into a normal synchronous section because Alembic’s migration operations expect that style. That handoff happens through SQLAlchemy’s `run_sync`, which is a safe way to run ordinary migration code on an async connection.

The important project-specific piece is `metadata` from `ufo.schema.tables`. Metadata is SQLAlchemy’s map of what the database tables should look like. Alembic uses it to compare or apply schema changes. There is also special behavior for SQLite: migrations are rendered “as batch,” which is a workaround for SQLite’s limited ability to alter existing tables.

Without this file, Alembic would not know how to connect to the app’s database or what schema it is supposed to maintain.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function prepares Alembic to run migrations on an already-open database connection. It gives Alembic the connection, the project’s table metadata, and a SQLite-safe mode when needed.

**Data flow**: It receives a live SQLAlchemy `Connection`. It reads the database type from that connection, configures Alembic with the connection and the project schema metadata, starts a migration transaction, and then runs the migrations. It does not return a value; its effect is that the database schema is updated inside the transaction.

**Call relations**: The async setup function `run` opens the database connection first, then asks SQLAlchemy to call `run_migrations` in the synchronous style Alembic expects. Inside that moment, `run_migrations` hands control to Alembic by configuring the context, opening a transaction, and triggering the actual migration run.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This function creates the database engine from Alembic’s configuration, opens a connection, runs the migration work, and then cleans up the engine. It is the top-level async workflow for this migration environment.

**Data flow**: It reads Alembic’s configured settings, especially the `sqlalchemy.` database connection options. From those settings it builds an async SQLAlchemy engine, opens a database connection, passes that connection into `run_migrations`, and finally disposes of the engine so resources are released. It returns nothing; its visible result is that migrations have been attempted against the configured database.

**Call relations**: At the bottom of the file, `asyncio.run(run())` starts this function when Alembic loads the environment. `run` performs the async connection setup, then hands the actual migration step to `run_migrations` through SQLAlchemy’s `run_sync` bridge.

*Call graph*: 1 external calls (async_engine_from_config).


### Sandbox image validation
Builds the sandbox image from a shared recipe and verifies the deployed proxy HTTPS behavior before use.

### `sandbox/build_template.py`

`entrypoint` · `build and deploy time`

A sandbox is the prepared machine where UFO can run tools, inspect files, use browsers, convert documents, and execute the compiled `ufo` helper program. This file is the recipe and command-line tool for making that machine. Without it, different deployments could end up with missing tools, stale binaries, or mismatched cloud and Docker environments.

The file defines the shared ingredients: operating-system packages, Python packages, Node packages, browser support, environment variables, the compiled UFO client binary, and the bundled system skills. It then applies those ingredients to two different bases. For E2B, it starts from E2B’s code-interpreter template. For Docker, it starts from the public image that template is based on and renders a Dockerfile.

A key idea here is the build digest, like a fingerprint on a sealed box. The script hashes the source recipe and important baked content, writes that digest into the image, and later reads it back to detect whether the published template is stale. Publishing also boots the finished sandbox and runs a readiness probe, so a template missing a required tool fails immediately instead of quietly reaching production.

#### Function details

##### `template_name`  (lines 269–270)

```
def template_name(size: str) -> str
```

**Purpose**: Creates the published E2B template name for a sandbox size such as small, medium, or large. This keeps naming consistent everywhere the script builds or checks a size-specific template.

**Data flow**: It receives a size word. It joins that word to the fixed base name `ufo-sbx`. It returns the full template name, for example a name ending in `-small`.

**Call relations**: When the script builds or checks E2B templates, `main` and `_built` ask this helper for the exact name to use, so they do not duplicate the naming rule.

*Call graph*: called by 2 (_built, main).


##### `client_definition`  (lines 273–299)

```
def client_definition() -> dict[str, str]
```

**Purpose**: Builds the fingerprint data for the compiled `ufo` client that will be baked into the sandbox. It hashes the client source files rather than the compiled binary, because compiled release binaries may differ byte-for-byte between machines even when the source is the same.

**Data flow**: It reads selected files and source directories from the client crate, feeds their relative paths and bytes into a SHA-256 hash, and combines that hash with the client binary name and target platform. It returns a small dictionary describing the client input that the image depends on.

**Call relations**: `build_definition_digest` calls this when making the overall build fingerprint. That means a real client source change makes the sandbox definition look changed, while harmless machine-specific binary differences do not.

*Call graph*: called by 1 (build_definition_digest); 1 external calls (sha256).


##### `stage_client_binary`  (lines 302–313)

```
def stage_client_binary() -> Path
```

**Purpose**: Copies the compiled `ufo` client binary into a known artifacts directory so the image build can include it. This prevents the build from accidentally using an old or hidden binary.

**Data flow**: It asks `client_binary` where the correct compiled client is, creates the staging directory if needed, copies the file there, marks it executable, and returns the staged path.

**Call relations**: `main` uses this before publishing E2B templates, and `build_docker_image` uses it before a Docker build. Later, `apply_layers` refers to that staged path when it adds the binary to the image.

*Call graph*: called by 2 (build_docker_image, main); 2 external calls (copyfile, client_binary).


##### `system_skill_bundle`  (lines 317–334)

```
def system_skill_bundle() -> SystemSkillBundle
```

**Purpose**: Collects all built-in system skills and packages them into one reusable bundle. A skill is a packaged capability the sandbox can expose, such as document or media tooling.

**Data flow**: It searches the repository for `SKILL.md` files, ignores anything under `node_modules`, finds top-level skill folders, discovers the skills in those folders, and turns them into a `SystemSkillBundle`. Because it is cached, repeated calls reuse the same bundle in one run.

**Call relations**: `stage_system_skills` calls this to write the bundle archive, and `build_definition_digest` calls it to include the bundle’s digest in the image fingerprint. This ties the built image to the exact skill content.

*Call graph*: calls 1 internal fn (from_skills); called by 2 (build_definition_digest, stage_system_skills); 1 external calls (discover_skills).


##### `stage_system_skills`  (lines 337–340)

```
def stage_system_skills() -> Path
```

**Purpose**: Writes the system skill bundle into the build artifacts directory so it can be copied into the sandbox image. This makes the sandbox carry the same built-in skills the host discovered from the repository.

**Data flow**: It creates the artifacts directory if needed, gets the cached skill bundle, writes the bundle archive bytes to `system-skills.zip`, and returns that path.

**Call relations**: `main` and `build_docker_image` run this before any build that needs real image files. `apply_layers` later copies and extracts this archive inside the image.

*Call graph*: calls 1 internal fn (system_skill_bundle); called by 2 (build_docker_image, main).


##### `build_definition_digest`  (lines 343–385)

```
def build_definition_digest(sizing: Sizing | None) -> str
```

**Purpose**: Creates the overall fingerprint of the sandbox definition. It is used to tell whether a live published template still matches the source recipe in this file.

**Data flow**: It receives either a sandbox size configuration or `None` for Docker. It gathers the base image/template, users, commands, packages, environment, runtime paths, client source fingerprint, skill bundle digest, module file hashes, and optional CPU and memory values. It serializes that information in a stable order, hashes it, and returns a `sha256:` digest string.

**Call relations**: `e2b_template` and `pod_dockerfile` use this before applying layers so the digest can be baked into the image. `main` also uses it during `--check` to compare current source expectations with the digest found inside the live template.

*Call graph*: calls 2 internal fn (client_definition, system_skill_bundle); called by 3 (e2b_template, main, pod_dockerfile); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 388–433)

```
def apply_layers(builder: TemplateBuilder, digest: str) -> TemplateFinal
```

**Purpose**: Adds the shared UFO sandbox setup to an E2B template builder or Docker image builder. This is the heart of the single recipe that keeps cloud and Docker images aligned.

**Data flow**: It receives a builder and a digest string. It switches to the build user, installs system tools, Python packages, Node packages, GitHub CLI, Node, and Playwright’s browser, creates needed directories, writes the build digest, sets environment variables, copies and unpacks system skills, copies the `ufo` client and helper module, then switches back to the runtime user and sets the long-running start command plus readiness check. It returns the finalized template definition.

**Call relations**: Both `e2b_template` and `pod_dockerfile` call this after choosing their base. It hands off all actual image-layer instructions to the E2B SDK builder methods, so one set of steps feeds both publishing paths.

*Call graph*: called by 2 (e2b_template, pod_dockerfile); 5 external calls (copy, run_cmd, set_envs, set_start_cmd, set_user).


##### `e2b_template`  (lines 436–438)

```
def e2b_template(size: str) -> TemplateFinal
```

**Purpose**: Creates the full E2B template definition for one sandbox size. It chooses the E2B base template and applies the shared UFO layers with the correct size-specific digest.

**Data flow**: It receives a size name, creates an E2B template builder rooted at the repository, computes the digest using that size’s CPU and memory values, applies the shared layers, and returns the final template object.

**Call relations**: `_built` calls this right before asking E2B to build and publish a size-specific template. It is the E2B branch of the shared image recipe.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 1 (_built); 1 external calls (Template).


##### `pod_dockerfile`  (lines 441–443)

```
def pod_dockerfile() -> str
```

**Purpose**: Renders the Docker version of the sandbox image recipe as a Dockerfile. This lets Docker deployments use the same layers as the E2B template without needing an E2B account just to produce the file.

**Data flow**: It creates a builder from the public Docker base image, computes a digest with no fixed E2B sizing, applies the shared layers, converts the result to Dockerfile text, and returns that text.

**Call relations**: `main` calls this for `--dockerfile`, and `build_docker_image` calls it before running `docker build`. It is the Docker branch of the shared image recipe.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 446–459)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker image used by the Docker sandbox carrier. It is meant for deployments that need Docker but not E2B publishing.

**Data flow**: It stages the compiled client and system skills, renders the Dockerfile text, sends that Dockerfile to `docker build` through standard input, uses the repository root as the build context, and tags the resulting image. If Docker reports failure, it stops the script with an error; otherwise it prints the image tag.

**Call relations**: `main` runs this when the user passes `--build-docker`. Inside, it relies on `stage_client_binary`, `stage_system_skills`, and `pod_dockerfile` to prepare the exact inputs Docker needs.

*Call graph*: calls 3 internal fn (pod_dockerfile, stage_client_binary, stage_system_skills); called by 1 (main); 1 external calls (run).


##### `_booted`  (lines 462–479)

```
def _booted(name: str) -> Sandbox
```

**Purpose**: Starts a temporary E2B sandbox from a named template, with retries for temporary E2B service or network problems. It prevents a flaky remote API call from being mistaken for a bad template on the first try.

**Data flow**: It receives a template name. It tries to create a sandbox with a readiness timeout, and if the connection or E2B service fails, it prints a warning, waits longer on each retry, and tries again. It returns the created sandbox, or raises an error after all attempts fail.

**Call relations**: `verify_published_template` and `check_published_template` use this before running their checks. It hands them a live sandbox box to inspect, while hiding retry details from the check logic.

*Call graph*: called by 2 (check_published_template, verify_published_template); 2 external calls (create, sleep).


##### `_reap`  (lines 482–492)

```
def _reap(sandbox: Sandbox, name: str) -> None
```

**Purpose**: Stops a temporary E2B sandbox after a verification or drift check. If cleanup itself fails, it reports that fact but does not replace the result of the check.

**Data flow**: It receives a sandbox object and its name. It asks E2B to kill the sandbox. If the network or E2B service fails during cleanup, it writes a warning to standard error and lets the sandbox expire naturally.

**Call relations**: `verify_published_template` and `check_published_template` call this in their cleanup path after `_booted` has created a sandbox. It is deliberately separate so both checks clean up in the same safe way.

*Call graph*: called by 2 (check_published_template, verify_published_template); 1 external calls (kill).


##### `verify_published_template`  (lines 495–510)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a freshly published E2B template can actually boot and has the expected tools installed. This is the publish gate that catches missing runtime pieces immediately.

**Data flow**: It receives a published template reference, boots a sandbox from it, runs the readiness shell command inside the sandbox, then always tries to stop the sandbox. If the command fails or exits unsuccessfully, it raises an error saying the template is missing baked runtime tools.

**Call relations**: After `_built` publishes each size, `main` calls this before reporting success. It uses `_booted` to get a sandbox and `_reap` to clean it up.

*Call graph*: calls 2 internal fn (_booted, _reap); called by 1 (main).


##### `check_published_template`  (lines 513–533)

```
def check_published_template(name: str, expected: str) -> None
```

**Purpose**: Checks whether an already-published E2B template matches the current source definition, without publishing anything. This is the drift gate: it tells CI or a developer when the live template is stale.

**Data flow**: It receives a template name and the digest expected from current source. It boots the template, reads the digest file baked inside it, cleans up the sandbox, and compares the live digest to the expected one. If the file is missing or the value differs, it raises an error asking for a republish.

**Call relations**: `main` calls this for every sandbox size when `--check` is passed. It depends on `_booted` and `_reap` for the temporary sandbox lifecycle, and on `build_definition_digest` from `main` for the expected value.

*Call graph*: calls 2 internal fn (_booted, _reap); called by 1 (main).


##### `_built`  (lines 536–558)

```
def _built(size: str, sizing: Sizing) -> BuildInfo
```

**Purpose**: Builds and publishes one E2B sandbox template tier, retrying temporary build-service failures. A tier is one size choice, such as small or large.

**Data flow**: It receives a size name and its CPU and memory settings. It creates the E2B template definition, asks E2B to build it under the correct published name with those resources, and returns the build information. If E2B times out or cannot be reached, it waits and retries before giving up.

**Call relations**: `main` calls this once per configured sandbox tier during normal publishing. It uses `e2b_template` to define what to build and `template_name` to decide where to publish it.

*Call graph*: calls 2 internal fn (e2b_template, template_name); called by 1 (main); 2 external calls (build, sleep).


##### `main`  (lines 561–599)

```
def main() -> None
```

**Purpose**: Provides the command-line behavior for this script. It decides whether to print a Dockerfile, build a Docker image, check live E2B templates, or publish and verify E2B templates.

**Data flow**: It reads command-line flags. With `--dockerfile`, it writes the rendered Dockerfile to standard output. With `--build-docker`, it builds the local Docker image. With `--check`, it computes expected digests for every size and checks the live templates. With no special flag, it stages artifacts, builds each E2B tier, verifies each published template, and prints the resulting references.

**Call relations**: This is the top-level driver called when the file is run as a script. It coordinates the helper functions: staging files, rendering definitions, building templates, checking drift, verifying readiness, and printing the final result.

*Call graph*: calls 9 internal fn (_built, build_definition_digest, build_docker_image, check_published_template, pod_dockerfile, stage_client_binary, stage_system_skills, template_name, verify_published_template); 1 external calls (ArgumentParser).


### `sandbox/proxy_gate.py`

`entrypoint` · `deploy-time validation`

This script answers a practical question before deployment is trusted: “Can a sandbox reach the outside world through our TLS proxy, and does the proxy enforce access correctly?” It creates an E2B sandbox, which is a temporary remote development box, then installs the certificate authority certificate that lets the sandbox trust the proxy’s TLS certificate. A certificate authority, or CA, is the trusted signer that tells software “this encrypted connection is really who it claims to be.”

After setup, the script runs a small Python probe inside the sandbox. The probe tries to open an HTTPS connection to Anthropic’s API while all proxy environment variables point at the sandbox proxy. It deliberately uses an invalid run token as the proxy username. That sounds wrong, but it is the test: if the request reaches the proxy correctly, the proxy should reject the CONNECT request with HTTP 403. In other words, a 403 is success here, like a locked door proving the guard is awake.

If the proxy is not ready yet, the probe may report a temporary “pending” network-style failure, so the script retries for several minutes. Any other result is treated as a deployment failure. The sandbox is then killed, but if cleanup cannot reach it, the script relies on E2B’s timeout to expire it later.

#### Function details

##### `ProxyTlsGate.run`  (lines 72–130)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy gate check. It creates a temporary sandbox, installs the CA certificate, sends a test HTTPS request through the proxy, and only passes if the proxy returns the expected 403 rejection for an invalid token.

**Data flow**: It starts with three stored values: the public proxy URL, the CA certificate text, and the sandbox template name. It parses the proxy URL, builds a proxy address containing a deliberately invalid run token, and prepares a command that will run a Python network probe inside the sandbox. It creates the sandbox, writes the CA certificate into it, runs the CA install command, then repeatedly runs the probe until it sees the expected 403, times out, or sees an unexpected status. On success it prints a pass message and returns. On failure it raises an error explaining what status was seen. In all cases it tries to kill the sandbox afterward, and if that cleanup request itself fails, it prints a warning and lets the sandbox expire by timeout.

**Call relations**: This is the worker step used after the command-line setup has gathered the proxy URL, certificate, and template. Inside the step it calls out to URL parsing to validate the proxy address, shell quoting to build a safe command string, E2B sandbox creation to get a temporary test machine, and timing helpers to retry while the proxy is still coming up. It hands the final verdict back by either returning normally for pass or raising an exception for fail.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 133–144)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the gate. It reads the required proxy URL argument and required environment variables, chooses the sandbox template, and starts the gate check.

**Data flow**: It receives command-line input through argparse, especially the required --proxy-url value. It reads the CA certificate and E2B template configuration from environment variables, and stops immediately with a clear error if either is missing. It converts the template configuration into a template lookup, selects the sandbox size this project expects, creates a ProxyTlsGate object with those values, and runs it. The output is either the gate’s pass message or an exception that fails the script.

**Call relations**: This is the front door of the file when it is run as a script. It uses argparse to turn command-line text into structured input, uses sandbox_templates to interpret the template environment setting, then passes the prepared values into ProxyTlsGate so the real sandbox-and-proxy test can happen.

*Call graph*: 3 external calls (__init__, ArgumentParser, sandbox_templates).
