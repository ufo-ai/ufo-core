"""One frozen record per model — the single source of truth for how a model is routed, billed, and
called. Every seam reads its fact off a ModelSpec through the registry; no seam keeps its own
per-model table. A model no registry entry describes fails loud at `registry.spec(id)` rather than
across a mid-turn 400, a render crash, and a silent zero bill. See RFC 0018."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ufo.credentials import CredentialValueInvalid
from ufo.models.interface import ModelClient, ToolSchema
from ufo.models.pricing import ModelPrice
from ufo.schema.records import ReasoningEffort

KNOWLEDGE_CUTOFF_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
KEY_REJECTED_STATUS = 401

ApiSurface = Literal["chat", "responses"]


@dataclass(frozen=True)
class ReasoningSupport:
    """Whether a model does extended reasoning, and whether reasoning composes with tool use on its
    api surface. A client drops reasoning from a tool round when the model declares that combination
    unsupported."""

    supported: bool
    tools_with_reasoning: bool
    default_on: bool = False
    can_disable: bool = True
    minimum: ReasoningEffort = "low"

    def internal_effort(self) -> ReasoningEffort:
        if not self.supported or self.can_disable:
            return "off"
        return self.minimum


@dataclass(frozen=True)
class ModelSpec:
    """Everything one model is: its provider and client builder, its price, its knowledge cutoff,
    its context window, its reasoning capability, and the api surface it is called on. The registry
    keys these by `id`; a seam reads `registry.spec(id).<fact>` instead of owning a per-model dict.

    `client` builds the `ModelClient` from this spec and the api key the registry resolves, once per
    turn. `knowledge_cutoff` is a machine date (`YYYY-MM`) rendered to a human month at the prompt
    seam. `key_slot`/`key_env` name where the registry resolves the api key: the workspace's BYOK
    secret under `key_slot`, else the platform default in env `key_env`; both empty ⇒ keyless."""

    id: str
    provider: str
    client: Callable[[ModelSpec, str], ModelClient]
    price: ModelPrice
    knowledge_cutoff: str
    context_window: int
    reasoning: ReasoningSupport
    api_surface: ApiSurface
    key_slot: str = ""
    key_env: str = ""

    def __post_init__(self) -> None:
        if not KNOWLEDGE_CUTOFF_RE.match(self.knowledge_cutoff):
            raise ValueError(
                f"model {self.id!r} knowledge_cutoff {self.knowledge_cutoff!r} is not YYYY-MM"
            )
        if self.reasoning.tools_with_reasoning and not self.reasoning.supported:
            raise ValueError(
                f"model {self.id!r} declares tools_with_reasoning without reasoning support"
            )
        if self.reasoning.default_on and not self.reasoning.supported:
            raise ValueError(f"model {self.id!r} declares default reasoning without reasoning")

    def key_rejected(self) -> CredentialValueInvalid:
        """The credential fault a provider's 401 is: the key this spec resolved is not one it
        accepts. Typed here rather than left as the provider's own auth error because the verdict is
        deterministic per key — every turn the workspace runs repeats it until that value changes,
        and a generic stream failure attributes it to nothing anyone can replace. The round cannot
        tell which of the two sources the registry read, so the message names both."""
        needed = self.key_env or self.key_slot.upper()
        return CredentialValueInvalid(
            f"model {self.id!r} key was rejected by the provider: env UFO_{needed} (or {needed}) "
            f"or the workspace's {self.key_slot!r} BYOK slot holds a key {self.provider} does not "
            "accept. Replace it."
        )

    def wire_reasoning(
        self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]
    ) -> ReasoningEffort | None:
        """The reasoning setting this request carries, or None when it carries no setting at all:
        the model does not reason, or its api surface refuses reasoning alongside this request's
        tools. `off` is a setting the request states, never the absence of one — a wire that reads
        an absent parameter as the provider's own default effort cannot say `off` by omission. A
        model that cannot disable reasoning clamps `off` to its declared minimum."""
        if not self.reasoning.supported:
            return None
        if tools and not self.reasoning.tools_with_reasoning:
            return None
        if requested == "off" and self.reasoning.default_on and not self.reasoning.can_disable:
            return self.reasoning.minimum
        return requested
