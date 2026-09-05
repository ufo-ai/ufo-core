# Runtime bundle and sandbox image gates  `stage-1.1`

This stage is part of getting UFO ready to run safely outside a developer’s machine. It prepares the “runtime bundle,” which is like a packed travel kit containing the exact code, settings, allowed extensions, sandbox client program, and Docker instructions needed to reproduce the same runtime elsewhere.

core/src/ufo/bundle.py is the packer. It creates the folder used by ufoctl bundle, including the runtime wheel, pinned configuration, extension lockfile, sandbox client binary, and Dockerfile. core/src/ufo/harness/sandbox/client_binary.py is the locator for that client binary. It does not build the program; it checks that a suitable one already exists and gives a clear fix if it does not.

The sandbox scripts guard the environment where untrusted code runs. sandbox/build_template.py builds both the hosted E2B sandbox template and the local Docker image from the same recipe, keeping them matched. sandbox/proxy_gate.py is a safety test before deployment: it launches a temporary sandbox, installs the proxy certificate, and confirms HTTPS proxy behavior fails in the expected controlled way.

## Files in this stage

### Runtime bundle assembly
Prepares the deployable UFO runtime bundle and locates the prebuilt sandbox client binary that must be included in it.

### `core/src/ufo/bundle.py`

`orchestration` · `bundle command execution`

This file solves the problem of making a UFO deployment repeatable. Instead of relying on whatever extensions or source files happen to be on a developer’s machine, it writes down exactly what should be installed and checks the actual bytes that will go into the image. Think of it like packing a lunchbox with a checklist: the Docker image gets only the listed items, and each item is checked before use.

The main `Bundle` object takes five inputs: the existing config file, an optional extension catalog, an output folder, the built UFO Python wheel, and the sandbox client binary. When `build` runs, it first decides which extensions must be pinned. It starts from the current lockfile if one exists, otherwise from the extensions discovered in the local environment. If a catalog is available, it also adds entries marked as disabled, because those are “bundle-only” extensions that should be installed into the image but not discovered later at runtime.

For each extension, `_pins` opens the built wheel file, finds the extension’s package files inside it, ignores cache files, and computes a digest, meaning a fingerprint of the file contents. The resulting lockfile says both which extension versions are present and what their contents should be. Finally, `build` copies the config and client binary, writes the new lockfile, and writes a Dockerfile that installs the wheel and starts `ufoctl serve`.

#### Function details

##### `wheel_name`  (lines 35–37)

```
def wheel_name() -> str
```

**Purpose**: Builds the expected filename for the UFO Python wheel that will be copied into the Docker image. It uses the current UFO version so the Dockerfile refers to the exact wheel produced for this release.

**Data flow**: It reads the current UFO version from the extension store helper, places that version into the standard Python wheel filename format, and returns the resulting string, such as a `ufo-...-py3-none-any.whl` name.

**Call relations**: When `Bundle._dockerfile` writes the Dockerfile text, it asks this helper for the wheel filename so the `COPY` and `pip install` lines match the artifact the bundle expects.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 61–81)

```
def build(self) -> BundleResult
```

**Purpose**: Creates the bundle folder on disk. It gathers extension pins, copies the deploy config and sandbox client, writes a new lockfile, writes the Dockerfile, and returns a summary of what it produced.

**Data flow**: It starts with the `Bundle` fields: paths to the source config, output folder, wheel, and client binary, plus the optional catalog. It asks `_pins` for the fixed extension list, creates the output folder, writes `ufo.toml`, writes `ufo.lock` with the current UFO version and extension fingerprints, copies the sandbox client binary, writes the Dockerfile text from `_dockerfile`, and returns a `BundleResult` containing the output paths and pins.

**Call relations**: This is the main action for the file. A higher-level bundle command would call it when the user asks to create a deploy artifact. Inside that flow, it delegates the careful extension fingerprinting to `_pins` and the container recipe text to `_dockerfile`, then packages their results into a `BundleResult` for the caller.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 83–127)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Decides exactly which extensions belong in the bundle and records a content fingerprint for each one. This prevents the final image from silently using a different extension than the one that was bundled.

**Data flow**: It reads the extensions currently discovered in the local environment and looks for an existing lockfile. If a lockfile exists, its extension names are the starting list; otherwise, all discovered extensions are used. It then adds any catalog entries marked disabled, because those are intended to be included only at bundle time. For each extension name, it confirms the extension is installed, opens the built wheel, extracts the matching package files while skipping cache and compiled bytecode files, computes a digest from those files, and returns a tuple of `ExtensionPin` records. If an expected extension or package is missing, it raises an error instead of creating an unsafe bundle.

**Call relations**: This function is called by `Bundle.build` before any lockfile is written. It is the quality-control step: it consults discovery and lockfile helpers, inspects the wheel through `ZipFile`, and hands back pins that `build` writes into the bundled `ufo.lock`.

*Call graph*: called by 1 (build); 7 external calls (__init__, Path, discovered, extension_content_digest, lockfile_path, read_lockfile, ZipFile).


##### `Bundle._dockerfile`  (lines 129–145)

```
def _dockerfile(self) -> str
```

**Purpose**: Writes the text of the Dockerfile used to turn the bundle folder into a runnable container image. The Dockerfile installs UFO, copies the locked config, installs the sandbox client, and starts the server command by default.

**Data flow**: It uses fixed bundle filenames and the wheel filename from `wheel_name`, then assembles Dockerfile lines into one string. The output text says to start from a Python 3.12 slim image, work in `/app`, set environment variables for the config and lockfile, install the wheel with `pip`, copy the config and lockfile, copy the sandbox client to `/usr/local/bin`, point UFO at that client, and run `ufoctl serve` unless another command is supplied.

**Call relations**: This function is called by `Bundle.build` near the end of bundle creation. It does not write files itself; it only produces the recipe text, and `build` saves that text as the bundle’s `Dockerfile`.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### `core/src/ufo/harness/sandbox/client_binary.py`

`config` · `startup`

A sandbox needs the `ufo` command-line program available inside it, much like a toolbox needs the right wrench before work can begin. This file is the shared place that answers the question: “Where is that `ufo` program?”

It checks a few sensible locations in order. First, it looks for an environment variable called `UFO_CLIENT_BINARY`. An environment variable is a setting passed in from the outside, often used by continuous integration systems to point at a prebuilt artifact. If that setting is present, this file trusts it, but verifies that it names a real file.

If no override is given, it looks in the Rust client project’s build output directory. Rust build outputs are usually placed under `target`, with separate folders for build profiles such as `release` and `debug`. If the caller asks for a specific target platform, it looks under that platform’s target folder. This matters because the machine creating a sandbox image may not be the same kind of machine that will run inside the sandbox.

For host-only use, it also checks whether `ufo` is installed somewhere on the current command path. If none of these options works, it raises an error explaining exactly which `cargo build` command can create the missing binary.

#### Function details

##### `client_binary`  (lines 32–59)

```
def client_binary(target: str | None=None) -> Path
```

**Purpose**: Finds the `ufo` executable file that should be used for a sandbox or local subprocess. It supports both the current machine and an optional Rust target platform, and it refuses to silently build anything during a run.

**Data flow**: It receives an optional target platform string. It first reads the `UFO_CLIENT_BINARY` environment setting; if present, it turns that text into a file path and returns it only if the file exists. If there is no override, it searches the client build output folders for `release` and then `debug` binaries, using the target-specific directory when a target was requested. If no target was requested, it also asks the operating system whether `ufo` is available on the command path. If all checks fail, it raises a `RuntimeError` with a build command the user can run.

**Call relations**: This function is the single answer used by sandbox setup code, local carrier code, and tests that need a real `ufo` binary. Inside its search, it uses `pathlib.Path` to build and inspect file paths, and `shutil.which` to ask the operating system whether a host-installed `ufo` command exists.

*Call graph*: 2 external calls (Path, which).


### Sandbox image gates
Builds consistent hosted and local sandbox images, then verifies deploy-time proxy behavior with a safety gate.

### `sandbox/build_template.py`

`entrypoint` · `build and deploy time`

This file is the build recipe and safety gate for UFO's sandbox. A sandbox is the isolated computer where agent-created code and tools run, rather like a disposable workshop stocked with the right tools before each job. Without this file, the hosted E2B sandbox and the Docker version could be built differently, miss required programs, or keep running an old setup without anyone noticing.

The script defines what must be baked into the image: system packages such as Git, Chromium, LibreOffice, PDF tools, and ffmpeg; Python and Node packages used by skills; the compiled `ufo` client command; the system skill bundle; environment variables; permissions; and a small digest file that records exactly what definition produced the image.

It has several modes. With no arguments, it stages the compiled client and skills, builds one E2B template per sandbox size, boots each published template, and runs a readiness check inside it. With `--check`, it boots the live templates and compares their baked digest to the current source recipe, failing if they are stale. With `--dockerfile`, it prints the Dockerfile for the Docker carrier. With `--build-docker`, it renders that Dockerfile and asks the local Docker daemon to build the image. The important design idea is “one recipe, two targets”: E2B and Docker differ only in their base image, while the layers above stay synchronized.

#### Function details

##### `template_name`  (lines 269–270)

```
def template_name(size: str) -> str
```

**Purpose**: Builds the official E2B template name for a given sandbox size, such as small, medium, or large. This gives every size tier its own published template.

**Data flow**: It receives a size name as text, adds it to the shared base name `ufo-sbx`, and returns the combined template name. It does not read or change anything else.

**Call relations**: When publishing templates, `_built` uses this name to tell E2B what to build. During `--check`, `main` uses the same naming rule so it checks the live template for each size tier.

*Call graph*: called by 2 (_built, main).


##### `client_definition`  (lines 273–299)

```
def client_definition() -> dict[str, str]
```

**Purpose**: Describes the baked `ufo` client in a stable way for the build digest. Instead of hashing the compiled binary, which can vary between machines, it hashes the source files that produce that binary.

**Data flow**: It reads selected files from the client crate, feeds their relative paths and bytes into a SHA-256 hash, and returns a small dictionary containing the client command name, target platform, and source hash. Nothing is written to disk.

**Call relations**: The build digest function calls this when deciding whether the sandbox definition has changed. That lets the drift check notice real client source changes without being confused by harmless binary build differences.

*Call graph*: called by 1 (build_definition_digest); 1 external calls (sha256).


##### `stage_client_binary`  (lines 302–313)

```
def stage_client_binary() -> Path
```

**Purpose**: Copies the compiled `ufo` client into the sandbox build context so Docker or E2B can include it in the image. This avoids compiling Rust inside the sandbox image, which would make builds much slower and larger.

**Data flow**: It asks the client build helper where the compiled binary is, creates the staging directory if needed, copies the binary to a known artifact path, marks it executable, and returns that path.

**Call relations**: The Docker build path and the normal E2B publish path call this before they build. Later, `apply_layers` refers to the staged path when adding the `ufo` command to the image.

*Call graph*: called by 2 (build_docker_image, main); 2 external calls (copyfile, client_binary).


##### `system_skill_bundle`  (lines 317–334)

```
def system_skill_bundle() -> SystemSkillBundle
```

**Purpose**: Collects all built-in system skills and packages them as one bundle object. These skills are the reusable tool instructions and code that the sandbox needs available at runtime.

**Data flow**: It searches the repository for `SKILL.md` files in the core, extensions, and packs areas, ignores anything under `node_modules`, finds the top-level skill folders, discovers the skills, and returns a `SystemSkillBundle`. The result is cached, so repeated calls reuse the same bundle.

**Call relations**: The digest builder calls this to include the skill bundle's identity in the build definition. The staging function calls it to write the actual bundle archive into the build context.

*Call graph*: calls 1 internal fn (from_skills); called by 2 (build_definition_digest, stage_system_skills); 1 external calls (discover_skills).


##### `stage_system_skills`  (lines 337–340)

```
def stage_system_skills() -> Path
```

**Purpose**: Writes the system skill bundle archive into the sandbox build artifacts directory. This makes the skills available for the image build to copy and unpack.

**Data flow**: It ensures the artifact directory exists, gets the cached system skill bundle, writes the bundle archive bytes to a fixed zip path, and returns that path.

**Call relations**: Both the Docker image build and the normal E2B publish path call this before building. `apply_layers` later copies this archive into the image and extracts it into the system skills location.

*Call graph*: calls 1 internal fn (system_skill_bundle); called by 2 (build_docker_image, main).


##### `build_definition_digest`  (lines 343–385)

```
def build_definition_digest(sizing: Sizing | None) -> str
```

**Purpose**: Creates a fingerprint of the sandbox build recipe. This fingerprint is baked into the image so later checks can tell whether the live template matches the current source definition.

**Data flow**: It receives either a sizing choice or `None` for Docker, gathers the base template, users, commands, package lists, environment, runtime paths, client source hash, system skill digest, and baked module hashes, serializes that information in a consistent order, and returns a SHA-256 digest string.

**Call relations**: The E2B and Docker recipe builders call this before applying layers, so the image records the definition it came from. `main` also calls it during `--check` to compare today's expected digest with the digest read from a live template.

*Call graph*: calls 2 internal fn (client_definition, system_skill_bundle); called by 3 (e2b_template, main, pod_dockerfile); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 388–433)

```
def apply_layers(builder: TemplateBuilder, digest: str) -> TemplateFinal
```

**Purpose**: Applies the shared sandbox recipe to a template builder. This is the central place where the image is stocked with tools, files, environment variables, permissions, and its readiness command.

**Data flow**: It receives a template builder and a digest string. It adds commands to install system tools, GitHub CLI, Node, Python packages, npm packages, and Playwright's browser; creates required directories; writes the digest file; sets environment variables; copies in the system skills archive, client binary, and helper modules; fixes permissions; switches to the runtime user; and returns the finalized template definition.

**Call relations**: Both `e2b_template` and `pod_dockerfile` pass their builder through this function. That is how the hosted E2B template and Docker image stay aligned even though they start from different base images.

*Call graph*: called by 2 (e2b_template, pod_dockerfile); 5 external calls (copy, run_cmd, set_envs, set_start_cmd, set_user).


##### `e2b_template`  (lines 436–438)

```
def e2b_template(size: str) -> TemplateFinal
```

**Purpose**: Creates the E2B version of the sandbox template for one size tier. It starts from E2B's code-interpreter template and applies UFO's shared layers on top.

**Data flow**: It receives a size name, creates an E2B template builder rooted at the repository, calculates the digest for that size's CPU and memory allocation, applies the shared layers, and returns the final template definition.

**Call relations**: _built calls this when it is time to publish a specific size tier to E2B. It hands the result to E2B's build API.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 1 (_built); 1 external calls (Template).


##### `pod_dockerfile`  (lines 441–443)

```
def pod_dockerfile() -> str
```

**Purpose**: Renders the Docker version of the sandbox recipe as a Dockerfile. This lets local Docker builds use the same layers as the hosted E2B build.

**Data flow**: It creates a template builder from the public Docker base image, computes a digest with no E2B sizing attached, applies the shared layers, converts the final template to Dockerfile text, and returns that text.

**Call relations**: `main` calls this directly for `--dockerfile`, and `build_docker_image` calls it before running `docker build`. It shares `apply_layers` with the E2B path to prevent recipe drift.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 446–459)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image from the shared recipe. This is for deployments that use Docker instead of E2B and does not require an E2B account.

**Data flow**: It stages the compiled client and system skills, renders the Dockerfile text, sends that Dockerfile to `docker build` with the repository root as the build context, and tags the result as `ufo-sandbox:latest`. If Docker reports failure, it exits with an error; otherwise it prints the tag.

**Call relations**: `main` calls this when the user passes `--build-docker`. Internally it depends on the staging helpers and `pod_dockerfile`, then hands the actual image creation to the local Docker command.

*Call graph*: calls 3 internal fn (pod_dockerfile, stage_client_binary, stage_system_skills); called by 1 (main); 1 external calls (run).


##### `_booted`  (lines 462–479)

```
def _booted(name: str) -> Sandbox
```

**Purpose**: Starts a sandbox from a named E2B template for verification. It retries temporary E2B connection or service problems so the build does not fail just because the remote service had a brief bad moment.

**Data flow**: It receives a template name, tries to create an E2B sandbox with a readiness timeout, and returns the sandbox object if successful. On transport or E2B service errors, it waits longer after each failed attempt and retries, finally raising the error if all attempts fail.

**Call relations**: Both `verify_published_template` and `check_published_template` call this before running commands inside a live sandbox. It isolates the retry behavior so both gates treat temporary boot trouble the same way.

*Call graph*: called by 2 (check_published_template, verify_published_template); 2 external calls (create, sleep).


##### `_reap`  (lines 482–492)

```
def _reap(sandbox: Sandbox, name: str) -> None
```

**Purpose**: Stops a temporary verification sandbox after a check is done. It treats cleanup failure as a warning, not as the main result of the check.

**Data flow**: It receives a sandbox object and its name, asks E2B to kill the sandbox, and returns nothing. If the kill request cannot reach E2B or E2B reports a service problem, it prints a warning to standard error and lets the sandbox expire naturally.

**Call relations**: The verification and drift-check functions call this in their cleanup path after booting a sandbox. It makes sure a failed cleanup does not hide the more important pass-or-fail result of the check that just ran.

*Call graph*: called by 2 (check_published_template, verify_published_template); 1 external calls (kill).


##### `verify_published_template`  (lines 495–510)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a freshly published E2B template actually contains the required runtime tools. This prevents a broken image from being reported as successfully published.

**Data flow**: It receives a template reference, boots a sandbox from it, runs the baked readiness command inside that sandbox, cleans up the sandbox, and raises an error if the command fails or exits with a nonzero code.

**Call relations**: After `_built` publishes each size tier, `main` calls this before printing success. It uses `_booted` to create the sandbox and `_reap` to return it when the probe is finished.

*Call graph*: calls 2 internal fn (_booted, _reap); called by 1 (main).


##### `check_published_template`  (lines 513–533)

```
def check_published_template(name: str, expected: str) -> None
```

**Purpose**: Checks whether a live E2B template is stale compared with the current source recipe. It never publishes anything; it only reports drift.

**Data flow**: It receives a template name and the expected digest, boots a sandbox from that template, reads the baked digest file inside it, cleans up the sandbox, and compares the live digest with the expected one. If the file is missing or the values differ, it raises an error telling the user to republish.

**Call relations**: `main` calls this for every sandbox size when the user passes `--check`. It relies on `_booted` and `_reap` for the temporary sandbox lifecycle, and on `build_definition_digest` from `main` for the expected value.

*Call graph*: calls 2 internal fn (_booted, _reap); called by 1 (main).


##### `_built`  (lines 536–558)

```
def _built(size: str, sizing: Sizing) -> BuildInfo
```

**Purpose**: Publishes one E2B template size tier, with retries for temporary E2B build-service problems. It is the step that turns the recipe into a named live template.

**Data flow**: It receives a size name and its CPU and memory settings, builds the final E2B template definition, asks E2B to build it under the tier's template name, and returns E2B's build information. If E2B times out or has a transport failure, it waits and retries before giving up.

**Call relations**: `main` calls this once per sandbox size during a normal publish. `_built` gets the correct template name from `template_name`, gets the recipe from `e2b_template`, and hands it to E2B's build API.

*Call graph*: calls 2 internal fn (e2b_template, template_name); called by 1 (main); 2 external calls (build, sleep).


##### `main`  (lines 561–599)

```
def main() -> None
```

**Purpose**: Runs the command-line interface for this build script. It chooses between printing a Dockerfile, building a Docker image, checking existing E2B templates, or publishing new E2B templates.

**Data flow**: It reads command-line arguments, chooses exactly one mode, and then coordinates the needed steps. In Dockerfile mode it prints the rendered Dockerfile; in Docker build mode it builds the local image; in check mode it computes expected digests and compares live templates; in publish mode it stages artifacts, builds each E2B size tier, verifies each published template, and prints the resulting template references.

**Call relations**: This is the top-level driver called when the file is run as a script. It ties together all the helper functions: staging, digest creation, E2B building, verification, drift checking, and Docker output.

*Call graph*: calls 9 internal fn (_built, build_definition_digest, build_docker_image, check_published_template, pod_dockerfile, stage_client_binary, stage_system_skills, template_name, verify_published_template); 1 external calls (ArgumentParser).


### `sandbox/proxy_gate.py`

`entrypoint` · `deployment gate`

This script acts like a gate at deployment time: it checks that the off-cluster sandbox can send HTTPS traffic through the project’s proxy route, and that the proxy is actually seeing and judging the connection. Without this check, a broken proxy setup could be deployed silently, leaving sandboxes unable to reach external services or bypassing the expected traffic controls.

The script receives a public HTTPS proxy URL, reads a certificate and sandbox template choice from environment variables, then creates a short-lived E2B sandbox. E2B is a service that provides temporary cloud sandboxes for running code. Inside that sandbox, the script writes and installs the certificate authority certificate, meaning the sandbox is taught to trust the proxy’s TLS certificate.

It then runs a small Python probe inside the sandbox. The probe tries to open an HTTPS connection to Anthropic’s API through the proxy using an intentionally invalid run token. A correct proxy should reject that connection with HTTP 403, meaning “forbidden.” That rejection is the success signal: it proves the sandbox reached the proxy, trusted its TLS setup, and got a policy decision back. If the connection is still unavailable, the script waits and retries for a limited time. If it gets any other result, it fails the deployment gate.

At the end, it tries to kill the temporary sandbox. If cleanup fails because the sandbox service is unreachable, the script does not turn a successful gate into a failure; the sandbox was created with an expiry timeout, so it will be removed later.

#### Function details

##### `ProxyTlsGate.run`  (lines 72–130)

```
def run(self) -> None
```

**Purpose**: This method performs the actual proxy health check. It creates a temporary sandbox, installs the certificate needed to trust the proxy, runs an HTTPS probe through the proxy, and accepts only the expected 403 rejection as proof that the route works correctly.

**Data flow**: It starts with three pieces of stored information: the public proxy URL, the certificate text, and the E2B sandbox template name. It checks that the proxy URL is HTTPS, builds a proxy address with an intentionally invalid token, creates a sandbox, copies the certificate into it, installs that certificate, and repeatedly runs a small Python network probe. If the probe prints 403, the method prints a success message and returns. If the probe reports a still-pending connection, it waits and retries until the deadline. If the final result is missing or unexpected, it raises an error. In all cases, it then tries to shut down the sandbox.

**Call relations**: This is called after command-line parsing by main. It relies on the sandbox service to create the temporary machine, uses shell quoting to build a safe command string, uses time checks and short sleeps for retry timing, and uses URL parsing to safely pull apart the proxy address before testing it.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 133–144)

```
def main() -> None
```

**Purpose**: This is the command-line entry point for the deploy gate. It collects the proxy URL from the command line, reads required environment settings, picks the right sandbox template, and starts the gate check.

**Data flow**: It receives process inputs: the --proxy-url argument and two environment variables, one containing the egress certificate and one describing available E2B templates. If either required environment value is missing, it raises an error. It converts the template configuration into a template map, chooses the first configured sandbox size, builds a ProxyTlsGate object with the proxy URL, certificate, and template, then calls its run method to do the real test.

**Call relations**: This function is invoked when the file is run as a script. It does the setup work and then hands control to ProxyTlsGate.run, which performs the sandbox creation, certificate install, proxy probing, retry loop, and cleanup.

*Call graph*: 3 external calls (__init__, ArgumentParser, sandbox_templates).
