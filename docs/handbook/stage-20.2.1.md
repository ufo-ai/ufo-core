# Protocol scaffolding and shared support schemas  `stage-20.2.1`

This stage is shared behind-the-scenes support. It does not send iMessages itself. Instead, it provides the basic wiring that other parts of the system rely on.

The sandbox protocol file defines a small command language for talking to a safe, isolated work area called a sandbox. Through it, the rest of the code can ask for simple actions such as “run this shell command” or “read this file” without caring which sandbox system is used underneath. It is like a common remote control for different safe workrooms.

The many __init__.py files are package markers. They tell Python that folders such as proto, google.api, photon, and photon.imessage.v1 can be imported as code. Most of them do no work at runtime, but they keep the generated protocol files easy to find.

The generated google.api files describe Google-style HTTP annotations for Protocol Buffers, a structured message format. They let other generated code understand how service calls correspond to HTTP methods and paths.

## Files in this stage

### Sandbox command bridge
Defines the shared sandbox protocol used to issue safe command and file-operation requests.

### `core/src/ufo/harness/sandbox/protocol.py`

`io_transport` · `request handling`

This file is the adapter between ordinary Python code and a sandbox command runner. A sandbox is like a workbench behind glass: the system can ask it to run commands or inspect files, but the details of how that request travels are supplied from outside.

The file has two main pieces. `SandboxCommands` builds safe, well-shaped command requests for common cases: running Bash, running a tracked Bash task, running a POSIX shell script, or running a Python snippet with a bootstrap prefix. It does not execute anything itself. Instead, it receives an `execute` function from elsewhere and passes that function a tuple of command arguments plus a timeout. Keeping each argument separate avoids the confusion and risk that comes from building one big command string.

`SandboxFileOperations` is for file-related actions that return structured JSON. It sends an operation name and JSON parameters to a configured file command, waits with the right timeout, then checks that the reply is usable. If the sandbox returns no output, invalid JSON, a non-object JSON value, or an explicit error, this class turns that into a clear Python exception. One important detail is that document reads can get a longer timeout based on file suffix, because large or complex documents may need more time than ordinary file operations.

#### Function details

##### `CommandResult.stdout`  (lines 14–14)

```
def stdout(self) -> str
```

**Purpose**: This property represents the text that a sandbox command wrote to standard output, which is the command’s normal response channel. Code in this file uses it to read structured results, especially JSON replies from file operations.

**Data flow**: A command has already run somewhere else and produced a result object. This property reads the result’s normal output text and gives it back as a string, without changing anything.

**Call relations**: This is part of the `CommandResult` protocol, meaning any sandbox result object used here must provide it. `SandboxFileOperations.run` relies on this field after execution to find and parse the sandbox’s reply.


##### `CommandResult.stderr`  (lines 17–17)

```
def stderr(self) -> str
```

**Purpose**: This property represents the text that a sandbox command wrote to standard error, which is where command failures and diagnostics usually appear. It helps turn failed or malformed sandbox replies into useful error messages.

**Data flow**: A command result object contains error-output text from a previous run. This property exposes that text as a string, so callers can include it in exceptions or diagnostics.

**Call relations**: This is required by the `CommandResult` protocol. `SandboxFileOperations.run` consults it when the expected normal output is missing or cannot be parsed, so the caller sees the sandbox’s own explanation when possible.


##### `CommandResult.exit_code`  (lines 20–20)

```
def exit_code(self) -> int
```

**Purpose**: This property represents the numeric success or failure code from a sandbox command. A code of zero usually means success, while other values usually mean something went wrong.

**Data flow**: A completed command result contains an exit status. This property returns that integer to any caller that needs to inspect whether the command succeeded.

**Call relations**: This file declares it as part of the common result shape for sandbox commands. Although `SandboxFileOperations.run` does not inspect it directly, requiring it keeps sandbox result objects consistent across command helpers.


##### `SandboxCommands.bash`  (lines 33–38)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This function runs one Bash command through the sandbox’s supervisor command. It is useful when callers want to execute a normal shell command inside the sandbox with a clear timeout.

**Data flow**: It receives a command string and, optionally, a timeout. It wraps that command in the configured supervisor plus `bash -lc`, chooses either the default timeout or the caller’s timeout, then passes the finished argument tuple to the supplied `execute` function. The result from `execute` is returned unchanged.

**Call relations**: Higher-level code calls this when it wants a simple Bash command run inside the sandbox. This method does the command shaping, then hands off to the injected `execute` function, which is the part that actually talks to the sandbox carrier.


##### `SandboxCommands.bash_task`  (lines 40–62)

```
async def bash_task(self, command: str, base: str, *, detach: bool, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This function runs, detaches, or reattaches a tracked Bash task through the sandbox supervisor. It is for longer-running or journaled commands where the system needs a stable task name and optional background execution.

**Data flow**: It receives a shell command, a task base name, a detach choice, and optionally a timeout. It builds a supervisor command containing `--task`, the task name, and `--detach` when requested, then appends `bash -lc` and the actual command. It sends that argument tuple and timeout to `execute`, and returns whatever result `execute` returns.

**Call relations**: Callers use this instead of plain `bash` when the command should be tracked by the supervisor. The method prepares the task-specific command line and hands it to the same injected `execute` function used by the other command helpers.


##### `SandboxCommands.sh`  (lines 64–69)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This function runs a POSIX shell script with its arguments kept separate. POSIX shell is the broadly portable basic shell interface, and keeping arguments separate helps avoid accidental mixing of script text and user data.

**Data flow**: It receives a script string, zero or more argument strings, and optionally a timeout. It builds an argument tuple like `sh -c <script> sh <args...>`, chooses the timeout, sends both to `execute`, and returns the sandbox result unchanged.

**Call relations**: Higher-level code calls this when it wants a small portable shell script rather than a Bash-specific command. This method only packages the request; the supplied `execute` function performs the actual sandbox call.


##### `SandboxCommands.python`  (lines 71–82)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This function runs a Python program inside the sandbox with a configured bootstrap added before the caller’s code. The bootstrap is setup code that helps enforce the sandbox’s containment rules before the requested Python code starts.

**Data flow**: It receives Python source text, optional command-line arguments, and optionally a timeout. It builds a `python3` command using the configured Python flag, prepends the bootstrap text to the program, includes the caller’s arguments, and sends the final command tuple to `execute`. The result from `execute` is returned as-is.

**Call relations**: Callers use this when they need Python execution rather than shell execution. The method combines caller code with the sandbox’s required setup, then delegates the actual run to the injected `execute` function.


##### `SandboxFileOperations.run`  (lines 96–126)

```
async def run(self, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This function performs one sandbox file operation and turns the sandbox’s JSON reply into a Python dictionary. It also turns common bad replies into clear exceptions, so callers do not have to parse raw command output themselves.

**Data flow**: It receives an operation name, such as a file action, and a dictionary of parameters. It checks whether this is a document read for a configured document suffix; if so, it uses the longer document-read timeout, otherwise it uses the default timeout. It serializes the parameters to compact JSON with `json.dumps`, sends the operation and parameters through `execute`, trims the command’s standard output, parses it with `json.loads`, confirms the parsed value is a dictionary, checks for an `error` string, and finally returns the parsed dictionary. If output is missing, malformed, not a JSON object, or contains an error string, it raises an exception instead.

**Call relations**: Higher-level file features call this when they need the sandbox to perform a file action. Inside the flow, it uses `PurePosixPath` to inspect the requested path suffix, `json.dumps` to send parameters in a machine-readable form, and `json.loads` to read the sandbox’s response. The actual command execution is still delegated to the injected `execute` function.

*Call graph*: 3 external calls (dumps, loads, PurePosixPath).


### Protocol package roots
Establishes the top-level generated protocol package hierarchy for the iMessage extension.

### `extensions/imessage/ufo_ext_imessage/proto/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as an importable package. Here, that means code elsewhere in the project can refer to modules under `extensions/imessage/ufo_ext_imessage/proto` using normal Python import paths.

The folder name `proto` usually refers to protocol definitions or generated protocol code, such as files created from Protocol Buffers, a format for describing structured messages. This file does not define those messages or load anything itself. Its job is more like putting a label on a filing cabinet drawer: the drawer may contain important documents, but the label is what lets people find and refer to it consistently.

Without this file, imports may fail in environments or tooling that still expect explicit package markers. Even though modern Python can sometimes import folders without `__init__.py`, keeping this file makes the package structure clear and compatible.


### `extensions/imessage/ufo_ext_imessage/proto/google/__init__.py`

`generated` · `import time`

Python needs a clear way to know which folders are meant to be imported as code packages. This file is that marker for the `google` package inside the iMessage extension’s generated protocol-buffer area. A protocol buffer is a structured message format often used for sharing data between systems; generated Python files may import shared `google` protocol-buffer modules from this folder. Even though the file contains no code, removing it could make imports fail in environments that still rely on `__init__.py` files to recognize packages. Think of it like a label on a drawer: the drawer may look empty, but the label tells Python where to find the files inside or below it.


### `extensions/imessage/ufo_ext_imessage/proto/google/api/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker. In Python, a file named `__init__.py` tells the import system that the surrounding folder should be treated as a package, meaning code elsewhere can refer to modules inside it using dotted names such as `google.api.something`. In this project, the path sits under `proto`, which usually holds code generated from protocol buffer definitions. Protocol buffers are a structured way for systems to describe and exchange data. Even though this file has no functions or classes, it still matters: without it, some Python environments or tooling may fail to recognize this directory as importable package space. Think of it like a label on a drawer. The drawer may contain useful documents, and the label helps the rest of the system find them. This file does not read data, change state, or perform work during normal execution beyond being present for package discovery.


### Google API annotations
Provides generated protobuf support for Google API HTTP annotation metadata.

### `extensions/imessage/ufo_ext_imessage/proto/google/api/annotations_pb2.py`

`generated` · `import-time protobuf schema registration`

This file is not handwritten project logic. It was produced by the Protocol Buffers compiler, a tool that turns `.proto` schema files into code a program can import. Its job is to register the `google.api.http` annotation with Python’s Protocol Buffers runtime. In plain terms, that annotation lets an API method say things like “this remote call should be exposed as an HTTP GET at this path.”

When Python imports this file, it first checks that the installed Protocol Buffers runtime is the expected version. It then imports the related HTTP rule definitions from `http_pb2` and the standard Google descriptor definitions. After that, it loads a compact serialized description of the schema into the global Protocol Buffers descriptor pool. A descriptor is like a catalog card: it describes what messages, fields, and extensions exist, so other code can understand serialized data correctly.

The important thing this file provides is the `DESCRIPTOR`, plus the generated registration of the `http` method option extension. Without this file, code that reads or uses Google API annotations in `.proto` files would not know what the `http` option means. Because it is generated, developers normally should not edit it directly; changes should come from changing the source `.proto` file and regenerating the Python code.


### `extensions/imessage/ufo_ext_imessage/proto/google/api/http_pb2.py`

`generated` · `import time`

This is machine-generated Protocol Buffers code. Protocol Buffers, often called protobuf, are a compact way to define structured messages that can be shared between different programs and languages. Here, the original definition was `google/api/http.proto`, and this Python file is the generated version that Python code can actually import and use.

The messages in this file describe HTTP routing rules for APIs. In plain terms, they let an API say things like: “this service method should be reached with a GET request at this URL path,” or “this method uses POST and takes its request body from this field.” The main generated message types are `Http`, which is a collection of HTTP rules, `HttpRule`, which describes one mapping from an API method to an HTTP pattern, and `CustomHttpPattern`, which allows non-standard HTTP methods.

Most of the file is setup code used by the protobuf runtime. It checks that the installed protobuf library is new enough, registers the serialized schema with protobuf’s global descriptor pool, and asks protobuf’s builder tools to create the Python message classes. Because this file is generated, developers normally should not edit it by hand. If it were missing, any code that imports these HTTP annotation message types would fail, especially code that reads, writes, or reflects on Google-style API definitions.


### Photon iMessage packages
Marks the Photon iMessage protocol directory chain as importable Python packages.

### `extensions/imessage/ufo_ext_imessage/proto/photon/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, meaning other parts of the project can refer to code inside `extensions/imessage/ufo_ext_imessage/proto/photon` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may contain useful items, but the label itself does not do the work. Without this file, some Python environments or tooling might not recognize the `photon` directory as a package, which could make imports fail or behave inconsistently. Because the file is empty, it does not define functions, classes, settings, or side effects. Its value is structural: it helps organize protocol-related code for the iMessage extension.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/__init__.py`

`other` · `import/package discovery`

This is an empty package file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Here, it makes the generated or protocol-related iMessage modules under `proto/photon/imessage` available to the rest of the project using normal Python imports. Think of it like a label on a drawer: the drawer may contain useful documents, but the label itself only helps people and tools find them. If this file were missing in environments that rely on traditional package markers, imports from this directory could fail or behave differently.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/__init__.py`

`other` · `import time`

This file does not define any functions, classes, or settings. Its job is structural: it tells Python that the surrounding folder belongs to an importable package. In everyday terms, it is like putting a label on a drawer so the rest of the program knows where to find the files inside. The directory name suggests this package holds versioned protocol code for an iMessage extension, likely generated message definitions or helpers used elsewhere. Even though the file is blank, it matters because imports often depend on this package layout being present and stable. If it were missing, code that tries to import modules from `extensions.imessage.ufo_ext_imessage.proto.photon.imessage.v1` could fail in environments that require package marker files.
