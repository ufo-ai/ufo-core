# Sandbox image and deployment validation commands  `stage-1.1`

This stage is part of the behind-the-scenes preparation that happens before the system relies on sandboxed runtime work. A sandbox is a safe, isolated place where UFO can run code without exposing the main system. These commands make sure that place is built correctly and that its network path works before real traffic uses it.

The build_template.py script is the image builder and consistency checker. An image is like a frozen setup of a computer with the needed tools already installed. This script builds the local Docker version and keeps it matched with the E2B-hosted sandbox template, so both sandbox types are made from the same recipe and do not quietly become different over time.

The proxy_gate.py script is a deployment safety test. It starts a temporary E2B sandbox, installs the proxy’s trusted certificate authority, and checks that HTTPS traffic can pass through the sandbox proxy as expected. Together, these scripts act like a workshop inspection: first confirming the sandbox machine is built right, then confirming its secure network route is ready.

## Files in this stage

### Sandbox image and proxy validation
Builds the shared sandbox image recipe and then validates that deployed sandbox HTTPS proxy behavior works before runtime use.

### `sandbox/build_template.py`

`entrypoint` · `build and deployment`

This file is the build recipe and command-line tool for UFO’s sandbox environment. A sandbox is an isolated place where the system can run tools, inspect files, use browsers, process PDFs, and perform other work without depending on the user’s machine. Without this file, the project could publish a sandbox missing key programs, or Docker and E2B sandboxes could behave differently even though the rest of the system expects them to match.

The script defines one shared set of image layers: system packages, Python packages, Node packages, browser binaries, helper scripts, environment variables, and a startup command. Think of it like one packing list used for two suitcases: one suitcase is an E2B template, and the other is a Docker image. The base suitcase differs, but the contents are meant to stay the same.

It also writes a digest, which is a short fingerprint of the whole build definition, into the image. Later, the script can boot the published E2B template, read that fingerprint, and compare it with the current source. This catches “drift,” meaning the live template was built from older instructions. Normal runs build and publish E2B templates for each supported size, then start them and check that required tools are present. Other modes print a Dockerfile, build the Docker image locally, or only check whether the published E2B templates are current.

#### Function details

##### `template_name`  (lines 188–189)

```
def template_name(size: str) -> str
```

**Purpose**: Creates the published E2B template name for a sandbox size, such as small, medium, or large. This keeps naming consistent wherever the script builds or checks templates.

**Data flow**: It receives a size name as text. It attaches that size to the shared base template name. It returns the final template name string that E2B should use.

**Call relations**: The main command flow calls this whenever it needs to check, build, or print the status of a size-specific E2B template. It does not hand work off to other project functions; it simply gives the rest of the script the correct name to use.

*Call graph*: called by 1 (main).


##### `build_definition_digest`  (lines 192–230)

```
def build_definition_digest(sizing: Sizing | None) -> str
```

**Purpose**: Builds a fingerprint of everything that matters in the sandbox recipe. This lets the script tell whether a live sandbox template matches the current source code and package lists.

**Data flow**: It receives either a sandbox size setting or nothing for Docker. It reads the declared base image, users, commands, package lists, environment values, and the contents of the scripts and modules that will be copied into the image. It turns all of that into a stable JSON record, hashes it with SHA-256, and returns a text digest like a tamper-evident seal.

**Call relations**: The E2B template builder calls this before applying layers so the digest can be baked into the image. The Dockerfile path does the same for the Docker image. The main check path calls it again later, then asks the live template for its baked digest so the two can be compared.

*Call graph*: called by 3 (e2b_template, main, pod_dockerfile); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 233–265)

```
def apply_layers(builder: object, digest: str) -> object
```

**Purpose**: Adds the shared sandbox contents to a template builder. This is the core recipe that installs tools, copies sandbox helper scripts, sets environment variables, and chooses the runtime user.

**Data flow**: It receives a builder object and the digest that should be written into the image. It changes the builder step by step: build as root, install operating-system packages, remove sudo access, install GitHub CLI, Python and Node packages, install Playwright’s browser, write the digest file, copy helper scripts and modules, fix file permissions, set environment variables, switch back to the non-root user, and set the start and readiness commands. It returns the updated builder.

**Call relations**: Both e2b_template and pod_dockerfile call this, which is the key reason the E2B and Docker sandboxes stay in sync. It hands the completed builder back to those callers so they can either publish an E2B template or render a Dockerfile.

*Call graph*: called by 2 (e2b_template, pod_dockerfile).


##### `e2b_template`  (lines 268–270)

```
def e2b_template(size: str) -> object
```

**Purpose**: Creates the full E2B template definition for one sandbox size. It combines the E2B base template with the shared UFO layers and the size-specific digest.

**Data flow**: It receives a size name. It starts an E2B template builder from the E2B base template, looks up that size’s CPU and memory settings, computes the matching digest, applies the shared layers, and returns the prepared builder object.

**Call relations**: The main build path calls this for each supported size before asking E2B to build and publish the template. Inside, it relies on build_definition_digest for the fingerprint and apply_layers for the actual image contents.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 273–275)

```
def pod_dockerfile() -> str
```

**Purpose**: Renders the Docker version of the sandbox image recipe as a Dockerfile. This is used when someone wants the local Docker carrier to run the same sandbox tools without publishing anything to E2B.

**Data flow**: It starts a template builder from the Docker base image instead of the E2B base template. It computes a digest without size settings, applies the shared layers, converts the resulting template definition into Dockerfile text, and returns that text.

**Call relations**: The main command calls this when the user asks to print the Dockerfile. build_docker_image calls it when it needs Dockerfile text to feed into docker build. It uses the same apply_layers path as E2B so the two targets share one recipe.

*Call graph*: calls 2 internal fn (apply_layers, build_definition_digest); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 278–289)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image from the generated Dockerfile. This supports deployments that use Docker instead of E2B and do not need an E2B account.

**Data flow**: It asks pod_dockerfile for Dockerfile text. It sends that text into the local docker build command, using the repository root as the build context and tagging the result with the expected sandbox image tag. If Docker reports failure, it exits with an error; otherwise it prints the image tag.

**Call relations**: The main command calls this when the user passes the Docker-build option. This function bridges the Python build recipe to the external Docker command-line tool.

*Call graph*: calls 1 internal fn (pod_dockerfile); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 292–307)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a newly published E2B template can actually start and has the required tools installed. This prevents a broken image from being reported as successfully built.

**Data flow**: It receives the name or reference of a published E2B template. It starts a sandbox from that template, runs the readiness command that checks for tools like Python, Node, browser support, PDF tools, LibreOffice, and GitHub CLI, then shuts the sandbox down. If the command fails or exits unsuccessfully, it raises an error.

**Call relations**: The main build path calls this immediately after E2B finishes building each template. It uses E2B’s Sandbox.create to boot the real published image, so the build only succeeds after a live smoke test passes.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 310–330)

```
def check_published_template(name: str, expected: str) -> None
```

**Purpose**: Checks whether a live E2B template is stale compared with the current source recipe. It is a safety gate for continuous integration or release checks because it never publishes anything; it only reports mismatch.

**Data flow**: It receives a template name and the digest expected from the current source. It starts a sandbox from the live template, reads the digest file baked into that image, then shuts the sandbox down. If the file is missing or the digest differs, it raises an error explaining that the sandbox template must be republished.

**Call relations**: The main check path calls this for every sandbox size. main computes the expected digest first, then this function asks the live E2B template what digest it contains and compares the two.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `main`  (lines 333–374)

```
def main() -> None
```

**Purpose**: Acts as the command-line control center for the script. It decides whether to print a Dockerfile, build a Docker image, check published E2B templates, or build and verify new E2B templates.

**Data flow**: It reads command-line options. If asked for a Dockerfile, it prints the generated Dockerfile. If asked to build Docker, it runs the local Docker build. If asked to check, it computes the expected digest for each size and checks the live E2B template. With no special option, it builds each size-specific E2B template, verifies the published result by booting it, and finally prints the published references.

**Call relations**: This is the top-level entry point used when the file is run as a script. It coordinates the smaller helper functions: template_name for names, build_definition_digest for fingerprints, e2b_template for E2B build definitions, pod_dockerfile and build_docker_image for Docker, check_published_template for drift checks, and verify_published_template for post-publish validation.

*Call graph*: calls 7 internal fn (build_definition_digest, build_docker_image, check_published_template, e2b_template, pod_dockerfile, template_name, verify_published_template); 2 external calls (ArgumentParser, build).


### `sandbox/proxy_gate.py`

`entrypoint` · `deploy validation`

This script acts like a deploy gate: it blocks a rollout if the sandbox’s TLS egress path is not ready. In plain terms, it spins up a throwaway sandbox, teaches it to trust the proxy’s certificate, then tries to make an HTTPS request through that proxy. The request uses an intentionally invalid run token, so a working proxy should reject it with a known response: HTTP CONNECT status 403. That rejection is the success signal, because it proves the sandbox can reach the proxy, trust its TLS certificate, and get as far as authentication.

The main flow is simple. The command receives a proxy URL, reads the certificate and sandbox template settings from environment variables, chooses the smallest sandbox size, and runs the gate. The gate checks that the proxy URL is HTTPS, creates a sandbox with enough time for setup and retries, writes the certificate into the sandbox, installs it as root, and repeatedly runs a curl probe. If the proxy is still starting up, curl may return temporary connection-style failures; the script waits and tries again until a deadline. If the expected 403 appears, the gate passes. No matter what happens, it kills the sandbox at the end so the temporary test machine is not left running.

#### Function details

##### `ProxyTlsGate.run`  (lines 47–111)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy readiness check inside a temporary sandbox. It proves that the sandbox can trust the proxy’s TLS certificate and can reach the proxy over HTTPS, expecting the proxy to reject an intentionally invalid token with a 403 response.

**Data flow**: It starts with three pieces of information stored on the ProxyTlsGate object: the public proxy URL, the certificate authority text, and the sandbox template to use. It validates the URL, builds a curl command that sends a test HTTPS request through the proxy, creates a sandbox, writes and installs the certificate there, and then repeatedly runs the probe. If the probe returns the expected CONNECT status, it prints a success message and exits normally; otherwise it raises an error describing the last status, curl exit code, and stderr. It always kills the sandbox before finishing, whether the check succeeds or fails.

**Call relations**: This is the core action that the command-line entrypoint sets up and then invokes. Inside the check, it uses urlsplit to understand the proxy URL, shlex.join to safely assemble the shell command, Sandbox.create to start the temporary E2B machine, monotonic to measure the retry deadline, and sleep to pause between attempts while the proxy is still coming online.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 114–125)

```
def main() -> None
```

**Purpose**: Provides the command-line entrypoint for the proxy gate. It collects the proxy URL and required environment settings, chooses the sandbox template, and starts the readiness check.

**Data flow**: It reads the --proxy-url argument from the command line, then reads the proxy certificate and E2B template list from environment variables. If either required environment value is missing, it raises an error right away. Otherwise it turns the template list into usable sandbox templates, selects the smallest configured sandbox size, creates a ProxyTlsGate object, and runs it.

**Call relations**: This function is called when the script is run directly. It uses argparse.ArgumentParser to parse the user’s command-line input, uses sandbox_templates to convert the environment setting into template names, constructs ProxyTlsGate with those values, and hands control to the gate’s run method for the real sandbox probe.

*Call graph*: 3 external calls (__init__, ArgumentParser, sandbox_templates).
