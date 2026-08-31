"""Environment documents: the overrides a turn tree pins by digest.

A document is written once (YAML or JSON), stored content-addressed through the terminal surface,
and pinned on a turn by `TurnRuntimeConfig.environment`; every turn in that tree — the member turn
and each spawned profile — resolves the same digest and the host applies the block addressed to
it while assembling the turn's prompt, tools, and skills. `main` addresses the member-facing
agent's turns, `profiles.<name>` the turns spawned as that profile, and a turn its document holds
no block for runs unchanged. Top-level `tools` apply wherever a named tool is offered; top-level
`skills` replace or add whole skills. Prompt, description, and skill experiments therefore run
against one shared stack — one document per arm, no server anywhere — and a stored document
replays byte-identically on crash recovery.

Overrides narrow the platform's grants; they cannot widen them. Rewritten text touches only what
the model reads; a removal only shrinks the offer; a scoped name the offer does not hold fails the
turn loud, so an arm whose overrides did not apply never measures as the default. A `run` tool is
the one addition the document may make: its implementation is a command inside the turn's own
sandbox, which grants nothing the sandbox's shell does not already grant."""

import hashlib
import json

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ufo.blob import WorkspaceBlobStore
from ufo.harness.containment import ContainmentError, contained_relative
from ufo.runtime.skills.runtime import parse_skill_content
from ufo.schema.records import ENVIRONMENT_DOCUMENT_RE

ENVIRONMENT_DOCUMENT_BYTE_CAP = 2_000_000
ENVIRONMENT_DOCUMENT_KEY_PREFIX = "environment/"
ENVIRONMENT_FILE_BYTE_CAP = 64_000_000
ENVIRONMENT_FILE_KEY_PREFIX = "environment/files/"
SKILL_MD = "SKILL.md"


class TextEdit(BaseModel):
    """One edit against a text the platform assembled — a system prompt or a skill's `SKILL.md`:
    `old` must appear exactly once. A block lists as many edits as the arm needs; they apply in
    order, so an arm stays anchored to the live text and fails loud when its anchor drifts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    old: str = Field(min_length=1)
    new: str


class PromptOverride(BaseModel):
    """Either a whole replacement (`text`) or in-place edits (`replace`), never both: `text` is
    faithful where the assembled prompt is static (a profile's), `replace` where it is rendered
    per turn (the member agent's)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str | None = None
    replace: tuple[TextEdit, ...] = ()

    @model_validator(mode="after")
    def _one_form(self) -> "PromptOverride":
        if (self.text is None) == (not self.replace):
            raise ValueError("a prompt override is either `text` or `replace`, exactly one")
        return self


class ToolInput(BaseModel):
    """One input field of a `run` tool, rendered into the schema the model reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = Field(default="string", pattern=r"^(string|integer|number|boolean)$")
    description: str = Field(min_length=1)
    required: bool = True


class ToolOverride(BaseModel):
    """What a document does to one named tool. Without `run`: rewrite its description and per-field
    `parameters` descriptions, or withhold it (`enabled: false`, alone). With `run`: define the
    tool as a command in the turn's sandbox — its `input` fields arrive as `INPUT_<NAME>`
    environment variables and its stdout is the result — replacing the named tool's implementation
    when the offer holds the name and adding the tool when it does not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str | None = None
    parameters: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    input: dict[str, ToolInput] = Field(default_factory=dict)
    run: str | None = None

    @model_validator(mode="after")
    def _one_meaning(self) -> "ToolOverride":
        if not self.enabled and (
            self.description is not None or self.parameters or self.input or self.run is not None
        ):
            raise ValueError("`enabled: false` withholds the tool and takes no other field")
        if self.run is None and self.input:
            raise ValueError("`input` defines a `run` tool's fields and requires `run`")
        if self.run is not None and self.parameters:
            raise ValueError(
                "a `run` tool defines its own `input`; `parameters` rewrite an existing model"
            )
        if self.run is None and self.description is None and not self.parameters and self.enabled:
            raise ValueError("a tool override must change something")
        return self


class SkillEdit(BaseModel):
    """In-place edits against an existing skill's whole `SKILL.md` — frontmatter included, so a
    routing-description ablation is one edit. The edited file must still parse as the skill it
    names; adding a skill takes the full text instead."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    replace: tuple[TextEdit, ...] = Field(min_length=1)


class EnvironmentOverrides(BaseModel):
    """One target's overrides: the model it runs on, the prompt it runs with, and the tools in
    its offer. `model` outranks the turn tree's `x-ufo-model` pin for this target alone, and it
    reaches even an own-account profile — the document is the workspace's explicit, attested
    spend choice, unlike the header pin such a profile still ignores — with the turn billing the
    workspace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str | None = Field(default=None, min_length=1)
    prompt: PromptOverride | None = None
    tools: dict[str, ToolOverride] = Field(default_factory=dict)


class EnvironmentDocument(BaseModel):
    """One stored document. `main` and `profiles.<name>` address one target each and fail loud on
    a tool name that target's offer does not hold; top-level `tools` apply to every turn wherever
    the name is offered (a `run` entry is offered everywhere); `skills` maps a skill name to the
    full `SKILL.md` that replaces it — keeping an existing skill's bundled files — or adds it;
    `files` maps a workspace-relative destination to the digest of a stored file, written into
    every turn's sandbox before the model runs (the client resolves a local path in an authored
    document to its digest by uploading it, so the stored document holds only digests). A skills
    value is the full replacement `SKILL.md`, or `replace` edits against the existing one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    main: EnvironmentOverrides | None = None
    profiles: dict[str, EnvironmentOverrides] = Field(default_factory=dict)
    tools: dict[str, ToolOverride] = Field(default_factory=dict)
    skills: dict[str, str | SkillEdit] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _entries_parse(self) -> "EnvironmentDocument":
        for name, text in self.skills.items():
            if isinstance(text, str):
                parse_skill_content(
                    name.rsplit("/", 1)[-1], {SKILL_MD: text.encode()}, registry_name=name
                )
        for destination, digest in self.files.items():
            if not destination or destination.startswith("/"):
                raise ValueError(
                    f"a file destination is a workspace-relative path: {destination!r}"
                )
            try:
                contained_relative(destination, "/workspace")
            except ContainmentError as error:
                raise ValueError(
                    f"a file destination is a workspace-relative path: {destination!r}"
                ) from error
            if ENVIRONMENT_DOCUMENT_RE.fullmatch(digest) is None:
                raise ValueError(
                    f"a stored document's file value is a sha256 digest: {destination!r}"
                )
        return self


def parse_environment_document(body: bytes) -> tuple[EnvironmentDocument, bytes, str]:
    """Parse one document (JSON or YAML), returning the document, the canonical JSON bytes that
    are stored, and the digest that pins them. Canonicalizing before digesting means the same
    document pins the same digest whatever serialization the author wrote it in."""
    if len(body) > ENVIRONMENT_DOCUMENT_BYTE_CAP:
        raise ValueError(
            f"environment document is {len(body)} bytes; the cap is {ENVIRONMENT_DOCUMENT_BYTE_CAP}"
        )
    try:
        loaded = yaml.safe_load(body)
    except yaml.YAMLError as error:
        raise ValueError(f"environment document is not valid YAML or JSON: {error}") from error
    document = EnvironmentDocument.model_validate(loaded)
    canonical = json.dumps(
        document.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return document, canonical, f"sha256:{hashlib.sha256(canonical).hexdigest()}"


async def store_environment_document(blob: WorkspaceBlobStore, body: bytes) -> str:
    _, canonical, digest = parse_environment_document(body)
    await blob.put(ENVIRONMENT_DOCUMENT_KEY_PREFIX + digest.removeprefix("sha256:"), canonical)
    return digest


async def load_environment_document(blob: WorkspaceBlobStore, digest: str) -> EnvironmentDocument:
    if ENVIRONMENT_DOCUMENT_RE.fullmatch(digest) is None:
        raise ValueError(f"environment document digest is malformed: {digest!r}")
    stored = await blob.get(ENVIRONMENT_DOCUMENT_KEY_PREFIX + digest.removeprefix("sha256:"))
    if f"sha256:{hashlib.sha256(stored).hexdigest()}" != digest:
        raise ValueError("stored environment document does not match its digest")
    return EnvironmentDocument.model_validate_json(stored)


async def store_environment_file(blob: WorkspaceBlobStore, body: bytes) -> str:
    """Store one file a document references, content-addressed — the raw bytes, no parsing, so
    the same archive uploaded across arms and runs lands on one key."""
    if len(body) > ENVIRONMENT_FILE_BYTE_CAP:
        raise ValueError(
            f"environment file is {len(body)} bytes; the cap is {ENVIRONMENT_FILE_BYTE_CAP}"
        )
    digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
    await blob.put(ENVIRONMENT_FILE_KEY_PREFIX + digest.removeprefix("sha256:"), body)
    return digest


async def load_environment_file(blob: WorkspaceBlobStore, digest: str) -> bytes:
    stored = await blob.get(ENVIRONMENT_FILE_KEY_PREFIX + digest.removeprefix("sha256:"))
    if f"sha256:{hashlib.sha256(stored).hexdigest()}" != digest:
        raise ValueError("stored environment file does not match its digest")
    return stored
