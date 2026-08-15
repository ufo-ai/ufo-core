# Sandbox deployment validation scripts  `stage-1.8`

This stage is a set of preflight checks run during deployment, before the sandbox service is trusted to handle real traffic. A sandbox is an isolated workspace where UFO can run work safely. These scripts make sure that workspace is built correctly and that its network path is safe.

sandbox/build_template.py checks the “room” itself. It builds the local Docker image, which is the packaged software environment used on a machine, and compares it with the cloud E2B sandbox template, which is the version used when real sandboxes are launched. Its job is to keep both made from the same recipe, so code behaves the same in testing and in production.

sandbox/proxy_gate.py checks the “doorway” out to the internet. It starts a real sandbox, installs the trusted certificate, then tries HTTPS traffic, meaning encrypted web traffic. The script proves that this traffic goes through the sandbox proxy as expected. Together, the two checks confirm both the sandbox contents and its secure routing before deployment continues.

## Files in this stage

### Sandbox deployment checks
Validate sandbox image/template parity first, then verify the sandbox proxy HTTPS route before serving traffic.

### `sandbox/build_template.py`

`entrypoint` · `build/deploy verification`

This file is the build recipe and command-line tool for UFO’s sandbox environment. A sandbox is the isolated computer where the system runs tools, edits files, uses browsers, converts documents, and does other potentially messy work without touching the host machine. Without this file, the E2B cloud sandbox and the Docker-based sandbox could drift apart: one might have a tool installed that the other lacks, or a script might behave differently depending on where it runs.

The file defines one shared set of layers: operating-system packages, Python packages, Node packages, browser binaries, sandbox helper scripts, environment variables, and a startup command. It then applies that same recipe to two different bases: an E2B template for cloud sandboxes, and a Docker image for local/container use.

It also builds a “digest,” which is like a fingerprint of the full recipe and the copied script contents. That fingerprint is baked into the image. Later, a check command can boot the published template, read the baked fingerprint, and fail if it no longer matches the source recipe. This prevents stale sandbox templates from silently passing. When publishing E2B templates, the script also boots the new template and runs a readiness probe to confirm key tools are actually present.

#### Function details

##### `template_name`  (lines 188–189)

```
def template_name(size: str) -> str
```

**Purpose**: Creates the E2B template name for a sandbox size, such as small, medium, or large. This keeps naming consistent everywhere the script builds, checks, or reports templates.

**Data flow**: It receives a size name as text. It adds that size to the shared base template name. It returns the final template name string that E2B should use.

**Call relations**: The main command flow calls this when it needs to refer to each size-specific E2B template. The result is then used for checking existing templates, building new ones, and printing status.

*Call graph*: called by 1 (main).


##### `build_definition_digest`  (lines 192–230)

```
def build_definition_digest(sizing: Sizing | None) -> str
```

**Purpose**: Creates a fingerprint of the sandbox build recipe. This fingerprint lets the project tell whether a published sandbox was built from the current source or from an older recipe.

**Data flow**: It receives either a sandbox sizing choice or no sizing for Docker. It reads the build ingredients, including package lists, environment values, script versions, and the actual file contents of baked sandbox scripts/modules. It turns that information into a stable JSON form, hashes it with SHA-256, and returns a string like a tamper-evident label.

**Call relations**: The E2B template builder and Dockerfile builder call this before applying layers, so the fingerprint can be written into the image. The main check path also calls it to compute what the live template should contain before asking the published sandbox to prove it matches.

*Call graph*: called by 3 (e2b_template, main, pod_dockerfile); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 233–265)

```
def apply_layers(builder: object, digest: str) -> object
```

**Purpose**: Adds the shared sandbox ingredients to a template builder. This is the central recipe that makes the E2B and Docker sandboxes contain the same tools, scripts, environment variables, and startup behavior.

**Data flow**: It receives a builder object and the build fingerprint to bake into the image. It switches to the build user, installs system tools, Python libraries, Node libraries, GitHub CLI, and Playwright’s browser, copies UFO sandbox scripts and modules into the command path, sets file permissions, stores the fingerprint, sets runtime environment variables, switches back to the non-root runtime user, and returns the updated builder.

**Call relations**: Both e2b_template and pod_dockerfile pass their builder through this function. That makes it the shared middle step between the two output formats, ensuring both carriers get the same sandbox contents.

*Call graph*: called by 2 (e2b_template, pod_dockerfile).


##### `e2b_template`  (lines 268–270)

```
def e2b_template(size: str) -> object
```

**Purpose**: Builds the in-memory definition for one E2B cloud sandbox template size. It starts from E2B’s base code-interpreter template and adds UFO’s shared sandbox layers.

**Data flow**: It receives a size name. It creates an E2B template builder rooted at the repository, computes the digest for that size’s CPU and memory settings, applies the shared layers, and returns the prepared template definition.

**Call relations**: The main publishing path calls this for each sandbox size before asking E2B to build and publish it. Inside, it relies on build_definition_digest for the fingerprint and apply_layers for the actual recipe.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 273–275)

```
def pod_dockerfile() -> str
```

**Purpose**: Renders a Dockerfile for the local Docker version of the sandbox. This lets Docker deployments use the same sandbox recipe without needing an E2B account.

**Data flow**: It starts with the public Docker base image, computes a digest without fixed E2B sizing, applies the shared sandbox layers, converts the resulting template definition into Dockerfile text, and returns that text.

**Call relations**: The main command uses this when asked to print a Dockerfile. build_docker_image also uses it as the input to a local docker build. Like the E2B path, it goes through apply_layers so the Docker image stays aligned with the cloud template.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 278–289)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image from the generated Dockerfile. It is for deployments that run the sandbox through Docker instead of E2B.

**Data flow**: It asks pod_dockerfile for Dockerfile text, sends that text to the local docker build command with the repository root as context, and tags the resulting image. If Docker reports failure, it stops the script with an error; otherwise it prints the image tag.

**Call relations**: The main command calls this when the user passes the Docker build option. It hands off the actual image construction to the Docker command-line tool after getting the shared recipe from pod_dockerfile.

*Call graph*: calls 1 internal fn (pod_dockerfile); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 292–307)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a freshly published E2B template really boots and contains the required tools. This prevents a broken template from being reported as successfully published.

**Data flow**: It receives the name or reference of a published template. It creates a temporary E2B sandbox from it, runs the readiness command inside that sandbox, then kills the sandbox. If the command fails or exits with a bad status, it raises an error explaining that required baked tools are missing.

**Call relations**: After main publishes each E2B template, it calls this as a gate before accepting the build. This function hands the readiness command to E2B’s sandbox runtime and turns any failure into a publishing failure.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 310–330)

```
def check_published_template(name: str, expected: str) -> None
```

**Purpose**: Checks whether an already published E2B template matches the current source recipe. It is a drift check: it catches templates that are stale even if they still boot.

**Data flow**: It receives a template name and the expected digest. It boots a temporary sandbox from that template, reads the baked digest file inside it, shuts the sandbox down, and compares the live value with the expected one. If the digest is missing or different, it raises an error telling the user to republish.

**Call relations**: The main check path calls this for every sandbox size. main computes the expected digest first, then this function asks the live E2B template what digest it contains and reports whether the two agree.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `main`  (lines 333–374)

```
def main() -> None
```

**Purpose**: Provides the command-line behavior for this build script. Depending on the flags, it can print a Dockerfile, build the Docker image, check published E2B templates, or publish and verify new E2B templates.

**Data flow**: It reads command-line arguments. If asked for a Dockerfile, it prints generated Dockerfile text. If asked to build Docker, it starts the local Docker build. If asked to check, it compares each live E2B template against the current digest. With no special option, it builds each size-specific E2B template, verifies it by booting it, and prints the published references.

**Call relations**: This is the top-level coordinator when the file is run as a script. It calls the smaller helper functions in the order needed for each mode: naming templates, computing digests, creating E2B definitions, rendering Docker output, running checks, and verifying published builds.

*Call graph*: calls 7 internal fn (build_definition_digest, build_docker_image, check_published_template, e2b_template, pod_dockerfile, template_name, verify_published_template); 2 external calls (ArgumentParser, build).


### `sandbox/proxy_gate.py`

`entrypoint` · `deployment validation`

This file acts like a gate at the end of a deployment: it checks that an off-cluster sandbox can use the new HTTPS proxy path safely. Without this check, a broken proxy URL, missing certificate, or not-yet-ready route could make sandboxed workloads fail later in a harder-to-debug place.

The script takes a proxy URL from the command line and reads two required environment settings: the certificate that sandbox machines must trust, and the available E2B sandbox templates. E2B is the service used here to create short-lived sandbox machines. The script chooses a sandbox template, creates a sandbox, copies the certificate into it, and runs the certificate installation command as root.

Then it runs a small curl probe from inside the sandbox. Curl is told to reach Anthropic's API through the proxy, using an intentionally invalid run token. A successful gate does not mean the API call is allowed; it means the HTTPS CONNECT step reaches the proxy and is rejected with the expected 403 status. That is like checking a locked front door: the correct result is not getting inside, but hearing the right lock click.

If the proxy is still starting up, curl may temporarily fail with known pending-style errors. The gate waits and retries for a limited time. It always kills the temporary sandbox at the end, whether the check passes or fails.

#### Function details

##### `ProxyTlsGate.run`  (lines 47–111)

```
def run(self) -> None
```

**Purpose**: This method performs the actual proxy health check from inside a real sandbox. It verifies that the given proxy URL is HTTPS, installs the certificate into the sandbox, repeatedly probes the proxy, and passes only when the proxy returns the expected CONNECT status.

**Data flow**: It starts with three pieces of stored input: the public proxy URL, the certificate text, and the sandbox template name. It validates and rewrites the proxy URL to include a deliberately invalid token, builds a curl command, creates a temporary sandbox, writes and installs the certificate there, and runs the probe command inside that sandbox. The result is either a printed success message and normal return, or a RuntimeError explaining what went wrong; in all cases, the sandbox is killed before the method exits.

**Call relations**: This is called after main has collected command-line and environment settings and built a ProxyTlsGate object. Inside the method, external services and libraries do the heavy lifting: urlsplit checks the URL shape, shlex.join safely builds the shell command, Sandbox.create starts the temporary machine, monotonic measures retry time, and sleep waits between attempts while the proxy may still be coming online.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 114–125)

```
def main() -> None
```

**Purpose**: This is the command-line entry point for the proxy gate script. It gathers the proxy URL and required environment values, chooses the sandbox template, and starts the gate check.

**Data flow**: It reads --proxy-url from the command line, reads the sandbox egress certificate and template configuration from environment variables, and fails immediately if either required environment value is missing. It converts the template configuration into a usable template name for the first configured sandbox size, creates a ProxyTlsGate with those inputs, and calls its run method. The visible outcome is either a completed gate check or an exception that stops the process with an error.

**Call relations**: When this file is run as a script, main is invoked. It is the setup step before ProxyTlsGate.run: argparse collects user input, sandbox_templates translates the environment template setting into a lookup table, and then the gate object takes over to create the sandbox and run the live proxy test.

*Call graph*: 3 external calls (__init__, ArgumentParser, sandbox_templates).
