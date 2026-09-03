# Deploy bundle and sandbox image validation  `stage-1.2`

This stage prepares UFO to be deployed safely and repeatably. It happens after the app has been built or configured, but before people rely on it in another environment. Think of it as packing a suitcase, checking the workshop it will run in, and testing the safety locks before travel.

`core/src/ufo/bundle.py` creates the suitcase. It builds a self-contained deployment bundle for `ufoctl`, the command-line tool. The bundle includes the app settings, the exact extension versions, the runtime package, and the sandbox client. This makes a Docker build context, meaning a folder Docker can use to build the same runnable image on another machine.

`sandbox/build_template.py` prepares the workshop where UFO runs untrusted or isolated code. It builds and checks both the hosted E2B sandbox template and the local Docker image, keeping them matched.

`sandbox/proxy_gate.py` is the safety inspection. It starts a temporary E2B sandbox, installs the trusted certificate, and confirms HTTPS proxy behavior fails in the expected controlled way before deployment is accepted.

## Files in this stage

### Deployment Bundle Creation
Build the frozen deployment context that packages UFO configuration, extensions, runtime artifacts, and sandbox client assets.

### `core/src/ufo/bundle.py`

`orchestration` · `bundle creation before deployment`

This file solves the problem of making a deploy reproducible. Instead of relying on whatever extensions and files happen to be installed on a machine at runtime, it creates a bundle that names exactly what should run and checks the exact contents of each extension. Think of it like packing a travel kit: the config, lockfile, client binary, and Docker instructions all go into one bag so nothing important is forgotten.

The main class, `Bundle`, is given the current config file, an optional extension catalog, an output directory, the built `ufo` Python wheel, and the sandbox client binary. When `build` runs, it decides which extensions must be pinned, copies the config and client binary into the bundle directory, writes a new lockfile, and creates a Dockerfile.

The most important part is `_pins`. It looks at the extensions already discovered in the local environment. If a lockfile already exists, it starts from that locked list; otherwise it pins every discovered extension. If an extension catalog is available, it also includes entries marked as disabled, because those are “bundle-only”: they are installed into the image now, not fetched later at runtime. For each extension, it opens the built wheel, extracts the extension’s package files, computes a digest, and writes that digest into the lockfile. That means the final image can verify the exact bytes it is booting with.

#### Function details

##### `wheel_name`  (lines 35–37)

```
def wheel_name() -> str
```

**Purpose**: This small helper creates the expected filename for the built `ufo` Python wheel that will be copied into the Docker image. A wheel is a packaged Python distribution, similar to an installable zip file.

**Data flow**: It reads the current `ufo` version from the extension store helper, places that version into the standard wheel filename pattern, and returns the resulting string. It does not change any files or state.

**Call relations**: When the Dockerfile text is being assembled, `Bundle._dockerfile` asks this helper for the wheel filename so the Dockerfile copies and installs the right package.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 61–81)

```
def build(self) -> BundleResult
```

**Purpose**: This is the main action for creating a bundle directory. It writes the frozen config, lockfile, sandbox client binary, and Dockerfile, then returns a summary of what it produced.

**Data flow**: It starts with the paths stored on the `Bundle`: the source config, output directory, wheel, and client binary. First it asks `_pins` for the exact extension pins. Then it creates the output directory, copies the config text, writes a JSON lockfile with the current `ufo` version and extension pins, copies the client binary bytes, writes the generated Dockerfile text, and returns a `BundleResult` containing the output paths and pins.

**Call relations**: This is the public build step that coordinates the whole file. It calls `_pins` before writing the lockfile, calls `_dockerfile` before writing the Dockerfile, uses the lockfile model to serialize pinned extension data, and packages the final paths into `BundleResult` for the caller.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 83–127)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: This decides which extensions belong in the bundle and records a content digest for each one. The digest is a fingerprint of the extension files, used later to prove that the runtime is loading the same code that was bundled.

**Data flow**: It reads the currently discovered extensions from the local environment and checks whether a lockfile already exists. If a lockfile exists, it uses that as the base list of extension names; otherwise it uses every discovered extension. If a catalog is present, it adds catalog entries marked disabled, because those are installed only during bundling. For each chosen extension name, it finds the installed extension metadata, opens the built wheel, gathers the package files for that extension while skipping cache and compiled Python files, computes a digest from those files, and returns a tuple of `ExtensionPin` records. If an expected extension or package is missing, it stops with a runtime error.

**Call relations**: This helper is called by `Bundle.build` before the lockfile is written. It relies on the extension loader to discover installed extensions, find and read any existing lockfile, and compute content digests. It also opens the wheel file directly so the lockfile describes the bytes that will be installed into the bundled image, not just whatever source files happen to be nearby.

*Call graph*: called by 1 (build); 7 external calls (__init__, Path, discovered, extension_content_digest, lockfile_path, read_lockfile, ZipFile).


##### `Bundle._dockerfile`  (lines 129–145)

```
def _dockerfile(self) -> str
```

**Purpose**: This creates the Dockerfile text used to build the runnable image. The Dockerfile installs the packaged `ufo` wheel, copies in the pinned config and lockfile, installs the sandbox client binary, and starts `ufoctl serve` by default.

**Data flow**: It takes no outside input beyond the constants in this file and the current `ufo` version used by `wheel_name`. It builds a sequence of Dockerfile lines: base Python image, working directory, environment variables for config and lockfile paths, wheel copy and install, config and lockfile copy, client binary copy, client path environment variable, entrypoint, and default command. It joins those lines into one string and returns it.

**Call relations**: This helper is called by `Bundle.build` when it is time to write the Dockerfile into the output directory. It calls `wheel_name` so the Dockerfile refers to the exact wheel filename expected in the bundle context.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### Sandbox Image Validation
Build and verify the sandbox images and run proxy-certificate safety checks before deployment.

### `sandbox/build_template.py`

`entrypoint` · `build/deploy time`

This file is the recipe and command-line tool for producing UFO's sandbox runtime: the isolated Linux environment where the agent can run tools, inspect files, use browsers, process PDFs, call the compiled `ufo` client, and load built-in skills. Without it, the project could accidentally run sandboxes that are missing tools, built from old sources, or different between E2B and Docker.

The script has one shared set of layers, like one packing list used for two suitcases. It installs operating-system packages, Python packages, Node packages, Playwright's browser, the compiled `ufo` client binary, helper Python modules, and the system skill bundle. The only major difference is the base: E2B builds from an E2B template, while Docker builds from the public image that template is based on.

A key idea is the build digest: a stable fingerprint of the sandbox definition. It includes package lists, environment variables, copied files, skill bundles, and E2B size settings. Published templates store this digest so `--check` can detect drift without republishing.

The script can print a Dockerfile, build the Docker image locally, check existing E2B templates, or publish all E2B size tiers. Publishing also boots the new template and runs a readiness probe, so a broken image fails before it is accepted.

#### Function details

##### `template_name`  (lines 269–270)

```
def template_name(size: str) -> str
```

**Purpose**: Creates the published E2B template name for a sandbox size such as small, medium, or large. This keeps naming consistent everywhere the script builds or checks templates.

**Data flow**: It receives a size string, adds it to the common base name `ufo-sbx`, and returns the full template name. It does not read or change outside state.

**Call relations**: When the script builds templates, `_built` asks this function for the name to publish under. During `--check`, `main` uses it to find the already-published template for each size.

*Call graph*: called by 2 (_built, main).


##### `client_definition`  (lines 273–299)

```
def client_definition() -> dict[str, str]
```

**Purpose**: Builds a stable description of the `ufo` client binary that will be baked into the sandbox. It hashes the client source code instead of the compiled binary so two machines building the same source do not appear different just because compiler output bytes vary.

**Data flow**: It reads selected files and source directories from the client crate, feeds their paths and contents into a SHA-256 hash, and returns a small dictionary with the binary name, target platform, and source digest.

**Call relations**: The build fingerprint made by `build_definition_digest` calls this function so the sandbox definition changes when the client source changes. Its only outside helper is the standard hash function.

*Call graph*: called by 1 (build_definition_digest); 1 external calls (sha256).


##### `stage_client_binary`  (lines 302–313)

```
def stage_client_binary() -> Path
```

**Purpose**: Copies the compiled `ufo` client into a known artifacts directory where the sandbox build can copy it. This prevents the image from silently using an old or unknown binary.

**Data flow**: It asks `client_binary` for the compiled binary for the fixed Linux target, creates the staging folder if needed, copies the binary there, makes it executable, and returns the staged path.

**Call relations**: Both `build_docker_image` and the normal publishing path in `main` call this before any real image build. Later, `apply_layers` refers to that staged file when adding the client to the image.

*Call graph*: called by 2 (build_docker_image, main); 2 external calls (copyfile, client_binary).


##### `system_skill_bundle`  (lines 317–334)

```
def system_skill_bundle() -> SystemSkillBundle
```

**Purpose**: Finds all built-in system skills and packs them into one reusable bundle. A skill is a packaged capability the sandbox can run, such as document or media processing.

**Data flow**: It searches the repository for `SKILL.md` files while ignoring `node_modules`, reduces overlapping folders to top-level skill roots, discovers the skills in those roots, and returns a `SystemSkillBundle`. The result is cached, so repeated calls reuse the same bundle.

**Call relations**: The digest builder calls this to include the skill bundle fingerprint in the image fingerprint. `stage_system_skills` calls it to write the actual bundle archive that will be copied into the sandbox.

*Call graph*: calls 1 internal fn (from_skills); called by 2 (build_definition_digest, stage_system_skills); 1 external calls (discover_skills).


##### `stage_system_skills`  (lines 337–340)

```
def stage_system_skills() -> Path
```

**Purpose**: Writes the bundled system skills to a zip file inside the build artifacts directory. This makes the skills available to the image build as a normal file.

**Data flow**: It creates the artifacts directory if needed, reads the archive bytes from `system_skill_bundle`, writes them to `system-skills.zip`, and returns that path.

**Call relations**: The Docker build path and E2B publishing path both call this before building. Later, `apply_layers` copies this archive into the image and unpacks it into the sandbox's system skill directory.

*Call graph*: calls 1 internal fn (system_skill_bundle); called by 2 (build_docker_image, main).


##### `build_definition_digest`  (lines 343–385)

```
def build_definition_digest(sizing: Sizing | None) -> str
```

**Purpose**: Creates the fingerprint that says exactly what sandbox definition is being built. This is the script's drift detector: if the source recipe changes, the digest changes.

**Data flow**: It receives either an E2B sizing choice or `None` for Docker. It gathers the base image/template, users, commands, package lists, environment variables, runtime directory permissions, client source digest, skill bundle digest, and helper module hashes, turns that data into stable JSON, hashes it, and returns a `sha256:` string.

**Call relations**: `e2b_template` and `pod_dockerfile` call this before applying image layers so the digest can be baked into the image. `main` also calls it during `--check` to compare the live published template with the current source definition.

*Call graph*: calls 2 internal fn (client_definition, system_skill_bundle); called by 3 (e2b_template, main, pod_dockerfile); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 388–433)

```
def apply_layers(builder: TemplateBuilder, digest: str) -> TemplateFinal
```

**Purpose**: Applies the shared sandbox recipe to either an E2B template builder or a Docker image builder. This is the central packing list that keeps both targets aligned.

**Data flow**: It receives a builder object and a digest. It switches to the build user, installs apt, Python, Node, GitHub CLI, and browser tools, creates required directories, writes the digest into the image, sets environment variables, copies and unpacks system skills, copies the client binary and helper modules, switches to the runtime user, and returns the finalized template with its start and readiness commands.

**Call relations**: `e2b_template` and `pod_dockerfile` both hand their builders to this function. It hands all concrete build steps to the E2B SDK builder methods such as `run_cmd`, `copy`, `set_envs`, and `set_start_cmd`.

*Call graph*: called by 2 (e2b_template, pod_dockerfile); 5 external calls (copy, run_cmd, set_envs, set_start_cmd, set_user).


##### `e2b_template`  (lines 436–438)

```
def e2b_template(size: str) -> TemplateFinal
```

**Purpose**: Creates the E2B version of the sandbox template for one size tier. It uses E2B's hosted code-interpreter base and adds UFO's shared layers on top.

**Data flow**: It receives a size name, creates a template builder using the repository root as the file context, starts from the configured E2B base template, computes the digest for that size's CPU and memory, applies the shared layers, and returns the final template definition.

**Call relations**: `_built` calls this when it is ready to publish a specific E2B size tier. The function delegates the actual package and file setup to `apply_layers`.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 1 (_built); 1 external calls (Template).


##### `pod_dockerfile`  (lines 441–443)

```
def pod_dockerfile() -> str
```

**Purpose**: Renders the Docker version of the sandbox recipe as a Dockerfile. This lets local Docker deployments use the same runtime contents as the E2B templates.

**Data flow**: It creates a builder from the Docker base image, computes a digest without E2B sizing, applies the shared layers, converts the result into Dockerfile text, and returns that text.

**Call relations**: `main` calls this directly for `--dockerfile`, while `build_docker_image` calls it before running `docker build`. It shares the same `apply_layers` recipe used by E2B builds.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 446–459)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image from the shared recipe. This mode does not need an E2B account because it only talks to the local Docker daemon.

**Data flow**: It stages the client binary and system skills, renders the Dockerfile text, sends that text to `docker build` with the repository root as the build context, and either exits with an error on failure or prints the resulting image tag on success.

**Call relations**: `main` calls this when the user passes `--build-docker`. It prepares artifacts through `stage_client_binary` and `stage_system_skills`, gets Dockerfile text from `pod_dockerfile`, then hands the actual build to the external Docker command.

*Call graph*: calls 3 internal fn (pod_dockerfile, stage_client_binary, stage_system_skills); called by 1 (main); 1 external calls (run).


##### `_booted`  (lines 462–479)

```
def _booted(name: str) -> Sandbox
```

**Purpose**: Starts an E2B sandbox from a template and retries temporary connection failures. It exists so checks do not fail just because E2B had a brief bad moment.

**Data flow**: It receives a template name, tries to create a sandbox with the readiness timeout, and returns the sandbox if creation succeeds. If network or E2B service errors happen, it prints a warning, waits longer on each retry, and only raises the error after all attempts fail.

**Call relations**: Both `verify_published_template` and `check_published_template` call this before running commands inside a published template. It hands off sandbox creation to the E2B `Sandbox.create` call and uses sleep between retries.

*Call graph*: called by 2 (check_published_template, verify_published_template); 2 external calls (create, sleep).


##### `_reap`  (lines 482–492)

```
def _reap(sandbox: Sandbox, name: str) -> None
```

**Purpose**: Stops a temporary E2B sandbox used for verification. It tries to clean up promptly without letting cleanup problems hide the real result of the check.

**Data flow**: It receives a sandbox object and its name, calls `kill` on the sandbox, and returns nothing. If killing fails because of a transport or E2B service issue, it prints a warning and leaves the sandbox to expire naturally.

**Call relations**: `verify_published_template` and `check_published_template` call this in their cleanup step after running their checks. It is deliberately secondary: the check result matters more than a failed cleanup call.

*Call graph*: called by 2 (check_published_template, verify_published_template); 1 external calls (kill).


##### `verify_published_template`  (lines 495–510)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a freshly published E2B template actually contains the expected runtime tools. This stops a broken image from being treated as successfully published.

**Data flow**: It receives a published template reference, boots a sandbox from it, runs the same readiness command that is baked into the template, cleans up the sandbox, and raises an error if the command fails or returns a non-zero exit code.

**Call relations**: After `_built` publishes a template, `main` calls this as the publish gate. It depends on `_booted` to start the sandbox and `_reap` to clean it up afterward.

*Call graph*: calls 2 internal fn (_booted, _reap); called by 1 (main).


##### `check_published_template`  (lines 513–533)

```
def check_published_template(name: str, expected: str) -> None
```

**Purpose**: Checks whether an existing published E2B template still matches the current source recipe. It never publishes; it only reports drift.

**Data flow**: It receives a template name and the digest expected from the current source. It boots the live template, reads the digest file baked into the image, cleans up the sandbox, compares live versus expected, and raises an error if the template is old, missing a digest, or different.

**Call relations**: `main` calls this for every sandbox size when the user passes `--check`. It uses `_booted` and `_reap` around the remote command that reads the digest file.

*Call graph*: calls 2 internal fn (_booted, _reap); called by 1 (main).


##### `_built`  (lines 536–558)

```
def _built(size: str, sizing: Sizing) -> BuildInfo
```

**Purpose**: Publishes one E2B sandbox template size, retrying temporary E2B build-service failures. It is the safe wrapper around the actual remote build call.

**Data flow**: It receives a size name and its CPU/memory sizing. It builds the template definition, asks E2B to publish it under the size-specific name, and returns E2B's build information. If the E2B service times out or cannot be reached, it waits and retries before giving up.

**Call relations**: The normal publish path in `main` calls this for each size tier. `_built` gets the template definition from `e2b_template`, gets the publish name from `template_name`, and hands the remote build to `Template.build`.

*Call graph*: calls 2 internal fn (e2b_template, template_name); called by 1 (main); 2 external calls (build, sleep).


##### `main`  (lines 561–599)

```
def main() -> None
```

**Purpose**: Runs the command-line interface for this script. It decides whether to print a Dockerfile, build Docker, check E2B templates, or publish E2B templates.

**Data flow**: It reads command-line flags. With `--dockerfile`, it writes Dockerfile text to standard output. With `--build-docker`, it builds the local Docker image. With `--check`, it recomputes expected digests and checks each published E2B size. With no special flag, it stages artifacts, builds every E2B size tier, verifies each published result, and prints the final size-to-template references.

**Call relations**: This is the top-level entrypoint called when the file is run as a script. It coordinates the helper functions: staging artifacts, rendering Docker, building E2B templates, checking digests, and verifying newly published templates.

*Call graph*: calls 9 internal fn (_built, build_definition_digest, build_docker_image, check_published_template, pod_dockerfile, stage_client_binary, stage_system_skills, template_name, verify_published_template); 1 external calls (ArgumentParser).


### `sandbox/proxy_gate.py`

`entrypoint` · `deployment validation`

This script acts like a gate at deployment time: it checks that the off-cluster sandbox can use the TLS egress proxy correctly. In plain terms, it spins up a throwaway sandbox, teaches it to trust the proxy’s certificate authority certificate, then tries to reach Anthropic’s API through the proxy using a deliberately invalid run token. The expected answer is HTTP 403, meaning “the proxy received the request and rejected the bad credentials.” That is a success here, because it proves the connection reached the right proxy and TLS certificate trust is working. If the connection is still warming up, the script waits and tries again. If it sees a TLS failure, a strange status, or no good answer before the deadline, it fails the deployment check. The file also makes sure the sandbox is killed afterward, but it treats kill failures as non-fatal because the sandbox was created with an expiry timeout. Without this gate, a broken proxy route or missing certificate setup might only be discovered later when real sandbox work tries to make outbound HTTPS calls.

#### Function details

##### `ProxyTlsGate.run`  (lines 72–130)

```
def run(self) -> None
```

**Purpose**: This method performs the actual proxy check. It creates a temporary sandbox, installs the certificate authority certificate, runs a small HTTPS probe through the proxy, and succeeds only when the proxy returns the expected 403 response.

**Data flow**: It starts with three stored values: the public proxy URL, the certificate text, and the sandbox template name. It checks that the proxy URL is HTTPS, builds a proxy address using a deliberately invalid run token and the known proxy password, and turns the probe command into a shell-safe string. It then creates a sandbox, writes the certificate into it, runs the certificate installation command, and repeatedly runs the probe until it sees the expected status, sees a definite failure, or runs out of time. On success it prints a pass message. On failure it raises an error explaining what status it got. In all cases it tries to stop the sandbox afterward, while allowing cleanup network errors because the sandbox will expire on its own.

**Call relations**: This is the worker step used after the command-line setup has gathered the proxy URL, certificate, and template. Inside the check it relies on URL parsing to understand the proxy address, shell quoting to build a safe command, E2B sandbox creation to get a clean temporary machine, and clock/sleep calls to retry while the proxy is still becoming ready.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 133–144)

```
def main() -> None
```

**Purpose**: This is the command-line entry point for the proxy gate. It reads the required proxy URL and environment settings, chooses the right sandbox template, and starts the gate check.

**Data flow**: It reads `--proxy-url` from the command line, then reads the certificate and E2B template configuration from environment variables. If either required environment value is missing, it stops with a clear error. It converts the template configuration into available sandbox templates, picks the template for the first configured sandbox size, builds a `ProxyTlsGate` with those values, and runs the check.

**Call relations**: This function is the setup step before the real validation work. It uses `argparse.ArgumentParser` to collect the user-provided proxy URL, calls `sandbox_templates` to interpret the E2B template configuration, constructs `ProxyTlsGate`, and hands control to the gate’s run logic.

*Call graph*: 3 external calls (__init__, ArgumentParser, sandbox_templates).
