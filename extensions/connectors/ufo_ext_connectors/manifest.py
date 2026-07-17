"""What the connectors extension declares: the dynamic connector tool surface and its prompt
section. The tools are workspace-global — they list, describe, search, and execute across every
connector any broker extension registers — so they are declared once here, generic over the
`ConnectorRegistry` core threads onto the turn, never per provider or per broker. The providers
themselves (OAuth descriptors, brokers, catalogs) are each broker extension's own `connectors`
Manifest point."""

from pathlib import Path

from ufo.sdk.manifest import Manifest, PromptSection
from ufo_ext_connectors.objects import CONNECTOR_OBJECT
from ufo_ext_connectors.tools import CONNECTOR_TOOLS

NAME = "connectors"
VERSION = "0.1.0"

SECTION_NAME = "external_tools"
SECTION_BODY = (Path(__file__).parent / "prompts" / "connectors_section.md").read_text().strip()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=CONNECTOR_TOOLS,
        objects=(CONNECTOR_OBJECT,),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
    )
