import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from ufo_ext_gbrain.manifest import manifest as gbrain_manifest
from ufo_ext_memory.manifest import (
    RECALL_CONTEXT_PREFIX,
    RECALL_HEAD_PREFIX,
    RECALL_TRUNCATION_MARK,
)
from ufo_ext_memory.manifest import manifest as memory_manifest
from ufo_ext_memory.objects import MEMORY_UNDELETABLE, MEMORY_UPDATE_REFUSAL
from ufo_ext_rag.manifest import PREFACE
from ufo_ext_rag.manifest import manifest as rag_manifest
from ufo_ext_research.manifest import manifest as research_manifest
from ufo_ext_sources.manifest import manifest as sources_manifest
from ufo_ext_sources.tools import ALERT_CLOSING, ALERT_NAMED_MAX, alert_message
from ufo_ext_sources.triggers import SourceTrigger

from ufo.host.tools.builtins import BUILTIN_TOOLS
from ufo.runtime.access.grants import FeedConnection
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.pages import PageChange

REPO = Path(__file__).resolve().parents[3]
PIN = Path(__file__).with_name("model_read_text.json")
EXTENSIONS = ("memory", "sources", "gbrain", "rag", "research")
CORE_TOOLS = ("connect_account",)
type ModelReadText = dict[str, dict[str, object]]
AT = datetime(2026, 1, 1, tzinfo=UTC)
ALERT_LOG_PATH = "/workspace/alerts/feed.jsonl"


def _manifests() -> tuple[Manifest, ...]:
    return (
        memory_manifest(),
        sources_manifest(),
        gbrain_manifest(),
        research_manifest(),
        rag_manifest(),
    )


def _files() -> dict[str, object]:
    found = sorted(
        path
        for name in EXTENSIONS
        for folder in ("prompts", "skills")
        for root in (REPO / "extensions" / name).rglob(folder)
        if root.is_dir() and "__pycache__" not in root.parts
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    return {
        path.relative_to(REPO).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in found
    }


def _alerts() -> dict[str, object]:
    connection = FeedConnection(
        id=UUID(int=1),
        provider="github",
        label="GitHub",
        account_id="acme",
        base_url=None,
        backfill_days=None,
        owner_member_id=None,
        shared=True,
    )
    trigger = SourceTrigger(
        id=UUID(int=2),
        conversation_id=UUID(int=3),
        agent_id=UUID(int=4),
        connection_id=connection.id,
        name="acme-ufo-7",
        description="acme/ufo#7",
        when=(),
        fault=None,
        delivery="current",
        paused=False,
        created_by_member_id=None,
        internet_access=None,
        created_at=AT,
        updated_at=AT,
    )
    changes = [
        PageChange(
            page_id=UUID(int=100 + index),
            source_id=UUID(int=5),
            connection_id=UUID(int=6),
            provider="github",
            subject="",
            stream="pull_requests",
            title=f"Pull request {index}",
            body="",
            digest="",
            revision=index,
            tombstone=False,
            indexed=True,
            created_at=AT,
            as_of=AT,
            changed_at=AT,
        )
        for index in range(ALERT_NAMED_MAX + 1)
    ]
    return {
        "alert_message:named": alert_message(connection, trigger, changes[:1], ALERT_LOG_PATH),
        "alert_message:log": alert_message(connection, trigger, changes, ALERT_LOG_PATH),
        "alert_message:listed": alert_message(connection, trigger, changes, None),
    }


def collect() -> ModelReadText:
    """Every text a model reads from the knowledge extensions and `connect_account`."""
    manifests = _manifests()
    tools = [tool for manifest in manifests for tool in manifest.tools]
    tools += [tool for tool in BUILTIN_TOOLS if tool.name in CORE_TOOLS]
    strings: dict[str, object] = {
        "MEMORY_UNDELETABLE": MEMORY_UNDELETABLE,
        "MEMORY_UPDATE_REFUSAL": MEMORY_UPDATE_REFUSAL,
        "ALERT_CLOSING": ALERT_CLOSING,
        "PREFACE": PREFACE,
        "RECALL_CONTEXT_PREFIX": RECALL_CONTEXT_PREFIX,
        "RECALL_HEAD_PREFIX": f"\n{RECALL_HEAD_PREFIX}",
        "RECALL_TRUNCATION_MARK": RECALL_TRUNCATION_MARK,
    }
    strings |= _alerts()
    strings |= {
        f"credential:{manifest.name}:{slot.name}": slot.description
        for manifest in manifests
        for slot in manifest.credentials
    }
    return {
        "tools": {
            tool.canonical_id: {
                "description": tool.description,
                "schema": tool.schema().input_schema,
            }
            for tool in tools
        },
        "sections": {
            f"{manifest.name}:{section.name}": section.body
            for manifest in manifests
            for section in manifest.prompt_sections
        },
        "kinds": {
            kind.name: {
                "description": kind.description,
                "guidance": kind.guidance,
                "schema": kind.spec_model.model_json_schema(),
            }
            for manifest in manifests
            for kind in manifest.objects
        },
        "files": _files(),
        "strings": strings,
    }


def render(text: ModelReadText) -> str:
    """The pin's bytes: sorted keys, two-space indent, a closing newline."""
    return json.dumps(text, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        raise SystemExit("Usage: model_read_text.py --write")
    PIN.write_text(render(collect()), encoding="utf-8")
