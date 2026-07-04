"""The assistant pack: a coherent assistant config activated as one named pack.

Activating it (config `[pack] name = "assistant"`) narrows the deploy to exactly the extensions it
names — durable memory and recall, the sandbox browser/computer-use tools driving core's default
BUA backend, Composio-brokered connectors, and Exa web research — together with the base-pinned
index and embed backends memory retrieves through. It bundles only extensions and adds no pack-level
skills or onboarding of its own: each capability's tools, skills, and onboarding ride that
extension's own manifest, so the pack is nothing but the set that comes up together."""

from selfhost.sdk.manifest import Pack

NAME = "assistant"
VERSION = "0.1.0"
EXTENSIONS = ("memory", "index-default", "embed-openai", "browser", "connectors", "exa")


def pack() -> Pack:
    return Pack(name=NAME, version=VERSION, extensions=EXTENSIONS)
