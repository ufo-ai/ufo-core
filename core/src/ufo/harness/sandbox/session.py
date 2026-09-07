"""The sandbox seam: the carrier interface, the sandbox a caller holds, and value objects.

Everything downstream (tools, engine) depends only on this module; the Docker carrier and the
egress proxy implement against it. A deploy swaps the carrier (E2B, remote) without touching a
tool. The invariant the sandbox exists to hold: a file tool reaches only the conversation's
`/workspace` and the `$UFO_HOME/skills` runtime tree, never the transcript or compaction records,
which live in the blob store the sandbox holds no credential for.

A caller holds a sandbox either way round: `SandboxSession` over one that exists, and `LateSandbox`
over one the first operation creates — the same operations, so no tool knows which it was handed."""

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import ufo.harness.containment as containment_module
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.harness.auth.token_signing import SignedTokenError, sign_token, verify_token
from ufo.harness.containment import ContainmentError, contained_relative
from ufo.harness.sandbox.protocol import SandboxCommands, SandboxFileOperations
from ufo.runtime.authority import (
    ExecutionAuthority,
    authority_from_member_id,
    authority_member_id,
)

WORKSPACE_DIR = "/workspace"
WORKSPACE_SCOPE_HINT = (
    f": a file tool reaches {WORKSPACE_DIR} and nothing above it, so name a path under "
    f"{WORKSPACE_DIR}."
)
WORKSPACE_WRITE_MODE = 0o644
assert containment_module.__file__ is not None
CONTAINMENT_SOURCE = Path(containment_module.__file__).read_text()
SANDBOX_MODULE_BOOTSTRAP = (
    "import sys, types\n"
    "containment = types.ModuleType('containment')\n"
    "sys.modules['containment'] = containment\n"
    f"exec(compile({CONTAINMENT_SOURCE!r}, 'containment.py', 'exec'), containment.__dict__)\n"
)
"""Make the guard importable as `containment` by carrying its source in the program that needs it,
so an in-sandbox program runs the guard this process ships and asks the sandbox for nothing but an
interpreter. The alternative — reading it off a path — makes the guard whatever that sandbox holds:
a member's own machine holds none, and an image holds the copy it was built with. The module is
registered before its own source runs, the order an import itself uses: a dataclass in it resolves
its module through `sys.modules` while the class is being built, and finds nothing otherwise."""
SANDBOX_PYTHON_FLAG = "-I"
"""Isolated mode, which is what keeps the program's own imports out of the workspace: `python3 -c`
otherwise puts the process cwd at `sys.path[0]`, and a carrier runs commands with cwd inside the
workspace the agent writes to, so `import hashlib` — or `import base64`, or `os` — would resolve
against a module the agent planted there, before the guard has checked anything. `-I` drops cwd and
the `PYTHON*` variables from module resolution, leaving the stdlib."""
COPY_IN_PROG = """
import sys
from containment import ContainmentError, contained_file

try:
    with contained_file(sys.argv[1], sys.argv[2], create_parent=True) as target:
        target.replace_bytes(sys.stdin.buffer.read(), target.mode(0o644))
except ContainmentError as error:
    raise SystemExit(str(error))
"""
"""The copy-in a carrier whose `/workspace` lives inside a container runs instead of a shell
redirect: `> "$1"` truncates through a planted link and `mkdir -p` follows a symlinked ancestor,
while this builds each directory as the descent reaches it and renames a staged inode onto the
target. The mode repeats `WORKSPACE_WRITE_MODE` because a `-c` program inside the sandbox cannot
import it."""
RUNTIME_DIRNAME = "runs"
TOOL_OUTPUT_DIRNAME = "tool-output"
SKILL_STAGING_DIRNAME = "staging"
SKILL_LOAD_PROG = """
import base64
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path, PurePosixPath

from containment import contained_file


def safe(value):
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and all(part not in (".", "..") for part in path.parts)
    )


def safe_name(value):
    path = PurePosixPath(value)
    return safe(value) and len(path.parts) == 1 and not path.parts[0].startswith(".")


def digest(files):
    value = hashlib.sha256()
    for name, content in sorted(files):
        value.update(hashlib.sha256(name.encode()).digest())
        value.update(hashlib.sha256(content).digest())
    return "sha256:" + value.hexdigest()


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


payload_path = Path(sys.argv[1])
with contained_file(payload_path, sys.argv[3]) as staged_payload:
    payload_bytes = staged_payload.read_bytes(64 * 1024 * 1024)
    staged_payload.unlink()
if hashlib.sha256(payload_bytes).hexdigest() != sys.argv[4]:
    raise ValueError("skill load payload changed after staging")
payload = json.loads(payload_bytes)
root = Path(sys.argv[2])
root.mkdir(parents=True, exist_ok=True)
try:
    manifest = json.loads((root / ".system-manifest.json").read_bytes())
except (FileNotFoundError, json.JSONDecodeError, TypeError):
    manifest = {"skills": {}}
declared = manifest.get("skills")
system = payload.get("system")
user = payload.get("user")
if not isinstance(declared, dict) or not isinstance(system, dict) or not isinstance(user, dict):
    raise TypeError("invalid skill load payload")
roots = {}
for name, expected in system.items():
    if not isinstance(name, str) or not isinstance(expected, str) or not safe(name):
        raise TypeError("invalid system skill")
    entry = declared.get(name)
    if not isinstance(entry, dict) or entry.get("digest") != expected:
        continue
    paths = entry.get("files")
    if not isinstance(paths, list) or any(
        not isinstance(path, str) or not safe(path) for path in paths
    ):
        raise TypeError("invalid system skill manifest: " + name)
    files = []
    for relative in paths:
        target = root / name / relative
        if target.is_symlink() or not target.is_file():
            files = []
            break
        files.append((relative, target.read_bytes()))
    if files and digest(files) == expected:
        roots[name] = str(root / name)
system_names = tuple(system)
for name, entry in user.items():
    if not isinstance(name, str) or not safe_name(name) or not isinstance(entry, dict):
        raise TypeError("invalid user skill")
    if any(
        item == name or item.startswith(name + "/") or name.startswith(item + "/")
        for item in system_names
    ):
        raise ValueError("user skill conflicts with system skill: " + name)
    expected = entry.get("digest")
    encoded = entry.get("files")
    if not isinstance(expected, str) or not isinstance(encoded, dict):
        raise TypeError("invalid user skill: " + name)
    files = []
    for relative, content in encoded.items():
        if not isinstance(relative, str) or not safe(relative) or not isinstance(content, str):
            raise TypeError("invalid user skill file: " + name)
        files.append((relative, base64.urlsafe_b64decode(content)))
    if digest(files) != expected:
        raise ValueError("user skill does not match its digest: " + name)
    staging = root / (".user-" + str(os.getpid()))
    remove(staging)
    staging.mkdir()
    for relative, content in files:
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    remove(target)
    os.replace(staging, target)
    roots[name] = str(target)
print(json.dumps({"roots": roots}, sort_keys=True, separators=(",", ":")))
"""
SYSTEM_SKILL_SYNC_PROG = """
import hashlib
import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath

from containment import contained_file


def safe(value):
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and all(part not in (".", "..") for part in path.parts)
    )


def digest(files):
    value = hashlib.sha256()
    for name, content in sorted(files):
        value.update(hashlib.sha256(name.encode()).digest())
        value.update(hashlib.sha256(content).digest())
    return "sha256:" + value.hexdigest()


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


archive_path = Path(sys.argv[1])
with contained_file(archive_path, sys.argv[3]) as staged_archive:
    with staged_archive.open_bytes() as source:
        archive_bytes = source.read()
    staged_archive.unlink()
if hashlib.sha256(archive_bytes).hexdigest() != sys.argv[4]:
    raise ValueError("system skill archive changed after staging")
root = Path(sys.argv[2])
root.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
    manifest = json.loads(archive.read("manifest.json"))
    skills = manifest.get("skills")
    if not isinstance(skills, dict):
        raise TypeError("invalid system skill manifest")
    payload = json.dumps({"skills": skills}, sort_keys=True, separators=(",", ":")).encode()
    if manifest.get("digest") != "sha256:" + hashlib.sha256(payload).hexdigest():
        raise ValueError("invalid system skill manifest digest")
    declared = {"manifest.json"}
    for name, entry in skills.items():
        if not isinstance(name, str) or not safe(name) or not isinstance(entry, dict):
            raise TypeError("invalid system skill")
        paths = entry.get("files")
        if not isinstance(paths, list) or any(
            not isinstance(path, str) or not safe(path) for path in paths
        ):
            raise TypeError("invalid system skill files: " + name)
        files = [(path, archive.read(name + "/" + path)) for path in paths]
        if digest(files) != entry.get("digest"):
            raise ValueError("system skill does not match its digest: " + name)
        declared.update(name + "/" + path for path in paths)
    archived = {entry.filename for entry in archive.infolist() if not entry.is_dir()}
    if archived != declared:
        raise ValueError("system skills archive contains undeclared files")
    try:
        previous = json.loads((root / ".system-manifest.json").read_bytes()).get("skills", {})
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    top_levels = {PurePosixPath(name).parts[0] for name in skills}
    merged = {
        name: entry
        for name, entry in previous.items()
        if isinstance(name, str) and PurePosixPath(name).parts[0] not in top_levels
    }
    merged.update(skills)
    staging = root / (".install-" + str(os.getpid()))
    remove(staging)
    staging.mkdir()
    try:
        for name, entry in skills.items():
            for relative in entry["files"]:
                target = staging / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name + "/" + relative))
        for name in top_levels:
            target = root / name
            remove(target)
            os.replace(staging / name, target)
        payload = json.dumps({"skills": merged}, sort_keys=True, separators=(",", ":")).encode()
        installed = json.dumps(
            {"digest": "sha256:" + hashlib.sha256(payload).hexdigest(), "skills": merged},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        manifest_path = root / (".system-manifest-" + str(os.getpid()))
        manifest_path.write_bytes(installed)
        os.replace(manifest_path, root / ".system-manifest.json")
        if "bundles" not in top_levels:
            remove(root / "bundles")
        current = root / "current"
        if "current" not in top_levels and (current.is_symlink() or current.is_file()):
            current.unlink()
    finally:
        remove(staging)
"""
DEFAULT_EXEC_TIMEOUT_SECONDS = 120
DOCUMENT_READ_EXEC_TIMEOUT_SECONDS = 360
DOCUMENT_READ_SUFFIXES = frozenset((".pdf", ".pptx", ".docx", ".xlsx"))
SANDBOX_COMMAND_SUPERVISOR = ("ufo", "run")
SANDBOX_FILE_COMMAND = (
    "sh",
    "-c",
    'if command -v ufo >/dev/null 2>&1; then exec ufo fs "$@"; fi; exec sbxfs "$@"',
    "sh",
)
SENTINEL_MODEL_KEY = "UFO_SENTINEL_MODEL_KEY"
SANDBOX_UID = 1000
SANDBOX_GID = 1000
PROXY_ENV_NAMES = frozenset(("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"))
NO_PROXY_HOSTS = "localhost,127.0.0.1,::1"
"""The destinations every carrier's egress env exempts from the proxy, in both spellings the
ecosystem reads. A sandbox reaching its own loopback is not egress: the proxy admits only globally
routable addresses, so a proxied loopback request can only 403, and a service the turn started
inside the container — Chrome's DevTools port, a dev-server preview — would be unreachable from
inside it. Exempting loopback grants no reach a raw socket does not already have."""
CA_STAGING_PATH = "/root/.ufo-egress-ca.pem"
CA_SANDBOX_PATH = "/usr/local/share/ca-certificates/ufo-egress-ca.crt"
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
PROXY_PASSWORD = "ufo"
NODE_GLOBAL_MODULES = "/usr/local/lib/node_modules"
PLAYWRIGHT_BROWSERS_DIR = "/usr/local/lib/playwright"
PLAYWRIGHT_VERSION = "1.62.0"
PLAYWRIGHT_CHROMIUM_REVISION = "1234"
UFO_HOME_ENV = "UFO_HOME"
SYSTEM_SKILLS_BAKED_ENV = "UFO_SYSTEM_SKILLS_BAKED"
SANDBOX_UFO_HOME = "/home/user/.ufo"
SYSTEM_SKILLS_ROOT = f"{SANDBOX_UFO_HOME}/skills"
SANDBOX_RUNS_ROOT = f"{SANDBOX_UFO_HOME}/{RUNTIME_DIRNAME}"
SANDBOX_TMPDIR = "/var/tmp"
SANDBOX_ENV: dict[str, str] = {
    "NODE_PATH": NODE_GLOBAL_MODULES,
    "PLAYWRIGHT_BROWSERS_PATH": PLAYWRIGHT_BROWSERS_DIR,
    SYSTEM_SKILLS_BAKED_ENV: "1",
    UFO_HOME_ENV: SANDBOX_UFO_HOME,
    "TMPDIR": SANDBOX_TMPDIR,
}
"""Runtime env the sandbox image needs beyond its base: NODE_PATH so node resolves the globally
installed skill modules from any cwd, PLAYWRIGHT_BROWSERS_PATH so scripts find the Chromium baked
at build time, UFO_SYSTEM_SKILLS_BAKED so the client uses the image's local system bundle,
UFO_HOME so the baked client reads the same skill-cache path as a terminal, and TMPDIR so scratch
lands on the disk. The guest mounts `/tmp` as a tmpfs sized to half its memory,
so a byte written there is a resident page — one whole-suite run of a python repository costs 2.5 GB
of scratch, which does not fit that ceiling below the largest tier and does fit the 27 GB root many
times over. Every caller that resolves temp the standard way moves with the one variable: python
`tempfile`, node `os.tmpdir()`, rust `env::temp_dir()`, `mktemp` with no explicit template.

It is `/var/tmp` itself and not a directory under it, because this env reaches boxes the image did
not build. A carrier merges it into every exec of a *resumed* container too, whose filesystem is
whatever template published it, so a path needing creation would be absent there — and creating it
on open would not close the gap either, since preparing a resumed box is allowed to defer.
`/var/tmp` is sticky and world-writable on the base, so it is already on every container this
reaches. Callers who resolve temp through it hard-fail on a missing directory rather than fall back:
the client's lock directory is made one level deep, not with the parents.

The image bakes it as ENV; a carrier whose exec does not inherit image ENV merges it into every
command's env instead — and that difference is the point, because a shell that exports TMPDIR
itself moves only its own descendants, leaving every other exec on the tmpfs."""


def egress_proxy_env(proxy: "ProxyEndpoint", run_token: str) -> dict[str, str]:
    """The environment a remote sandbox's command runs under so its every call off the box routes
    through the public egress proxy. `ufo run` gives its child a plaintext loopback endpoint and
    carries that byte stream to this TLS URL, so standard clients including Python's `urllib` use
    the forward-proxy protocol they implement while the run token remains encrypted off-box. The
    token is the basic-auth username so the proxy attributes and meters each request to the turn;
    the non-empty password makes `urllib` send authentication. `NO_PROXY` exempts the sandbox's own
    loopback services, the model keys are the sentinels the proxy swaps for real keys on the wire,
    and the CA is the one written into the sandbox so the proxy can terminate target TLS the
    sandbox trusts. Off-cluster means the public base is required — absent it (the guard `serve`
    applies at boot), the sandbox would have no metered route out, so this fails loud rather than
    build an open sandbox."""
    if proxy.public_url is None:
        raise RuntimeError(
            "an off-cluster carrier runs outside the pod and needs a reachable egress proxy; "
            "set [sandbox] proxy_public_url to the externally-reachable proxy URL"
        )
    parsed = urlsplit(proxy.public_url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise RuntimeError(
            "an off-cluster carrier requires an HTTPS [sandbox] proxy_public_url so its run "
            "token is encrypted in transit"
        )
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    authority = f"{host}:{parsed.port}" if parsed.port is not None else host
    proxy_url = f"https://{run_token}:{PROXY_PASSWORD}@{authority}"
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "NO_PROXY": NO_PROXY_HOSTS,
        "no_proxy": NO_PROXY_HOSTS,
        "ANTHROPIC_API_KEY": SENTINEL_MODEL_KEY,
        "OPENAI_API_KEY": SENTINEL_MODEL_KEY,
        "SSL_CERT_FILE": SYSTEM_CA_BUNDLE,
        "REQUESTS_CA_BUNDLE": SYSTEM_CA_BUNDLE,
        "CURL_CA_BUNDLE": SYSTEM_CA_BUNDLE,
        "NODE_EXTRA_CA_CERTS": CA_SANDBOX_PATH,
    }


PROBE_TOKEN_KIND = "ufo-probe"


def _basic_username(header: str) -> str:
    """The username inside a `Proxy-Authorization: Basic` header, where every token class rides: a
    sandbox client is handed a proxy URL and nothing else, so userinfo is the only channel."""
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        raise ValueError("proxy authorization is not basic auth")
    return base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)[0]


@dataclass(frozen=True, slots=True)
class RunToken:
    """The turn and member authority attributed to one sandbox process tree."""

    workspace_id: UUID
    turn_id: UUID
    authority: ExecutionAuthority


@dataclass(frozen=True, slots=True)
class RunTokenCodec:
    """Sign the per-turn proxy username and recover only tokens minted by this deployment."""

    secret: bytes

    @classmethod
    def from_env(cls) -> "RunTokenCodec":
        value = os.environ.get(UFO_TOKEN_SECRET_ENV)
        if not value:
            raise RuntimeError(f"{UFO_TOKEN_SECRET_ENV} must be set to sign sandbox run tokens")
        return cls(secret=value.encode())

    def encode(self, run: RunToken) -> str:
        member_id = authority_member_id(run.authority)
        member = "-" if member_id is None else str(member_id)
        payload = f"ufo-run/{run.workspace_id}/{run.turn_id}/{member}".encode()
        return sign_token(self.secret, payload)

    def from_proxy_auth(self, header: str) -> RunToken:
        username = _basic_username(header)
        try:
            kind, workspace, turn, member = verify_token(username, self.secret).decode().split("/")
            if kind != "ufo-run":
                raise ValueError("invalid run token domain")
            return RunToken(
                workspace_id=UUID(workspace),
                turn_id=UUID(turn),
                authority=authority_from_member_id(None if member == "-" else UUID(member)),
            )
        except (UnicodeDecodeError, SignedTokenError, ValueError) as error:
            raise ValueError("invalid signed run token") from error


@dataclass(frozen=True, slots=True)
class ProbeToken:
    """The conversation and member authority attributed to one off-turn sandbox exec, until it
    expires.

    A turn's egress is authorized by the turn: the proxy admits a CONNECT while the DB still reports
    that turn running. A probe runs off every turn, so there is no row whose status answers whether
    it is still live — the token carries its own deadline, minted per exec for that exec's timeout,
    and the proxy compares it fresh per CONNECT. `probe_id` names the one exec.

    Member authority names whoever armed the watch this exec serves, so a command that reached
    their own connected account in the arming turn keeps reaching it on every probe after it.
    Workspace authority reaches only connections shared with the whole workspace."""

    workspace_id: UUID
    conversation_id: UUID
    probe_id: UUID
    expires_at: int
    authority: ExecutionAuthority


@dataclass(frozen=True, slots=True)
class ProbeTokenCodec:
    """Sign the per-probe proxy username and recover only probes minted by this deployment. It holds
    the same deploy secret `RunTokenCodec` does, and each class names its own domain inside the
    signed payload — so a run token presented as a probe (or the reverse) is refused as firmly as a
    forgery, and neither codec can be made to read the other's token as its own."""

    secret: bytes

    def encode(self, probe: ProbeToken) -> str:
        member_id = authority_member_id(probe.authority)
        member = "-" if member_id is None else str(member_id)
        payload = (
            f"{PROBE_TOKEN_KIND}/{probe.workspace_id}/{probe.conversation_id}"
            f"/{probe.probe_id}/{member}/{probe.expires_at}"
        ).encode()
        return sign_token(self.secret, payload)

    def from_proxy_auth(self, header: str) -> ProbeToken:
        username = _basic_username(header)
        try:
            kind, workspace, conversation, probe, member, expires = (
                verify_token(username, self.secret).decode().split("/")
            )
            if kind != PROBE_TOKEN_KIND:
                raise ValueError("invalid probe token domain")
            return ProbeToken(
                workspace_id=UUID(workspace),
                conversation_id=UUID(conversation),
                probe_id=UUID(probe),
                expires_at=int(expires),
                authority=authority_from_member_id(None if member == "-" else UUID(member)),
            )
        except (UnicodeDecodeError, SignedTokenError, ValueError) as error:
            raise ValueError("invalid signed probe token") from error


EGRESS_CA_CERT_ENV = "UFO_EGRESS_CA_CERT"
EGRESS_CA_KEY_ENV = "UFO_EGRESS_CA_KEY"
EGRESS_CONTROL_TOKEN_ENV = "UFO_EGRESS_CONTROL_TOKEN"


@dataclass(frozen=True)
class ProxyEndpoint:
    """Where the egress proxy listens, backend-neutral: the port plus the CA the sandbox trusts so
    the proxy can terminate TLS and swap sentinels onto the wire. Each carrier decides how its
    sandbox addresses the host the proxy runs on — that reachability detail is the carrier's, not
    the proxy's. `public_url` is the externally-reachable base an off-cluster sandbox (e2b) dials
    the proxy at; unset for an in-pod carrier (docker/local) whose sandbox reaches the proxy over a
    host-local address it forms from `port` alone."""

    port: int
    ca_cert: str
    public_url: str | None = None


SANDBOX_SIZES: tuple[str, ...] = ("small", "medium", "large")
"""The sandbox sizes a sizing carrier provisions, in ascending order. A carrier that offers them
declares them on its `CarrierSpec.sizes`; one that provisions a single shape (docker, local, the
terminal) declares none and ignores `SandboxSpec.size`."""


@dataclass(frozen=True)
class SandboxSpec:
    """`workspace_host_path` is the host directory an in-cluster carrier serves `/workspace` from —
    the Docker carrier's bind-mount source, the local carrier's cwd. An off-cluster carrier (e2b)
    cannot see the host filesystem and serves `/workspace` from its own sandbox disk, so it ignores
    the field.

    `resume_id` is the sandbox id a prior process persisted on the conversation row: when this
    process holds no live sandbox for the conversation, the carrier resumes that id rather than
    opening a fresh sandbox, so a serve restart reattaches instead of stranding it. None means
    create fresh (no stored handle, or one another backend wrote). A carrier that resumes by
    conversation identity (docker's container name, the local host directory) ignores it.

    `size` is the owning agent's sandbox size, read off its row by the open that builds this spec.
    It shapes only a fresh sandbox on a carrier that declares sizes — a resumed sandbox keeps the
    size it was created at, and a single-shape carrier ignores it. None means the caller states no
    size (an attach, a terminal bind).

    `turn_id` is the turn this open serves, which one container answers many of: a subagent inherits
    the sandbox of the turn that spawned it, so several turns run commands in one container at once.
    A `CommandStopping` carrier keys the commands it leaves running on it, so a stop reaches the
    turn's own groups and no sibling's. None means no turn owns the open (a read, an off-turn write,
    a probe) and nothing will ever stop its commands; a carrier whose commands die with the call
    that launched them ignores it."""

    conversation_id: UUID
    image_ref: str
    workspace_host_path: str
    proxy: ProxyEndpoint
    run_token: str
    resume_id: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    size: str | None = None
    turn_id: UUID | None = None


@dataclass(frozen=True)
class SandboxHandle:
    """An opaque reference to a created-or-attached container; the carrier reads it, not tools. It
    carries `workspace_host_path` so a host-path carrier can rewrite a logical `/workspace` path to
    where it actually serves it, and the base `run_token` plus `egress_env` a scoped session
    rewrites for each exec. A container shared across turns never pins either one's authority.
    Whatever a public per-port host requires on the wire is not here: `dial` reads it off the live
    container, so a handle rebuilt from the durable row alone (the ingress) reaches a port exactly
    as the process that created it does. `turn_id` names the turn this reference was opened for —
    `SandboxSpec.turn_id`, carried across re-authorization — and is what scopes a stop to that
    turn's own commands where several turns share the container."""

    conversation_id: UUID
    container_id: str
    workspace_host_path: str | None = None
    run_token: str | None = None
    egress_env: Mapping[str, str] = field(default_factory=dict)
    turn_id: UUID | None = None
    runtime_root: str = ""


SANDBOX_HANDLE_SEP = ":"


def sandbox_handle_id(backend: str, value: str) -> str | None:
    """The sandbox id inside a stored `<backend>:<id>` handle when it belongs to `backend`, else
    None — a handle another backend wrote is not this carrier's to resume. The backend prefix is
    load-bearing: a deploy that switched carriers must not resume another backend's id."""
    prefix = f"{backend}{SANDBOX_HANDLE_SEP}"
    return value[len(prefix) :] if value.startswith(prefix) else None


def sandbox_handle_backend(value: str) -> str:
    """The backend scheme a stored `<backend>:<id>` handle bears — what routes a conversation to
    the carrier that wrote it, where a deploy keeps more than one live."""
    return value.split(SANDBOX_HANDLE_SEP, 1)[0]


@dataclass(frozen=True)
class ExecResult:
    """One command's outcome. `timed_out_after_s` is the carrier's own deadline firing, in the
    seconds it allowed; None means the command chose its own exit however it ended. The exit code
    cannot carry this: a command that runs `timeout` exits 124 exactly as a carrier-stopped one
    does, so a caller reading the code alone cannot tell whose deadline ended the work."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out_after_s: int | None = None


@dataclass(frozen=True)
class DialTarget:
    """An externally dialable authority for one in-sandbox port, plus whatever the carrier
    requires on the wire to reach it (e2b's traffic-access header). `tls` says whether the
    authority terminates TLS, so a consumer picks https/wss vs http/ws instead of guessing."""

    host: str
    tls: bool
    headers: Mapping[str, str] = field(default_factory=dict)


class SandboxUnreachable(RuntimeError):
    """The dial contract's error: a carrier's sandbox is gone or has no external route."""


class SandboxProviderUnavailable(RuntimeError):
    """A sandbox carrier's external control plane did not recover inside its short retry."""


class Carrier(Protocol):
    """Create-or-attach a per-conversation container and reach its `/workspace`: run commands in it,
    write bytes in, stream bytes out. `/workspace` is the carrier's own storage and the only copy of
    a conversation's files, so nothing here reclaims a container — whether one can be dropped
    without taking the workspace with it is knowledge only a carrier holds, and the carrier that can
    reclaims its own."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        """The sandbox `spec.resume_id` names when it is reachable, else None — never a fresh one.
        The read seam: a browse of a conversation's files must not answer by provisioning, so a
        container the carrier reclaimed or a sandbox its provider lost reads as absent rather than
        resurrected. The returned handle carries no egress env — a read runs no command that leaves
        the box."""
        ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        """Run `argv` in the sandbox. `model_command` is the one element of it the model wrote,
        set only by a journal launch the caller declared model-authored; everything else — a file
        walk, a probe, a member's own declared command — leaves it unset, because the text was
        composed here rather than by the model. A carrier that runs commands on hardware the deploy
        owns has no use for the distinction and ignores it; the client carrier runs on the member's
        own machine, where what the model wrote is the only text it may refuse."""
        ...

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """Write `content` to the absolute workspace `path`, creating parent directories — the
        copy-in that pairs with `read`'s copy-out. Each carrier supplies its own (e2b uploads
        through its filesystem API, docker streams over a real stdin), because bytes must never ride
        `exec`'s argv: a carrier whose command API takes a shell string has to inline them, which
        the provider rejects once they are large — exactly when a caller offloads a large result.

        The path is a filename an agent, a model, or an inbound surface chose, so the write runs
        through the containment guard — `COPY_IN_PROG` where the bytes land inside a container —
        rather than a shell redirect, and a refusal is an OSError. A non-regular target is replaced,
        not refused: the rename cannot write through a link, and a link the agent left at an inbox
        name must not deny every later delivery to that name."""
        ...

    def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        """Stream the workspace file at `path` out of the container in bounded chunks, never
        buffering it whole in the host process — the copy-out that pairs with `write`. Each carrier
        supplies its own (e2b streams from its filesystem API, docker over a real stdout, the local
        carrier off the host directory). A missing path raises FileNotFoundError on every carrier;
        the local carrier confines the path through the containment guard first, so a target that
        is a symlink, a directory, or outside the workspace is refused as a ContainmentError before
        any open, and the docker carrier raises each filesystem refusal as the OSError its errno
        names (resolving cat's reason through strerror) while e2b surfaces its SDK's exception; a
        read that dies for a non-filesystem reason raises the carrier's own error naming what is
        known."""
        ...

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        """Everything a caller outside the sandbox needs to reach one in-sandbox `port` — the
        generic inbound seam for a service the turn started inside the container (a browser's CDP
        endpoint, a site's dev-server preview). The returned `DialTarget` carries the authority to
        dial (`host`, `host:port` where the carrier publishes no per-port name), whether that
        authority terminates TLS (`tls` picks the caller's scheme — https/wss or http/ws — so no
        caller guesses), and any header the wire requires (e2b's traffic-access token). A carrier
        with no external route, or whose sandbox is gone or unroutable, raises
        `SandboxUnreachable` — never the provider SDK's own error."""
        ...

    async def file_op(
        self, handle: SandboxHandle, op: str, params: dict[str, object]
    ) -> dict[str, object]:
        """Run one file op — the windowed read, write, edit, glob, grep and change listing the file
        tools are built on — against the sandbox's workspace, and answer its parsed JSON object.
        The work runs inside the sandbox and comes back bounded, so the host never pulls a whole
        file across the boundary to loop over it. How the op reaches the files is the carrier's:
        one whose sandbox bakes the `ufo` client answers with `ufo_fs_file_op`. A handled failure
        raises `ValueError` — a recoverable tool error to the model — and anything else raises
        `RuntimeError`."""
        ...


@runtime_checkable
class CommandStopping(Protocol):
    """A carrier whose commands outlive the call that launched them, and which can stop them on
    demand. It is separate from `Carrier` because it answers a question only some backends have: one
    that runs a command off-box, where the launch and the wait are two round trips, holds work a
    cancelled `exec` leaves behind, while one whose command dies with the call it was made in has
    nothing to stop.

    Which cancel a carrier is unwinding is not knowable inside `exec` — a member's stop and an
    executor preemption both arrive there as a bare `asyncio.CancelledError`, and DBOS names the
    difference only once the step's body has unwound — so a carrier that implements this leaves the
    command running and the engine issues the stop on a deliberate cancel alone.

    The stop reaches only what `handle.turn_id` launched. One container serves every turn of a
    conversation and every subagent turn that inherited it, so a stop scoped to the container would
    kill the in-flight commands of turns nobody cancelled."""

    async def stop_commands(self, handle: SandboxHandle) -> None: ...


@runtime_checkable
class SkillLoading(Protocol):
    """A carrier whose connected runtime loads skills through its native operation."""

    async def load_skills(
        self, handle: SandboxHandle, payload: Mapping[str, object]
    ) -> ExecResult: ...


@runtime_checkable
class SkillExecuting(Protocol):
    """A container carrier that runs the server-carried skill programs as root."""

    async def exec_skill(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult: ...


@runtime_checkable
class SystemSkillSeeding(Protocol):
    """A carrier whose runtime filesystem is created in this process."""

    def seed_system_skills(self, archive: bytes) -> None: ...


async def ufo_fs_file_op(
    carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]
) -> dict[str, object]:
    """`Carrier.file_op` for a sandbox image: run `ufo fs`, or `sbxfs` when that is the image's
    file command, and read its single JSON object off stdout. Shared by every such carrier, so none
    of them restates the argv, the parse, or which failures the model may recover from."""
    return await SandboxFileOperations(
        execute=lambda argv, timeout_s: carrier.exec(handle, argv, timeout_s),
        default_timeout_s=DEFAULT_EXEC_TIMEOUT_SECONDS,
        document_read_timeout_s=DOCUMENT_READ_EXEC_TIMEOUT_SECONDS,
        document_suffixes=DOCUMENT_READ_SUFFIXES,
        command=SANDBOX_FILE_COMMAND,
        name="ufo fs",
    ).run(op, params)


WORKSPACE_ROOT_SEGMENT = re.compile(
    r"(?<![\w.~%$@+)}-])" + re.escape(WORKSPACE_DIR) + r"(?![\w.-])"
)


def host_argv(argv: tuple[str, ...], root: str) -> tuple[str, ...]:
    """The argv for a carrier whose `/workspace` is a host directory: every logical path in it
    named under `root`.

    A `/workspace` in argv text is rewritten only where it spans a whole path segment — nothing
    continuing a name before it, nothing continuing one after. The substring also arrives as text
    that is not this workspace, and each of those crosses untouched: a blob key under `workspaces/`
    inside a presigned URL, a `$HOME/workspace` of the member's own, a sibling `/workspace-old`.
    The signed URL is the sharp one — an upload PUTs exactly the key the store signed, so a rewrite
    inside it sends a request nothing signed and the store answers 403, and the upload legs of
    hosted publishing and file sharing both ride one.
    """
    return tuple(WORKSPACE_ROOT_SEGMENT.sub(lambda _: root, arg) for arg in argv)


def workspace_path(path: str) -> str:
    """Resolve a tool-supplied path under WORKSPACE_DIR and reject any escape from the subtree —
    the container's mount already scopes the filesystem, this scopes the arguments tools pass. The
    refusal names the scope, because the path came from a model that can only correct it by knowing
    which tree it may name."""
    candidate = PurePosixPath(path if path.startswith("/") else f"{WORKSPACE_DIR}/{path}")
    resolved = PurePosixPath(*_resolve_parts(candidate.parts))
    root = PurePosixPath(WORKSPACE_DIR)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path {path!r} escapes {WORKSPACE_DIR}{WORKSPACE_SCOPE_HINT}")
    return str(resolved)


def runtime_relative(path: str) -> PurePosixPath:
    """A runtime-internal name below one conversation's `$UFO_HOME/runs/<id>` root."""
    root = "/runtime"
    try:
        resolved = contained_relative(path, root)
    except ContainmentError:
        raise ValueError(f"invalid runtime path: {path!r}") from None
    relative = PurePosixPath(resolved).relative_to(root)
    if str(relative) != path:
        raise ValueError(f"invalid runtime path: {path!r}")
    return relative


def sandbox_runtime_root(conversation_id: UUID) -> str:
    """The per-conversation runtime root inside an isolated sandbox."""
    return f"{SANDBOX_RUNS_ROOT}/{conversation_id.hex}"


def shell_path(path: str) -> str:
    """Quote a sandbox path for a shell while expanding a terminal's `$UFO_HOME`."""
    prefix = f"${UFO_HOME_ENV}/"
    if path.startswith(prefix):
        return f'"${UFO_HOME_ENV}"/' + shlex.quote(path.removeprefix(prefix))
    return shlex.quote(path)


def _runtime_root(handle: SandboxHandle) -> str:
    return handle.runtime_root or sandbox_runtime_root(handle.conversation_id)


def _runtime_path(handle: SandboxHandle, relative: str) -> str:
    return str(PurePosixPath(_runtime_root(handle)) / runtime_relative(relative))


def _runtime_display_path(handle: SandboxHandle, relative: str) -> str:
    run_id = PurePosixPath(_runtime_root(handle)).name
    return str(
        PurePosixPath(f"${UFO_HOME_ENV}") / RUNTIME_DIRNAME / run_id / runtime_relative(relative)
    )


def rooted_path(path: str, root: str) -> str:
    """Normalize a path under `root` with the workspace guard and preserve its root spelling."""
    normalized = workspace_path(f"{WORKSPACE_DIR}{path.removeprefix(root)}")
    return f"{root}{normalized.removeprefix(WORKSPACE_DIR)}"


def _resolve_parts(parts: tuple[str, ...]) -> list[str]:
    stack: list[str] = []
    for part in parts:
        if part == "..":
            if len(stack) <= 1:
                raise ValueError("path escapes the workspace root")
            stack.pop()
        elif part not in ("", "."):
            stack.append(part)
    return stack


class Sandbox:
    """What a caller reaches a conversation's `/workspace` through: run a command in it, write bytes
    in, stream bytes out, dial a port something inside it opened.

    Every operation resolves the sandbox it runs against through `_bound`, so the two ways a caller
    holds one — a session over a sandbox that exists, and a turn's sandbox that is created the first
    time an operation needs it — answer every operation from this one implementation."""

    @property
    def conversation_id(self) -> UUID:
        """The conversation whose workspace this reaches, which a subagent turn inherits from the
        turn that spawned it. It is known before anything is created, so naming the workspace an
        operation would land in never creates one."""
        raise NotImplementedError

    @property
    def turn_id(self) -> UUID | None:
        """The durable turn whose authority this sandbox reference carries, when it is turn-bound.
        Recovery rebuilds a reference with the same id, so a sandbox-local resource can use it as
        stable ownership without conflating sibling turns sharing the conversation container."""
        raise NotImplementedError

    @property
    def created(self) -> bool:
        """Whether a sandbox exists to run an operation against."""
        raise NotImplementedError

    def authorize(
        self,
        run_token: str,
        cleared_env: frozenset[str],
        env: Mapping[str, str],
    ) -> "Sandbox":
        """The same sandbox under one exact authority: its run token on the proxy environment,
        connector variables outside that authority dropped, and its admitted variables exported."""
        raise NotImplementedError

    async def _bound(self) -> "SandboxSession":
        raise NotImplementedError

    async def runtime_path(self, relative: str) -> str:
        """Resolve one internal file below this conversation's runtime root."""
        return _runtime_path((await self._bound()).handle, relative)

    async def runtime_display_path(self, relative: str) -> str:
        """Name one internal file through the `$UFO_HOME` path the agent can reuse."""
        return _runtime_display_path((await self._bound()).handle, relative)

    async def write_runtime_file(self, relative: str, content: bytes) -> None:
        """Write one runtime-owned file outside the member workspace."""
        bound = await self._bound()
        await bound.carrier.write(bound.handle, _runtime_path(bound.handle, relative), content)

    async def write_runtime_path(self, path: str, content: bytes) -> None:
        """Write an already-resolved path inside this conversation's runtime root."""
        bound = await self._bound()
        root = PurePosixPath(_runtime_root(bound.handle))
        candidate = PurePosixPath(path)
        if not candidate.is_relative_to(root) or candidate == root:
            raise ValueError(f"path {path!r} escapes the runtime root")
        relative = str(candidate.relative_to(root))
        await bound.carrier.write(bound.handle, _runtime_path(bound.handle, relative), content)

    async def runtime_file_exists(self, relative: str) -> bool:
        """Whether one regular runtime-owned file exists."""
        bound = await self._bound()
        target = _runtime_path(bound.handle, relative)
        result = await bound.carrier.exec(
            bound.handle, ("sh", "-c", 'test -f "$1"', "sh", target), timeout_s=30
        )
        return result.exit_code == 0

    def _commands(
        self, bound: "SandboxSession", model_command: str | None = None
    ) -> SandboxCommands[ExecResult]:
        return SandboxCommands(
            execute=lambda argv, timeout_s: bound.carrier.exec(
                bound.handle, argv, timeout_s, model_command
            ),
            default_timeout_s=DEFAULT_EXEC_TIMEOUT_SECONDS,
            python_flag=SANDBOX_PYTHON_FLAG,
            python_bootstrap=SANDBOX_MODULE_BOOTSTRAP,
            supervisor=SANDBOX_COMMAND_SUPERVISOR,
        )

    async def bash(self, command: str, timeout_s: int | None = None) -> ExecResult:
        bound = await self._bound()
        return await self._commands(bound).bash(command, timeout_s)

    async def bash_task(
        self,
        command: str,
        base: str,
        *,
        detach: bool,
        model_authored: bool,
        timeout_s: int | None = None,
    ) -> ExecResult:
        """Run or reattach one journaled bash command through the shared sandbox supervisor.

        `model_authored` says whose text `command` is, and every caller states it because the
        launch cannot be read back off the argv: a member's declared command and the model's own
        arrive at the carrier in the same shape. A carrier that refuses commands on the member's
        machine refuses only what the model wrote."""
        bound = await self._bound()
        commands = self._commands(bound, command if model_authored else None)
        return await commands.bash_task(command, base, detach=detach, timeout_s=timeout_s)

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        """Run a POSIX script with `args` as its positional parameters — each one its own argv
        element, so a host-path carrier's `/workspace` rewrite reaches it and no quoting ever
        interpolates it into the script."""
        bound = await self._bound()
        return await self._commands(bound).sh(script, *args, timeout_s=timeout_s)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        """Run an in-sandbox python program with the containment guard importable, so a program that
        builds a path from an argument runs the same checks the file ops run rather than its own —
        the one place every such program reaches the guard from.

        The guard is carried into the program by `SANDBOX_MODULE_BOOTSTRAP` rather than imported
        from the sandbox, so it is the copy this process ships under every carrier. The interpreter
        runs isolated (`SANDBOX_PYTHON_FLAG`), which is what keeps that bootstrap from resolving
        against the very workspace it is about to guard. Run as argv, never through a login shell,
        whose profile resets PATH and drops the local carrier's own bin directory."""
        bound = await self._bound()
        return await self._commands(bound).python(program, *args, timeout_s=timeout_s)

    async def stop_commands(self) -> None:
        """Stop what this turn left running in the container, for a cancel already known to be a
        member's. A carrier whose commands cannot outlive the `exec` that launched them declares no
        stop and needs none — there is nothing left for this to reach."""
        bound = await self._bound()
        if isinstance(bound.carrier, CommandStopping):
            await bound.carrier.stop_commands(bound.handle)

    async def write_file(self, path: str, content: bytes) -> None:
        bound = await self._bound()
        await bound.carrier.write(bound.handle, workspace_path(path), content)

    async def load_skills(self, payload: Mapping[str, object]) -> dict[str, str]:
        """Load system and member skills under the runtime's `$UFO_HOME/skills`."""
        bound = await self._bound()
        if isinstance(bound.carrier, SkillLoading):
            native = True
            result = await bound.carrier.load_skills(bound.handle, payload)
        else:
            native = False
            result = await self._run_staged_skill_load(bound, payload)
        expected: set[str] = set()
        for tier in ("system", "user"):
            entries = payload.get(tier)
            if isinstance(entries, Mapping):
                expected.update(str(name) for name in entries)
        system = payload.get("system")
        system_names = set(str(name) for name in system) if isinstance(system, Mapping) else set()
        refreshed = False
        while True:
            if result.exit_code != 0:
                raise OSError(result.stderr.strip() or "skill load failed")
            try:
                response = json.loads(result.stdout)
                roots = response["roots"]
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise RuntimeError("skill load returned an invalid result") from error
            if not isinstance(roots, dict) or any(
                not isinstance(name, str) or not isinstance(path, str)
                for name, path in roots.items()
            ):
                raise RuntimeError("skill load returned invalid paths")
            unexpected = set(roots) - expected
            if unexpected:
                raise RuntimeError(f"skill load returned unexpected names: {sorted(unexpected)}")
            if (
                native
                or refreshed
                or not (system_names - set(roots))
                or not bound.system_skill_archive
            ):
                return roots
            await self._sync_system_skills(bound)
            result = await self._run_staged_skill_load(bound, payload)
            refreshed = True

    async def _run_staged_skill_load(
        self, bound: "SandboxSession", payload: Mapping[str, object]
    ) -> ExecResult:
        staged = _runtime_path(bound.handle, f"{SKILL_STAGING_DIRNAME}/skill-load-{uuid4()}.json")
        content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        await bound.carrier.write(
            bound.handle,
            staged,
            content,
        )
        return await self._exec_skill(
            bound,
            (
                "python3",
                SANDBOX_PYTHON_FLAG,
                "-c",
                f"{SANDBOX_MODULE_BOOTSTRAP}{SKILL_LOAD_PROG}",
                staged,
                SYSTEM_SKILLS_ROOT,
                _runtime_root(bound.handle),
                hashlib.sha256(content).hexdigest(),
            ),
        )

    async def _sync_system_skills(self, bound: "SandboxSession") -> None:
        staged = _runtime_path(bound.handle, f"{SKILL_STAGING_DIRNAME}/system-skills-{uuid4()}.zip")
        await bound.carrier.write(bound.handle, staged, bound.system_skill_archive)
        result = await self._exec_skill(
            bound,
            (
                "python3",
                SANDBOX_PYTHON_FLAG,
                "-c",
                f"{SANDBOX_MODULE_BOOTSTRAP}{SYSTEM_SKILL_SYNC_PROG}",
                staged,
                SYSTEM_SKILLS_ROOT,
                _runtime_root(bound.handle),
                hashlib.sha256(bound.system_skill_archive).hexdigest(),
            ),
        )
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or "system skill sync failed")

    async def _exec_skill(self, bound: "SandboxSession", argv: tuple[str, ...]) -> ExecResult:
        if not isinstance(bound.carrier, SkillExecuting):
            raise RuntimeError("sandbox carrier cannot execute privileged skill programs")
        return await bound.carrier.exec_skill(
            bound.handle, argv, timeout_s=DEFAULT_EXEC_TIMEOUT_SECONDS
        )

    async def ensure_tool_output_dir(self) -> bool:
        """Guarantee the engine's private runtime offload dir exists, reclaiming a
        non-directory squatting the name — a bare `mkdir -p` fails `File exists` when a file or
        broken symlink already occupies it, so a member write to that name would otherwise poison
        every later offload. The target is fixed to `TOOL_OUTPUT_DIR`, never a caller-supplied path,
        so this destructive reclaim can only ever touch the engine's own namespace, never member
        data. Returns whether a squatter was reclaimed."""
        bound = await self._bound()
        target = _runtime_path(bound.handle, TOOL_OUTPUT_DIRNAME)
        result = await bound.carrier.exec(
            bound.handle,
            (
                "sh",
                "-c",
                'if [ -d "$1" ]; then exit 0; fi; '
                'if [ -e "$1" ] || [ -L "$1" ]; then rm -f "$1" && printf r; fi; '
                'mkdir -p "$1"',
                "sh",
                target,
            ),
            timeout_s=30,
        )
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"cannot ensure {target}")
        return result.stdout == "r"

    async def file_exists(self, path: str) -> bool:
        target = workspace_path(path)
        bound = await self._bound()
        result = await bound.carrier.exec(
            bound.handle, ("sh", "-c", 'test -f "$1"', "sh", target), timeout_s=30
        )
        return result.exit_code == 0

    async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]:
        """Run one in-sandbox file op through the carrier and return its parsed JSON. A `path` arg
        is workspace-scoped except for reads, globs, and greps under this run or the skill tree."""
        params = dict(args)
        raw_path = params.get("path")
        root = WORKSPACE_DIR
        bound = await self._bound()
        if isinstance(raw_path, str):
            runtime_root = _runtime_root(bound.handle)
            runtime_display = str(
                PurePosixPath(f"${UFO_HOME_ENV}")
                / RUNTIME_DIRNAME
                / PurePosixPath(runtime_root).name
            )
            if raw_path == runtime_display or raw_path.startswith(f"{runtime_display}/"):
                if op not in {"read", "glob", "grep"}:
                    raise ValueError(f"path {raw_path!r} escapes {WORKSPACE_DIR}")
                params["path"] = rooted_path(raw_path, runtime_display).replace(
                    runtime_display, runtime_root, 1
                )
                root = runtime_root
            elif raw_path == runtime_root or raw_path.startswith(f"{runtime_root}/"):
                if op not in {"read", "glob", "grep"}:
                    raise ValueError(f"path {raw_path!r} escapes {WORKSPACE_DIR}")
                params["path"] = rooted_path(raw_path, runtime_root)
                root = runtime_root
            elif raw_path == "$UFO_HOME/skills" or raw_path.startswith("$UFO_HOME/skills/"):
                if op not in {"read", "glob", "grep"}:
                    raise ValueError(f"path {raw_path!r} escapes {WORKSPACE_DIR}")
                params["path"] = rooted_path(raw_path, "$UFO_HOME/skills")
                root = "$UFO_HOME/skills"
            elif raw_path == SYSTEM_SKILLS_ROOT or raw_path.startswith(f"{SYSTEM_SKILLS_ROOT}/"):
                if op not in {"read", "glob", "grep"}:
                    raise ValueError(f"path {raw_path!r} escapes {WORKSPACE_DIR}")
                params["path"] = rooted_path(raw_path, SYSTEM_SKILLS_ROOT)
                root = SYSTEM_SKILLS_ROOT
            elif raw_path.startswith("$UFO_HOME/"):
                raise ValueError(f"path {raw_path!r} escapes {WORKSPACE_DIR}")
            else:
                params["path"] = workspace_path(raw_path)
        params["workspace"] = root
        return await bound.carrier.file_op(bound.handle, op, params)

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        """A workspace or current-runtime file's bytes in bounded chunks."""
        return self._read_scoped_file(path)

    async def _read_scoped_file(self, path: str) -> AsyncIterator[bytes]:
        bound = await self._bound()
        runtime_root = _runtime_root(bound.handle)
        runtime_display = str(
            PurePosixPath(f"${UFO_HOME_ENV}") / RUNTIME_DIRNAME / PurePosixPath(runtime_root).name
        )
        if path == runtime_display or path.startswith(f"{runtime_display}/"):
            target = rooted_path(path, runtime_display).replace(runtime_display, runtime_root, 1)
        elif path == runtime_root or path.startswith(f"{runtime_root}/"):
            target = rooted_path(path, runtime_root)
        else:
            target = workspace_path(path)
        async for chunk in bound.carrier.read(bound.handle, target):
            yield chunk

    async def _read_file(self, target: str) -> AsyncIterator[bytes]:
        bound = await self._bound()
        async for chunk in bound.carrier.read(bound.handle, target):
            yield chunk

    async def dial(self, port: int) -> DialTarget:
        """The externally dialable target for an in-sandbox `port`, from the carrier's own
        reachability map — address, TLS, and any header the wire requires (e2b's traffic token)."""
        bound = await self._bound()
        return await bound.carrier.dial(bound.handle, port)


@dataclass(frozen=True)
class SandboxSession(Sandbox):
    """The per-turn handle a tool holds: bash runs in the container through the carrier; file reads
    and writes go through the carrier too, so the same scoping and proxy rules apply whether a byte
    arrives via a shell command or a file op."""

    carrier: Carrier
    handle: SandboxHandle
    system_skill_archive: bytes = b""

    @property
    def conversation_id(self) -> UUID:
        return self.handle.conversation_id

    @property
    def turn_id(self) -> UUID | None:
        return self.handle.turn_id

    @property
    def created(self) -> bool:
        return True

    async def _bound(self) -> "SandboxSession":
        return self

    def authorize(
        self,
        run_token: str,
        cleared_env: frozenset[str],
        env: Mapping[str, str],
    ) -> "SandboxSession":
        current = self.handle.run_token
        if current is None:
            raise RuntimeError("sandbox handle carries no run token")
        authorized = {
            key: (value.replace(current, run_token) if key in PROXY_ENV_NAMES else value)
            for key, value in self.handle.egress_env.items()
            if key not in cleared_env
        }
        if any(current not in self.handle.egress_env.get(name, "") for name in PROXY_ENV_NAMES):
            raise RuntimeError("sandbox proxy environment does not carry its run token")
        return SandboxSession(
            carrier=self.carrier,
            handle=SandboxHandle(
                conversation_id=self.handle.conversation_id,
                container_id=self.handle.container_id,
                workspace_host_path=self.handle.workspace_host_path,
                run_token=run_token,
                egress_env={**authorized, **env},
                turn_id=self.handle.turn_id,
                runtime_root=self.handle.runtime_root,
            ),
            system_skill_archive=self.system_skill_archive,
        )


class _LateSandbox(Sandbox):
    def __init__(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        open: Callable[[], Awaitable[SandboxSession]],
        existing: Callable[[], Awaitable[SandboxSession | None]],
    ) -> None:
        self._conversation_id = conversation_id
        self._turn_id = turn_id
        self._open = open
        self._existing = existing
        self._lock = asyncio.Lock()
        self._session: SandboxSession | None = None

    @property
    def conversation_id(self) -> UUID:
        return self._conversation_id

    @property
    def turn_id(self) -> UUID:
        return self._turn_id

    @property
    def created(self) -> bool:
        return self._session is not None

    def authorize(
        self,
        run_token: str,
        cleared_env: frozenset[str],
        env: Mapping[str, str],
    ) -> Sandbox:
        return _AuthorizedSandbox(late=self, run_token=run_token, cleared_env=cleared_env, env=env)

    async def _bound(self) -> SandboxSession:
        if self._session is None:
            async with self._lock:
                if self._session is None:
                    self._session = await self._open()
        return self._session

    async def stop_commands(self) -> None:
        bound = self._session if self._session is not None else await self._existing()
        if bound is not None and isinstance(bound.carrier, CommandStopping):
            await bound.carrier.stop_commands(replace(bound.handle, turn_id=self._turn_id))


@dataclass(frozen=True)
class _AuthorizedSandbox(Sandbox):
    late: _LateSandbox
    run_token: str
    cleared_env: frozenset[str]
    env: Mapping[str, str]

    @property
    def conversation_id(self) -> UUID:
        return self.late.conversation_id

    @property
    def turn_id(self) -> UUID:
        return self.late.turn_id

    @property
    def created(self) -> bool:
        return self.late.created

    def authorize(
        self,
        run_token: str,
        cleared_env: frozenset[str],
        env: Mapping[str, str],
    ) -> Sandbox:
        return self.late.authorize(run_token, cleared_env, env)

    async def _bound(self) -> SandboxSession:
        return (await self.late._bound()).authorize(self.run_token, self.cleared_env, self.env)
