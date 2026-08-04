# Deployment Runtime Readiness Checks  `stage-1.1`

This stage happens before the service is allowed to take real traffic. It is a set of “ready or not” checks, like inspecting a kitchen before opening the restaurant. The goal is to catch missing databases, mismatched runtime images, or broken secure network paths early, while deployment can still stop safely.

The control database check in `schema.py` prepares and verifies the PostgreSQL database, which is the service’s shared record book. PostgreSQL is the database system; the “schema” is the expected set of tables and fields. This check makes sure the UFO control service will not start with a missing or outdated ledger.

The sandbox build step in `build_template.py` defines the environment where user code will run. It keeps the hosted E2B sandbox template and the local Docker image built from the same instructions, so testing and production use the same recipe.

The proxy check in `proxy_gate.py` starts a fresh sandbox, installs the proxy’s certificate authority certificate, and confirms HTTPS traffic can pass through the proxy correctly. Together, these checks prove the runtime is safe enough to open.

## Files in this stage

### Control Database Gate
Validates that the control service database schema is present and current before the gateway can serve traffic.

### `control/src/ufo_control/schema.py`

`orchestration` · `startup and deploy migration`

This file is the database “setup checklist” for the control service. The service depends on several ledger tables: one for the gateway store, one for invites, and one for Slack connection records. Without these tables, the gateway could accept requests but have nowhere safe to record or look up important state.

The main job here is careful schema creation. In PostgreSQL, even commands that say “create if not exists” can still collide if two processes try them at exactly the same time. To avoid that, `shape_control_schema` takes a PostgreSQL advisory lock, which is like putting a temporary “one worker at this counter” sign on the database setup step. Only one migration writer proceeds at a time.

The file also contains a safety check for an older invite table shape. If the invite ledger exists but lacks the `email_domain` column, its old rows cannot be trusted because an invite must say which domain it grants access to. In that case, the old invite table is dropped and rebuilt.

`require_control_schema` is the runtime guard. It does not create anything. It only checks that every required ledger table already exists, and tells the operator to run `ufo-control migrate` if not.

#### Function details

##### `shape_control_schema`  (lines 41–59)

```
async def shape_control_schema(dsn: str) -> None
```

**Purpose**: This function brings the control database schema up to the expected shape. It is used by the migration command before gateway replicas start, so normal request handling does not try to create tables on the fly.

**Data flow**: It receives a database connection string, opens a PostgreSQL connection, starts one transaction, and takes a database-level advisory lock so only one schema-shaping process runs at once. It checks whether the invite table is from an older format that cannot bind invites to an email domain; if so, it drops that table. Then it runs the schema and table creation statements, each written to be harmless when the database is already up to date. Finally, it closes the connection.

**Call relations**: This function calls `asyncpg.connect` to talk to PostgreSQL. It gathers table definitions from the gateway store, invite, and Slack connection modules, then applies them as one coordinated migration step.

*Call graph*: 1 external calls (connect).


##### `require_control_schema`  (lines 62–71)

```
async def require_control_schema(dsn: str) -> None
```

**Purpose**: This function verifies that the control database has already been prepared. It is meant for gateway startup: if a required table is missing, the gateway refuses to run instead of silently creating tables during live traffic.

**Data flow**: It receives a database connection string, opens a PostgreSQL connection, and checks each required ledger table by name. If all tables exist, it finishes without returning a value. If any table is missing, it raises an error explaining that `ufo-control migrate` must be run first. It always closes the connection afterward.

**Call relations**: This function calls `asyncpg.connect` to query PostgreSQL. It uses the shared `LEDGERS` list, which names the tables defined by the gateway store, invite, and Slack connection modules, to decide what must be present before the gateway can start.

*Call graph*: 1 external calls (connect).


### Sandbox Runtime Validation
Builds the sandbox runtime image and verifies the HTTPS proxy certificate path in a fresh sandbox before deployment.

### `sandbox/build_template.py`

`entrypoint` · `build/deploy time`

UFO needs a prepared “workshop” where tasks can safely run commands, inspect files, use browsers, edit documents, process PDFs, and call helper scripts. This file is the recipe for that workshop. It installs system tools, Python packages, Node.js packages, browser support, GitHub CLI support, UFO’s own sandbox scripts, environment variables, and a simple start command.

The important idea is that there is one shared build definition but two targets. One target is an E2B template, meaning an image used by E2B’s hosted sandbox service. The other is a Docker image, used by a local Docker-based carrier. Like cooking the same meal in two kitchens, the base pantry is different, but the ingredients and final checks are meant to match.

The file also protects against stale builds. It computes a digest, which is a fingerprint of the build recipe and script contents, then bakes that fingerprint into the image. A check mode boots the live template and compares its baked fingerprint to the current source. A publish path also boots the newly built template and runs a readiness command to prove key tools are present before reporting success. Without this file, sandbox builds could become inconsistent, missing tools might only fail during real user work, and Docker and E2B runs could behave differently.

#### Function details

##### `build_definition_digest`  (lines 160–187)

```
def build_definition_digest() -> str
```

**Purpose**: Creates a single fingerprint for the sandbox build recipe. This lets the project tell whether the published sandbox was built from the same inputs as the current source code.

**Data flow**: It reads the fixed build settings in this file, such as the base template, memory size, users, commands, package lists, environment variables, and sandbox script files. It hashes the script contents, places all build inputs into a stable JSON shape, then hashes that JSON. The output is a string like a labeled checksum, which can be written into an image or compared later.

**Call relations**: When the image layers are being assembled, apply_layers asks this function for the fingerprint and writes it into the sandbox. Later, check_published_template asks for the same fingerprint again and compares it with the one found inside the live published template.

*Call graph*: called by 2 (apply_layers, check_published_template); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 190–216)

```
def apply_layers(builder: object) -> object
```

**Purpose**: Applies the shared sandbox setup steps to a template builder. This is the heart of the file: it describes what tools, scripts, permissions, environment variables, and start behavior every sandbox image should have.

**Data flow**: It receives a builder object that represents either an E2B template build or a Docker-image build. It switches to the root user for installation, installs operating-system tools, removes sudo access, installs GitHub CLI, Python packages, Node packages, Playwright’s browser, creates UFO’s config directory, writes the build fingerprint, sets environment variables, copies UFO sandbox scripts into the command path, fixes their permissions, switches back to the normal runtime user, and sets the command and readiness check. It returns the updated builder.

**Call relations**: Both e2b_template and pod_dockerfile pass their builders through this function, which is how the hosted E2B image and Docker image stay synchronized. During this process it calls build_definition_digest so the image can carry proof of exactly what recipe produced it.

*Call graph*: calls 1 internal fn (build_definition_digest); called by 2 (e2b_template, pod_dockerfile).


##### `e2b_template`  (lines 219–221)

```
def e2b_template() -> object
```

**Purpose**: Creates the E2B version of the sandbox build definition. E2B is the hosted sandbox service used to run isolated work environments.

**Data flow**: It starts with the repository root as the file context, chooses the E2B base template, and then sends that builder through apply_layers. The result is a complete E2B template definition ready to be built and published.

**Call relations**: main calls this function when running the default publish path. It relies on apply_layers for the actual tool installation and sandbox setup, then hands the finished definition back so main can ask E2B to build it.

*Call graph*: calls 1 internal fn (apply_layers); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 224–226)

```
def pod_dockerfile() -> str
```

**Purpose**: Creates a Dockerfile for the Docker version of the same sandbox. A Dockerfile is a text recipe that tells Docker how to build a container image.

**Data flow**: It starts a builder from the public Docker base image, applies the same shared layers used for E2B, then converts the result into Dockerfile text. The output is that Dockerfile as a string.

**Call relations**: main calls this directly when the user asks to print the Dockerfile. build_docker_image also calls it before running Docker locally. Like e2b_template, it depends on apply_layers so the Docker image gets the same tools and scripts as the E2B template.

*Call graph*: calls 1 internal fn (apply_layers); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 229–240)

```
def build_docker_image() -> None
```

**Purpose**: Builds the local Docker sandbox image from the generated Dockerfile. This is for deployments that use Docker instead of an E2B account.

**Data flow**: It asks pod_dockerfile for the Dockerfile text, sends that text into a local `docker build` command, uses the repository root as the build context, and tags the result with the expected sandbox image name. If Docker reports failure, it stops the script with an error; if it succeeds, it prints the image tag.

**Call relations**: main calls this when the user passes the Docker build option. This function bridges the shared build recipe to the outside Docker command-line tool by generating the Dockerfile first and then handing it to Docker.

*Call graph*: calls 1 internal fn (pod_dockerfile); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 243–258)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a newly published E2B template can actually boot and contains the required baked-in tools. It is a safety gate after publishing.

**Data flow**: It receives the name or reference of a published template, creates a sandbox from it, runs the readiness command inside that sandbox, and then kills the sandbox. If the command fails or exits unsuccessfully, it raises an error saying the runtime tools are missing. If the command succeeds, nothing is returned and the publish is considered verified.

**Call relations**: main calls this after building and publishing the E2B template. It uses E2B’s sandbox creation API and the same readiness command that the image itself declares, so a broken build is caught immediately instead of later during real work.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 261–282)

```
def check_published_template(name: str) -> None
```

**Purpose**: Checks whether the live E2B template matches the current source recipe without publishing anything. This is the drift check used to catch stale templates.

**Data flow**: It computes the expected build fingerprint from the current source, boots a sandbox from the named live template, reads the fingerprint file baked inside that sandbox, and then shuts the sandbox down. If the file is missing or the value differs, it raises an error telling the user to republish. If they match, it completes successfully.

**Call relations**: main calls this when the user requests check mode. It depends on build_definition_digest for the current expected fingerprint and on E2B sandbox creation to inspect the already published image.

*Call graph*: calls 1 internal fn (build_definition_digest); called by 1 (main); 1 external calls (create).


##### `main`  (lines 285–317)

```
def main() -> None
```

**Purpose**: Provides the command-line behavior for this build script. It decides whether to print a Dockerfile, build a Docker image, check an existing E2B template, or build and verify a new E2B template.

**Data flow**: It reads command-line arguments, chooses one mode, and then calls the matching helper. In Dockerfile mode it writes Dockerfile text to standard output. In Docker build mode it builds the local image. In check mode it compares the live template with the source recipe and prints a success message. With no special option, it builds the E2B template, verifies the published result, and prints the published template reference.

**Call relations**: This is the top-level entry for the file when run as a script. It coordinates the other functions: pod_dockerfile for text output, build_docker_image for local Docker builds, check_published_template for drift checks, e2b_template for the hosted build definition, E2B’s build call for publishing, and verify_published_template for the final safety check.

*Call graph*: calls 5 internal fn (build_docker_image, check_published_template, e2b_template, pod_dockerfile, verify_published_template); 2 external calls (ArgumentParser, build).


### `sandbox/proxy_gate.py`

`entrypoint` · `deployment gate`

This script acts like a gate at the end of a deployment: it refuses to pass unless the sandbox can use the off-cluster HTTPS egress proxy correctly. That matters because sandboxed code may need to make outbound HTTPS requests, and those requests depend on both the proxy route and the proxy's TLS certificate setup working together.

The check uses a real E2B sandbox, which is a temporary cloud sandbox environment. First, it verifies that the supplied proxy URL is an HTTPS URL. Then it builds a `curl` command that tries to connect through that proxy to Anthropic's API. It deliberately uses an invalid run token as the proxy username. A working proxy should therefore answer the CONNECT request with HTTP status `403`, meaning "I reached the proxy, but this credential is not allowed." That is the success signal here.

Before probing, the script writes the certificate authority certificate into the sandbox and runs the install command so the sandbox trusts the proxy's TLS certificate. Then it retries the probe for up to several minutes. Some curl failures are treated as "not ready yet," like a service still warming up. Any unexpected status or timeout becomes a clear error. The sandbox is always killed at the end, like cleaning up a temporary test room after an inspection.

#### Function details

##### `ProxyTlsGate.run`  (lines 46–110)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy readiness check inside a temporary sandbox. It confirms that the proxy URL is usable over HTTPS, installs the certificate needed to trust it, and waits until the proxy responds with the expected rejection for an invalid token.

**Data flow**: It starts with three pieces of information stored on the `ProxyTlsGate`: the public proxy URL, the certificate text, and the sandbox template name. It parses the URL, builds a safe shell command for `curl`, creates a sandbox, writes and installs the certificate there, then repeatedly runs the probe command. If the probe gets CONNECT status `403`, it prints a success message and returns. If the result is malformed, unexpected, or never becomes ready before the deadline, it raises an error. In every case, it shuts down the sandbox before leaving.

**Call relations**: This is the workhorse called after `main` has collected the proxy URL and environment settings. Inside the flow, it asks E2B to create the sandbox, uses URL parsing to validate the proxy address, uses shell quoting to build the probe safely, and uses time checks and sleeps to retry without spinning too fast.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 113–123)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the proxy gate. It collects the proxy URL from the command line and required settings from environment variables, then starts the gate check.

**Data flow**: It reads `--proxy-url` from command-line arguments, reads the CA certificate and E2B template name from environment variables, and stops with a clear error if either environment value is missing. With those values present, it creates a `ProxyTlsGate` object and runs it. It does not return any data; success means the script exits normally, while failure is reported by an exception.

**Call relations**: This function is the front door of the script. It uses Python's argument parser to understand the command line, constructs the gate object with the gathered inputs, and hands control to `ProxyTlsGate.run` for the real sandbox and proxy test.

*Call graph*: 2 external calls (__init__, ArgumentParser).
