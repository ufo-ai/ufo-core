# SDK agent context, models, tools, and prompt-safety exports  `stage-20.1.2`

This stage is shared support for extension authors. It is not the main work loop itself. Instead, it provides stable “front doors” into parts of the system that outside code is allowed to use, so extensions do not have to depend on deeper internal files that may change.

The context module gathers the pieces an extension needs to understand the current run, such as the agent, conversation, pages, credentials, models, and saved state. The models module exposes the shapes of messages, model clients, tool calls, pricing details, and access grants. The tools and skills modules provide the approved building blocks for adding actions and higher-level abilities. The authority module exposes objects that describe what an extension is allowed to execute. The audience module gives shared names and helpers for who a message is meant for. The delivery register module exports standard prompt text used when speaking to the model. Finally, untrusted provides a common way to mark outside text as unsafe to trust directly. Together, these files form the public SDK shelf of labeled tools.

## Files in this stage

### Conversation Context and Authority
Public exports for understanding who is involved in a conversation, what authority is available, and what contextual state extensions can access.

### `core/src/ufo/sdk/audience.py`

`data_model` · `cross-cutting`

This file does not define new behavior itself. Instead, it re-exports audience-related pieces from the deeper runtime package, which is where the real implementation lives. In plain terms, an “audience” describes who a conversation turn is meant for: for example, a shared audience, a room-specific audience, or an audience in a foreign room. Without this file, SDK users would need to know the project’s internal folder layout and import from `ufo.runtime.turns.audience` directly. That would make the public interface fragile, because internal files could move or change names. This file acts like a front desk: it points users to the right audience tools while hiding the hallway map behind it. It exposes the `Audience` value, constants such as `SHARED_AUDIENCE` and `FOREIGN_AUDIENCE_PREFIX`, and helper functions for building, parsing, and displaying audience values. Because every import is written as an explicit alias, the names exported by this SDK module are clear and intentional.


### `core/src/ufo/sdk/authority.py`

`data_model` · `cross-cutting`

This file is a small public-facing doorway. The real authority logic lives in `ufo.runtime.authority`, but outside code should not have to know that internal location. Instead, this file re-exports the important names from the runtime layer under `ufo.sdk.authority`.

In plain terms, an “execution authority” is the permission context for work owned by an extension. It answers questions like: which workspace or member is this work allowed to act as? If this file did not exist, extension code would need to import these authority tools from an internal runtime module, which would make the public API more fragile. Moving or reorganizing runtime code could then break extension authors.

The file does not create new behavior. It imports and republishes a workspace-wide authority value, authority classes, an error used when authority is missing, and helper functions for converting between member IDs and authority objects. It is like a reception desk that points visitors to the right internal office while keeping the building layout hidden.


### `core/src/ufo/sdk/context.py`

`other` · `cross-cutting import-time SDK surface`

This file acts like a front desk for the SDK. The real implementations live deeper inside the project, mostly under runtime and schema modules. Instead of asking extension authors to know those internal paths, this file lets them import from `ufo.sdk.context`, which is simpler and less likely to change.

The objects re-exported here describe the world an extension is running in. For example, `ExtensionContext` is the main bundle of information an extension receives, `ConversationFacts` summarizes the conversation, `PageRecord` and `PageState` describe pages, and `CredentialAccess` and `ModelAccess` provide controlled access to secrets and model calls. It also exposes identity and scope helpers, such as `agent_current`, plus records like `AgentChange` and `ProposalRef` that represent changes or references used elsewhere in the system.

The important point is stability. If other code imported directly from the internal runtime modules, every internal move or rename could break extension code. This file creates a clean public surface: internal code can evolve, while extension handlers keep using the same familiar import path.


### Model and Prompt Interfaces
Stable SDK doorways for prompt-delivery text, model clients, message and tool-call shapes, and related model metadata.

### `core/src/ufo/sdk/delivery_register.py`

`util` · `cross-cutting`

This file is a small public doorway into a deeper part of the system. The project has a “delivery register,” which is a shared instruction block added to prompts so that the main shell, subagents, and direct model calls all record their results in the same expected way. Without this shared block, different parts of the system could ask the model to report work differently, which would make results harder to collect and compare.

The actual wording lives in `ufo.runtime.turns.delivery_register`. This file simply imports two named pieces from there and exposes them under the `ufo.sdk.delivery_register` module. That matters because `ufo.sdk` is meant to be the public software development kit: the safer, stable surface that extensions should use instead of reaching into internal runtime modules.

The comment also explains a project rule: `ufo.sdk` keeps its `__init__.py` empty, so public SDK items live in explicit named files like this one. In everyday terms, this file is like a labeled service window. The goods are stored elsewhere, but outsiders are told to pick them up here so the internal storage layout can change later without breaking their code.


### `core/src/ufo/sdk/models.py`

`data_model` · `cross-cutting`

This module is like a front desk for everything an extension needs when talking to language models through UFO. The actual code lives in several internal areas, such as Anthropic support, OpenAI support, shared message formats, model specifications, pricing, and usage records. Rather than asking outside code to know all those internal paths, this file gathers the important names and re-exports them under `ufo.sdk.models`.

That matters because imports are a kind of promise. If an extension imports directly from internal modules, a harmless internal cleanup could break it. By importing from this file instead, extensions depend on a smaller, more stable surface. The file includes client classes such as `AnthropicClient` and `OpenAIClient`, shared request and response building blocks such as `Message`, `ModelRequest`, and stream event types, tool-use structures, image and text block types, model specification and pricing types, and helper values for account grants and API keys.

There is no new logic here. Nothing is transformed or computed. The file simply says, in effect: “these are the model-related pieces we intentionally make available to SDK users.” Without it, extension authors would need to rummage through the project’s internal layout, and the project would have a harder time changing internals without breaking outside code.


### Extension Skills and Tools
Public re-export modules for defining, importing, and using supported skill and tool abstractions in extensions.

### `core/src/ufo/sdk/skills.py`

`other` · `cross-cutting import surface`

This file is like a front desk for the project’s skill system. The real code for skills lives deeper in the runtime area, but outside users should not need to know those internal paths. Instead, they can import from `ufo.sdk.skills`, which is a stable, easy-to-find public module.

It exposes the main value objects used to describe skills, such as `RuntimeSkill` and `SkillCard`. A value object is a small object whose main job is to carry meaningful data, like a card in a catalog. It also exposes `parse_skill_content`, which turns in-memory skill text into structured skill information, and `skill_root`, which helps identify where a skill belongs. Finally, it re-exports `SKILL_LINE_MAX_CHARS` and `lexical_score`, which are used when searching or ranking skills by matching words in a simple text-based way.

The comment explains an important project rule: package `__init__.py` files are kept empty, so public SDK imports are gathered in named modules like this one. Without this file, callers would have to import from internal runtime modules directly, making their code more fragile if the internal layout changes.


### `core/src/ufo/sdk/tools.py`

`util` · `cross-cutting`

This file is like a clearly marked service counter at the front of a building. The useful parts live deeper inside the project, but extension authors should not have to know those internal hallway names. Instead, they import from `ufo.sdk.tools`, which gives them the stable public names they are meant to use.

The file does not define new behavior of its own. It gathers tool-facing pieces from several runtime modules and exposes them under one public SDK module. These include types for tool inputs and outputs, such as text and image content; context objects that tell a tool about the current run; registry types used to declare tools and actions; file-change limits; connection-related errors; preview objects; and task-running helpers.

One especially important export is `run_task`, which lets a tool start longer-running work in a detached task journal. In plain terms, if a command takes longer than the caller can wait, it can keep running in the background and still be tracked through shared task handles. This matters because different tools can report and refer to the same ongoing work in a consistent way.

Without this file, extension code would need to import from internal runtime paths. That would make extensions more fragile, because internal module names and layouts can change even when the public SDK contract stays the same.


### Prompt Safety Marking
Shared SDK export for marking outside text as untrusted using the same safety language as the core runtime.

### `core/src/ufo/sdk/untrusted.py`

`util` · `cross-cutting`

This file is a tiny bridge, but it matters for safety. In this project, some text may come from places the system should not fully trust, such as a tool’s output, a third-party provider response, or a subagent handing back results. That text needs to be clearly separated from trusted instructions, like putting it behind a glass wall: visible, but not allowed to quietly become commands.

Rather than define a new marker in the SDK, this file imports the existing `wall` definition from `ufo.harness.untrusted` and exposes it here as `ufo.sdk.untrusted.wall`. That means extension authors can use the same untrusted-content wrapper as the core runtime. Without this file, SDK users might invent separate wrappers or use a different import path, which could lead to inconsistent safety boundaries.

There is no local behavior here. The important design choice is that there is exactly one shared definition of this untrusted-content boundary, and this file makes it available from the SDK-facing namespace.
