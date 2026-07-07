"""The system-prompt render engine: fill a template's slots, validate its vars, digest the result.

A profile template is core-shipped prose with slots the renderer fills: {{agent-prompt}} (the
agent's own instructions — only the shell carries this slot), {{skill_index}} (the loadable-skill
<available_skills> block), {{sections}} (the capability sections packs contribute — the seam), and
{{citation}} (the one shared citation block, kept in citation.md and injected here).

Vars ({{under_scored}}) inside the agent prompt are substituted from a caller-supplied mapping under
strict both-ends validation — every declared var supplied, every supplied var declared — so a
missing or stray var fails loud rather than reaching the model as a literal brace. After every slot
is filled a leftover {{var}} is itself a loud failure: a shell hole nothing fills, or a pack section
smuggling a slot, is a bug, never silently shipped to the model."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

PROMPT_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
BLANK_RUN_RE = re.compile(r"\n{3,}")

AGENT_PROMPT_SLOT = "{{agent-prompt}}"
SKILL_INDEX_SLOT = "{{skill_index}}"
SECTIONS_SLOT = "{{sections}}"
CITATION_SLOT = "{{citation}}"

_PROMPTS_DIR = Path(__file__).parent
SHELL = (_PROMPTS_DIR / "shell.md").read_text()
CITATION_BLOCK = (_PROMPTS_DIR / "citation.md").read_text().strip()
COMPACTION_SYSTEM_PROMPT = (_PROMPTS_DIR / "compaction.md").read_text().strip()


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
) -> RenderedPrompt:
    """The main agent's system prompt: the core shell with the agent's own prompt, the pack-
    contributed capability sections, and the loadable-skill index slotted in."""
    return render_template(SHELL, agent_prompt, {}, skills, sections)


def render_template(
    template: str,
    agent_prompt: str,
    variables: Mapping[str, str],
    skills: Sequence[tuple[str, str]],
    sections: Sequence[tuple[str, str]],
) -> RenderedPrompt:
    filled_agent_prompt = _substitute_vars(agent_prompt, variables)
    if filled_agent_prompt and AGENT_PROMPT_SLOT not in template:
        raise ValueError(f"prompt template has no {AGENT_PROMPT_SLOT} slot for its agent prompt")
    filled = (
        template.replace(SKILL_INDEX_SLOT, render_skill_index(skills))
        .replace(CITATION_SLOT, CITATION_BLOCK)
        .replace(SECTIONS_SLOT, "\n\n".join(body for _, body in sorted(sections)))
        .replace(AGENT_PROMPT_SLOT, filled_agent_prompt)
    )
    if unresolved := frozenset(PROMPT_VAR_RE.findall(filled)):
        raise ValueError(f"prompt has unresolved slots: {', '.join(sorted(unresolved))}")
    return rendered_prompt(BLANK_RUN_RE.sub("\n\n", filled).rstrip())


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
