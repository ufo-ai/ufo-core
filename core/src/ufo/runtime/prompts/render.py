"""The system-prompt render engine: fill a template's slots, validate its vars, digest the result.

A profile template is core-shipped prose with slots the renderer fills: {{agent-prompt}} (the
agent's own instructions — only the shell carries this slot), {{context_window}} (the stable note on
how the window behaves at the boundary — the active context strategy owns the words, so a deploy
never promises a tool its boundary does not offer), {{skill_index}} (the loadable-skill
<available_skills> block), {{sections}} (the capability sections packs contribute — the seam),
{{citation}} (the one shared citation block, kept in citation.md and injected here),
and {{knowledge_cutoff}} (the resolved model's knowledge boundary from knowledge_cutoff.md — the
`ModelSpec.knowledge_cutoff` fact, which every registered model declares).

Vars ({{under_scored}}) inside the agent prompt are substituted from a caller-supplied mapping under
strict both-ends validation — every declared var supplied, every supplied var declared — so a
missing or stray var fails loud rather than reaching the model as a literal brace. After every slot
is filled a leftover {{var}} is itself a loud failure: a shell hole nothing fills, or a pack section
smuggling a slot, is a bug, never silently shipped to the model."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from ufo.runtime.turns.delivery_register import DELIVERY_REGISTER_BLOCK

PROMPT_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
BLANK_RUN_RE = re.compile(r"\n{3,}")

AGENT_PROMPT_SLOT = "{{agent-prompt}}"
SKILL_INDEX_SLOT = "{{skill_index}}"
SECTIONS_SLOT = "{{sections}}"
CITATION_SLOT = "{{citation}}"
CONTEXT_WINDOW_SLOT = "{{context_window}}"
DELIVERY_REGISTER_SLOT = "{{delivery_register}}"
KNOWLEDGE_CUTOFF_SLOT = "{{knowledge_cutoff}}"
CUTOFF_VAR = "{{cutoff}}"

_PROMPTS_DIR = Path(__file__).parent
CITATION_BLOCK = (_PROMPTS_DIR / "citation.md").read_text().strip()
SHELL = (
    (_PROMPTS_DIR / "shell.md").read_text().replace(DELIVERY_REGISTER_SLOT, DELIVERY_REGISTER_BLOCK)
)
"""The core shell with every core block filled but `{{context_window}}`: the boundary extension the
deploy selected owns those words and ships the file they live in, and the renderer fills the slot
from the selected spec rather than at import."""
KNOWLEDGE_CUTOFF_BLOCK = (_PROMPTS_DIR / "knowledge_cutoff.md").read_text().strip()
SUBAGENT_OUTPUT_DISCIPLINE = (
    (_PROMPTS_DIR / "subagent_shell.md")
    .read_text()
    .strip()
    .replace(CITATION_SLOT, CITATION_BLOCK)
    .replace(DELIVERY_REGISTER_SLOT, DELIVERY_REGISTER_BLOCK)
)
"""The subagent shell with every core block filled but `{{context_window}}`: the strategy the deploy
runs owns those words, and `subagent_system_prompt` fills the slot per deploy rather than at
import."""


@dataclass(frozen=True)
class RenderedPrompt:
    """The rendered system prompt and the digest that identifies its content — the turn sends the
    content to the model and stamps the digest into observability, so a prompt change is visible."""

    digest: str
    content: str


def rendered_prompt(content: str) -> RenderedPrompt:
    return RenderedPrompt(digest="sha256:" + sha256(content.encode()).hexdigest(), content=content)


def render_system_prompt(
    agent_prompt: str,
    sections: Sequence[tuple[str, str]],
    skills: Sequence[tuple[str, str]] = (),
    *,
    knowledge_cutoff: str,
    context_window: str = "",
) -> RenderedPrompt:
    """The main agent's system prompt: the core shell with the agent's own prompt, the pack-
    contributed capability sections, the loadable-skill index, and the model's knowledge cutoff
    slotted in. `knowledge_cutoff` is the resolved model's `ModelSpec.knowledge_cutoff` (a machine
    `YYYY-MM` date) — the spec guarantees every model declares one, so the prompt never ships
    without the boundary. The machine `YYYY-MM` date renders to the human month the prompt shows —
    `"2026-02"` → `"February 2026"`. `context_window` is the active context strategy's own note on
    how the window behaves at its boundary; a deploy that hands none renders no note rather than the
    words of a strategy it does not run."""
    human_cutoff = datetime.strptime(knowledge_cutoff, "%Y-%m").strftime("%B %Y")
    block = KNOWLEDGE_CUTOFF_BLOCK.replace(CUTOFF_VAR, human_cutoff)
    return render_template(
        SHELL.replace(KNOWLEDGE_CUTOFF_SLOT, block),
        agent_prompt,
        {},
        skills,
        sections,
        context_window=context_window,
    )


def render_template(
    template: str,
    agent_prompt: str,
    variables: Mapping[str, str],
    skills: Sequence[tuple[str, str]],
    sections: Sequence[tuple[str, str]],
    context_window: str = "",
) -> RenderedPrompt:
    filled_agent_prompt = _substitute_vars(agent_prompt, variables)
    if filled_agent_prompt and AGENT_PROMPT_SLOT not in template:
        raise ValueError(f"prompt template has no {AGENT_PROMPT_SLOT} slot for its agent prompt")
    filled = (
        template.replace(SKILL_INDEX_SLOT, render_skill_index(skills))
        .replace(CITATION_SLOT, CITATION_BLOCK)
        .replace(SECTIONS_SLOT, "\n\n".join(body for _, body in sorted(sections)))
        .replace(AGENT_PROMPT_SLOT, filled_agent_prompt)
        .replace(CONTEXT_WINDOW_SLOT, context_window)
    )
    if unresolved := frozenset(PROMPT_VAR_RE.findall(filled)):
        raise ValueError(f"prompt has unresolved slots: {', '.join(sorted(unresolved))}")
    return rendered_prompt(BLANK_RUN_RE.sub("\n\n", filled).rstrip())


WORKSPACE_FACTS_SECTION = "workspace_capabilities"
WORKSPACE_FACTS_CLOSER = "Already set up — do not offer again."
"""The one rule that holds for every line, whatever the capability is. A caveat about connector
accounts lived here and came out: it only bites where a capability shares a name with a broker
provider (slack, github do; imessage does not), and it is a universal claim that a connector-backed
capability would falsify. The arms measured it as carrying nothing — and an empty closer regresses,
so this sentence stays."""


def render_workspace_facts(lines: Sequence[str]) -> str:
    """The capabilities this workspace already holds, as one block. Each extension contributes one
    line of its own; the closer is stated once here rather than by every extension, so a second
    capability costs a line instead of a paragraph repeating the first one's rules."""
    if not lines:
        return ""
    return "\n".join(
        (
            f"<{WORKSPACE_FACTS_SECTION}>",
            *lines,
            f"</{WORKSPACE_FACTS_SECTION}>",
            WORKSPACE_FACTS_CLOSER,
        )
    )


OBJECT_KINDS_SECTION = "workspace_objects"


def render_object_kinds(kinds: Sequence[tuple[str, str, Sequence[str]]]) -> str:
    """The workspace object kinds this turn can address, one line each with the kind's own
    description and the actions it carries. A deploy registering no kind renders nothing, so the
    prompt never carries an empty tag."""
    if not kinds:
        return ""
    lines: list[str] = []
    for name, description, actions in kinds:
        lines.append(f"- kind: {name} - {description}")
        if actions:
            lines.append(f"  actions: {', '.join(actions)}")
    return "\n".join(
        (
            f"<{OBJECT_KINDS_SECTION}>",
            *lines,
            f"</{OBJECT_KINDS_SECTION}>",
        )
    )


def render_skill_index(skills: Sequence[tuple[str, str]]) -> str:
    if not skills:
        return ""
    return "\n".join(
        (
            "<available_skills>",
            *(f"- {name}: {description}" for name, description in skills),
            "</available_skills>",
        )
    )


def _substitute_vars(template: str, variables: Mapping[str, str]) -> str:
    declared = frozenset(PROMPT_VAR_RE.findall(template))
    supplied = frozenset(variables)
    if missing := declared - supplied:
        raise ValueError(f"prompt vars missing: {', '.join(sorted(missing))}")
    if undeclared := supplied - declared:
        raise ValueError(f"prompt vars undeclared: {', '.join(sorted(undeclared))}")
    return PROMPT_VAR_RE.sub(lambda match: variables[match.group(1)], template)
