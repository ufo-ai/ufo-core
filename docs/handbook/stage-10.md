# Prompt construction and model streaming  `stage-10`

This stage prepares and runs the conversation with the language model during the main work loop. First, the prompt package makes prompt-building code importable. Its render module assembles the final system prompt: the trusted instruction text that tells the model its role, skills, citation rules, contributed extra sections, and knowledge cutoff. It also records a digest, like a fingerprint, so prompt changes can be noticed later.

Before outside text is added, untrusted.py wraps it so the model treats it as quoted evidence, not as new orders. This helps protect against a web page or tool result trying to hijack the agent.

The models package then provides the doors to real model services. The Anthropic bridge converts UFO’s internal message format into Anthropic’s Messages API request, and translates Anthropic’s streaming reply back into UFO’s common event stream. The OpenAI bridge does the same for OpenAI-style Chat Completions or Responses APIs. The OpenRouter extension adds another OpenAI-like route to many models, plus image and video generation tools that save outputs and record cost.

## Files in this stage

### Prompt rendering
Builds the prompt package and renders the final tracked system prompt from instructions, skills, sections, citations, and model metadata.

### `core/src/ufo/loop/prompts/__init__.py`

`other` · `import/package discovery`

This file is empty on purpose. In Python projects, an `__init__.py` file tells Python that a folder should be treated as a package, meaning its contents can be imported by name from elsewhere in the codebase. You can think of it like putting a label on a drawer: the drawer may contain many useful items, and the label lets the rest of the system find them reliably. Nothing runs here, and no settings or helper functions are defined. Its main value is structural: without it, depending on the Python version and packaging setup, imports involving `ufo.loop.prompts` could fail or behave differently. This matters because prompt files are likely used by the loop system when preparing text instructions or templates for the UFO agent, and this package marker keeps that area of the codebase addressable.


### `core/src/ufo/loop/prompts/render.py`

`domain_logic` · `prompt construction before model calls`

This file is the prompt assembly line. The project keeps prompt text in separate Markdown files, with placeholders such as {{agent-prompt}}, {{sections}}, and {{knowledge_cutoff}} marking where live information must be inserted. This renderer fills those holes and refuses to continue if anything is missing or unexpected.

That strictness matters because an unfilled placeholder could be sent to the model as literal text, which would make the instructions confusing or incomplete. The file checks both sides: if an agent prompt declares a variable, the caller must supply it; if the caller supplies a variable the prompt did not ask for, that is also treated as an error. After all replacements, it scans again for any leftover {{name}} slots and fails loudly if it finds one.

The main flow starts with a shell prompt loaded from disk. It inserts a delivery register block, a citation block, the model’s knowledge cutoff in human-readable form, pack-provided capability sections, and an optional index of loadable skills. Finally, it normalizes extra blank lines, trims the end, and returns both the final text and a SHA-256 digest, which is like a fingerprint for the prompt’s exact content.

#### Function details

##### `rendered_prompt`  (lines 53–54)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This wraps finished prompt text together with a stable fingerprint of that text. The fingerprint helps logs and monitoring show exactly which prompt version was used.

**Data flow**: It receives the final prompt content as plain text. It turns that text into a SHA-256 hash, prefixes it with "sha256:", and returns a RenderedPrompt object containing both the digest and the original content. It does not change anything outside itself.

**Call relations**: After render_template has finished filling and checking a prompt, it calls rendered_prompt as the final packaging step. rendered_prompt hands back the object that the rest of the system can send to the model and record for observability.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 57–74)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: This builds the main agent system prompt from the project’s standard shell template. It adds the agent’s own instructions, skills, capability sections, citation text, and the model’s knowledge cutoff.

**Data flow**: It receives an agent prompt, a list of section name-and-body pairs, an optional list of skills, and a required knowledge cutoff in machine form such as "2026-02". It converts that date into a human form such as "February 2026", inserts it into the knowledge cutoff block, places that block into the shell template, and passes everything to render_template. The result is a RenderedPrompt containing the complete prompt and its digest.

**Call relations**: This is the higher-level entry point for normal system prompt rendering. It relies on datetime parsing to make the cutoff readable, then delegates the detailed filling and validation work to render_template.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 77–95)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This is the central template filler and safety checker. It combines the template, the agent prompt, skills, citation text, and contributed sections into one final prompt, while making sure no placeholder is accidentally left behind.

**Data flow**: It receives a prompt template, an agent prompt, a mapping of variable names to replacement text, a skill list, and a section list. First it substitutes variables inside the agent prompt. If there is agent prompt text but the template has no place for it, it raises an error. Then it replaces the skill index slot, citation slot, sections slot, and agent prompt slot. It scans the finished text for any remaining {{variable}} pattern, raises an error if any remain, collapses long runs of blank lines, trims the end, and returns a RenderedPrompt.

**Call relations**: render_system_prompt calls this after preparing the shell template. Inside the flow, render_template asks _substitute_vars to fill variables safely, asks render_skill_index to format the available skills block, and finally calls rendered_prompt to package the checked result with a digest.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_skill_index`  (lines 98–107)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This turns a list of available skills into the small XML-like block that is inserted into the prompt. If there are no skills, it returns an empty string so the prompt does not show an empty section.

**Data flow**: It receives a sequence of skill name-and-description pairs. With no skills, it outputs empty text. With skills, it creates a block beginning with <available_skills>, adds one bullet line per skill, and ends with </available_skills>.

**Call relations**: render_template calls this when it reaches the {{skill_index}} slot. The formatted skill block is then inserted into the larger prompt alongside the agent instructions, sections, and citation rules.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 110–117)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This fills named variables inside an agent prompt, but only when the declared variables and supplied values match exactly. It prevents silent mistakes such as forgetting a required value or passing a value that the prompt never uses.

**Data flow**: It receives a text template and a mapping from variable names to replacement strings. It finds every {{name}} variable declared in the template and compares that set with the supplied keys. If a declared variable is missing, or an extra undeclared variable was supplied, it raises an error. Otherwise it replaces each variable with its matching value and returns the filled text.

**Call relations**: render_template calls this at the start, before inserting the agent prompt into the larger shell. Its output becomes the safe, fully filled agent prompt that the rest of the rendering flow can use.

*Call graph*: called by 1 (render_template).


### Content trust boundaries
Provides the shared wrapper for presenting external or tool-provided text to the model as untrusted data rather than instructions.

### `core/src/ufo/untrusted.py`

`util` · `cross-cutting`

This file is a small safety barrier. Some text that reaches an agent may come from outside the trusted workspace, such as a web page or a third-party tool result. That text might contain sentences that look like commands. Without a clear wrapper around it, the model could confuse those outside words with real instructions from the system or user.

The file defines a standard “wall” around untrusted content. Think of it like putting a printed note inside a sealed evidence bag: the note can be read, but it is clearly marked as something found elsewhere, not something to obey. The wrapper includes a warning, an opening tag that names the source, the content itself, and a closing tag.

The important detail is that the content is not allowed to break out of its own wrapper. If the outside text already contains the closing tag, this file replaces it with a harmless escaped version. That means malicious or accidental text cannot end the untrusted section early and continue afterward as if it were trusted instructions.

Keeping this in one file matters because several paths can deliver outside content to an agent. By sharing the same wrapper function, those paths use the same warning, same boundary markers, and same escaping rule.

#### Function details

##### `wall`  (lines 22–30)

```
def wall(source: str, content: str) -> str
```

**Purpose**: Wraps a piece of outside content in a clearly marked untrusted-content section. Someone uses this when text should be shown to an agent for reference, but must not be treated as instructions.

**Data flow**: It receives a source name and the content from that source. It builds a warning message, adds an opening untrusted-content tag with the source name, inserts the content after replacing any real closing tag with a harmless escaped version, and then adds the real closing tag at the end. The result is one string that safely labels and contains the untrusted text.

**Call relations**: This function is the shared gate used wherever outside content is passed on to an agent, such as tool results or a background child agent's returned output. Those callers hand it the source and body text, and it hands back a safe wrapped version so each path does not invent its own slightly different boundary format.


### Core model adapters
Defines the model package and adapts UFO conversations to Anthropic and OpenAI-style streaming APIs while normalizing provider events.

### `core/src/ufo/models/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker. In Python projects, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it makes the `core/src/ufo/models` directory available under the name `ufo.models`, so other parts of the project can import model definitions from files placed inside this folder.

There is no code to run here, no classes, and no functions. Its value is structural: it gives the project a clean place to group model-related code. A simple analogy is a labeled drawer in a filing cabinet. The label itself does not contain documents, but it tells everyone where a certain kind of document belongs.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.models` could become less reliable or fail in environments that expect traditional Python packages. Even though it is empty, it helps keep the codebase organized and import paths predictable.


### `core/src/ufo/models/anthropic.py`

`io_transport` · `request handling`

This file lets the rest of the project talk to Anthropic models without needing to know Anthropic's exact request and response format. It is like a travel adapter: UFO has its own plug shape for messages, images, tool calls, reasoning, and usage counts, while Anthropic expects and returns a different shape.

Before sending a request, the file converts UFO content blocks into Anthropic content blocks. Plain text stays plain text. Images become Anthropic base64 image objects. Tool calls and tool results are rewritten into Anthropic's format. One important detail is that reasoning from another provider is dropped, because providers cannot safely understand each other's private reasoning formats.

The main class, `AnthropicClient`, opens a streaming Anthropic request and yields UFO `ModelEvent` objects as pieces arrive. Text is streamed out as text deltas. Tool calls are streamed as a start event followed by partial JSON chunks. Anthropic's hidden reasoning blocks are not streamed live to users; they are collected and yielded only after the stream finishes, just before the final usage record, so the engine can echo them back exactly if Anthropic requires it later.

The file also contains careful retry behavior. Network timeouts, dropped connections, rate limits, and temporary provider errors are retried before any visible output is produced. Once text or a tool call has been yielded, failures are no longer retried, because a second attempt could produce a different answer and confuse the caller. Deterministic client errors, refused API keys, refusals, and truncated completions are surfaced as explicit failures.

#### Function details

##### `anthropic_sdk_client`  (lines 49–53)

```
def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the low-level Anthropic asynchronous SDK client used to contact Anthropic's service. It deliberately turns off the SDK's own retries so this file can apply one clear retry policy itself.

**Data flow**: It takes an Anthropic API key as input. It builds an `AsyncAnthropic` client with that key, a fixed timeout, and no built-in retries. It returns that ready-to-use SDK client for higher-level code to wrap in `AnthropicClient`.

**Call relations**: This is the starting point for making an Anthropic connection. It hands off to the external Anthropic SDK constructor, while the retry decisions are left for `AnthropicClient.complete` when actual model requests are made.

*Call graph*: 1 external calls (AsyncAnthropic).


##### `_anthropic_image`  (lines 56–60)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts UFO's image representation into the image shape Anthropic expects. This is needed whenever a user message or tool result contains an image.

**Data flow**: It receives an `ImageSource`, which contains the image media type and base64 data. It wraps those fields in Anthropic's dictionary format for an image content block. It returns that dictionary without changing anything else.

**Call relations**: This helper is used by `_anthropic_tool_result_part` for image parts inside tool results and by `anthropic_content` for images in normal message content. It keeps image conversion consistent in both places.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 63–68)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of a tool result into Anthropic's content format. A tool result can include plain text or an image, and Anthropic needs each part labeled clearly.

**Data flow**: It receives one tool-result content part. If the part is text, it returns an Anthropic text dictionary. If the part is an image, it sends the image source through `_anthropic_image` and returns the resulting Anthropic image dictionary.

**Call relations**: This helper is called by `anthropic_content` when a tool result contains multiple structured parts instead of just a string. It delegates image conversion to `_anthropic_image` so the same image format is used everywhere.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 71–105)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Translates UFO message content into the content format Anthropic's Messages API accepts. This allows the rest of UFO to use one internal message format even though Anthropic has its own wire format.

**Data flow**: It receives either a plain string or a tuple of UFO content blocks. A string is returned as-is. For blocks, it walks through each one and builds a list of Anthropic dictionaries for thinking, redacted thinking, text, images, tool uses, and tool results. OpenAI-style reasoning items are skipped because Anthropic cannot use another provider's reasoning format.

**Call relations**: This is called by `AnthropicClient.complete` while building the outgoing request. It uses `_anthropic_image` for image blocks and `_anthropic_tool_result_part` for structured tool-result content, then hands the converted content to Anthropic's streaming API call.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (complete).


##### `AnthropicClient.complete`  (lines 113–376)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one model request to Anthropic and streams the answer back as UFO's standard model events. It is the main adapter that makes Anthropic look like any other model provider to the rest of the system.

**Data flow**: It receives a `ModelRequest` containing the model name, system prompt, messages, tools, token limit, reasoning preference, and cache settings. It caches the stable tools and system prefix for one hour and the changing conversation tail for the request's five-minute setting. It converts messages into Anthropic format, adds tool and reasoning options when needed, then starts a streaming API request. As Anthropic events arrive, it turns them into UFO events such as stream-start, text chunks, tool-call starts, tool-call JSON chunks, saved reasoning blocks, and finally a `Usage` record with token counts. It also records cache token usage and raises clear errors for refusals, truncation, rejected credentials, provider failures, and unusable streams.

**Call relations**: This method calls `anthropic_content` before contacting Anthropic so the request is in the right shape. During the stream, it constructs UFO event objects such as `ModelStreamStart`, `TextDelta`, `ToolCallStart`, `ToolCallDelta`, `ThinkingBlock`, and `RedactedThinkingBlock` for callers to consume. If Anthropic reports retryable transport or status problems before anything visible has been yielded, it logs the problem, emits a retry metric, waits, and tries again. Once it has yielded visible output, it stops retrying and lets the error surface, because replaying the request could mix two different answers.

*Call graph*: calls 1 internal fn (anthropic_content); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep (+3 more)).


### `core/src/ufo/models/openai.py`

`io_transport` · `request handling during a model call`

The rest of the system speaks in its own neutral language: messages contain text, images, tool calls, tool results, reasoning items, and usage counts. OpenAI-compatible providers expect a different shape on the wire. This file is the translator and courier between those two worlds.

It supports two OpenAI API styles. The older Chat Completions API is good for normal chat, tool calls, and images, but it has no proper place to replay hidden reasoning from a previous turn. The newer Responses API can carry reasoning items, function calls, images, and tool outputs in a richer format. The model's ModelSpec says which surface to use, so the code does not guess from the model name.

On the way out, helper functions convert UFO messages into the exact OpenAI request format. Images become data URLs. Tool results may be split because Chat Completions only allows text in tool messages, so image results are lifted into a following user message. On the way back, OpenAIClient reads the provider's stream and yields simple events such as stream start, text chunks, tool-call starts, tool-call argument chunks, reasoning blocks, and final token usage.

The file also protects callers from common provider problems. It retries timeouts, dropped connections, rate limits, server errors, and empty responses when it is still safe to retry. Once visible output has been yielded, it stops retrying so the user does not see duplicated partial answers.

#### Function details

##### `openai_sdk_client`  (lines 87–93)

```
def openai_sdk_client(api_key: str, base_url: str | None=None) -> openai.AsyncOpenAI
```

**Purpose**: Creates the underlying asynchronous OpenAI SDK client used to talk to OpenAI or an OpenAI-compatible service. It deliberately turns off the SDK's own retry behavior because this file has its own retry rules.

**Data flow**: It receives an API key and, optionally, a custom base URL for a compatible provider. It builds an AsyncOpenAI client with a fixed timeout and no SDK retries. The result is a ready-to-use network client.

**Call relations**: This is the factory that prepares the low-level OpenAI connection before an OpenAIClient can use it. It hands off actual network behavior to the OpenAI SDK, while retry decisions are left to OpenAIClient's streaming methods.

*Call graph*: 1 external calls (AsyncOpenAI).


##### `_openai_image`  (lines 96–100)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts one internal image source into the image format expected by OpenAI's Chat Completions API. It is a small adapter for embedding base64 image data into a request.

**Data flow**: It receives an ImageSource containing a media type and base64 data. It wraps that information in a data URL inside OpenAI's image_url structure. The output is a dictionary that can be placed into an OpenAI message.

**Call relations**: This helper is used when building normal chat messages and when splitting image-containing tool results. It keeps image formatting consistent anywhere Chat Completions needs image parts.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 103–119)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: Separates a tool result into the text part OpenAI can accept in a tool message and the image parts that must be sent another way. This matters because Chat Completions tool messages are text-only.

**Data flow**: It receives either a plain string result or a tuple of text and image blocks. If the result is text, it returns that text and no images. If it contains blocks, it joins text blocks with newlines and converts image blocks into OpenAI image dictionaries. The output is a pair: tool-message text plus a list of images.

**Call relations**: openai_messages calls this while translating UFO tool result blocks. When images appear in a tool result, this helper prepares them so openai_messages can place the text in the tool reply and lift the images into a following user message.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 122–181)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns UFO's internal conversation history into Chat Completions messages. It preserves the parts that Chat Completions can understand and drops reasoning blocks because that API has no field for them.

**Data flow**: It receives a system prompt and a tuple of internal messages. It first trims images as needed, then walks each message block by block. Text becomes message content, images become image parts, tool-use blocks become OpenAI function tool calls, and tool-result blocks become tool messages. If a tool result includes images, those images are moved into a following user message. The result is a list of dictionaries ready for the Chat Completions request.

**Call relations**: OpenAIClient._chat_kwargs calls this when preparing a Chat Completions request. It also relies on _openai_image and _openai_tool_result for image and tool-output translation.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 184–267)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: Turns UFO's internal messages into the richer input item format used by OpenAI's Responses API. Unlike Chat Completions, this format can carry reasoning items back to the provider.

**Data flow**: It receives the stored conversation messages. It trims images, then converts simple string messages directly. For block-based messages, it keeps Responses-style reasoning items, converts text and images into input content, converts tool-use blocks into function-call items, and converts tool results into function-call-output items. The output is an ordered list of Responses API input items.

**Call relations**: responses_request calls this to fill the input field of a Responses API request. It is the Responses-side counterpart to openai_messages, with extra support for replaying reasoning items so the model can continue a tool-use chain correctly.

*Call graph*: called by 1 (responses_request); 11 external calls (dumps, ResponseReasoningItemParam, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, Summary (+1 more)).


##### `responses_request`  (lines 270–300)

```
def responses_request(request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the full request body for OpenAI's Responses API. It includes the model, instructions, message input, streaming settings, token limit, reasoning option, and tool definitions.

**Data flow**: It receives a ModelRequest. It converts the request's messages through responses_input, adds the system instructions, model name, maximum output tokens, streaming flag, and a request to include encrypted reasoning content. If reasoning is enabled, it adds the requested reasoning effort. If tools are available, it describes them in OpenAI's function-tool format and sets tool-choice rules. The output is a dictionary passed to the OpenAI SDK.

**Call relations**: OpenAIClient._complete_responses calls this immediately before opening a Responses API stream. It packages the high-level request into the exact shape the provider accepts.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `OpenAIClient.complete`  (lines 312–315)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Chooses which OpenAI API style to use for a model request. Callers use this one method without needing to know whether the target model needs Chat Completions or Responses.

**Data flow**: It receives a ModelRequest and reads this client's ModelSpec. If the spec says the model uses the Responses API, it returns the Responses streaming generator. Otherwise, it returns the Chat Completions streaming generator. The output is an asynchronous stream of UFO model events.

**Call relations**: This is the public entry point of OpenAIClient. It routes the request to _complete_chat or _complete_responses based on the model specification, keeping the rest of the system independent from OpenAI API surface details.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 317–346)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the keyword arguments for a Chat Completions API call. It translates the request into the provider's expected fields and adds optional reasoning and tool settings when allowed.

**Data flow**: It receives a ModelRequest. It converts messages through openai_messages, adds the model name, token budget, streaming options, and usage reporting. It asks the model spec what reasoning effort should be used, then includes it only when appropriate. If tools exist, it converts them into OpenAI function definitions and sets whether tools may run in parallel or whether one named tool must be chosen. The output is a dictionary of arguments for the SDK call.

**Call relations**: _complete_chat calls this just before starting the Chat Completions stream. It is the request-building half of the chat path; _complete_chat is the streaming and response-reading half.

*Call graph*: calls 1 internal fn (openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 348–503)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a streaming Chat Completions request and turns the provider's chunks into UFO model events. It also applies the file's retry policy and reports final token usage.

**Data flow**: It receives a ModelRequest. It builds Chat Completions arguments, opens a provider stream, and reads each incoming chunk. The first real chunk produces a ModelStreamStart event. Text chunks become TextDelta events. Tool-call starts and partial JSON arguments become ToolCallStart and ToolCallDelta events. Usage information is saved and yielded as the final event. If the provider says the answer was cut off by the token limit, it raises ModelResponseTruncated. If the provider returns retryable errors before any visible output, it waits and tries again; after visible output, errors are raised immediately.

**Call relations**: OpenAIClient.complete sends chat-surface models here. This method depends on _chat_kwargs for request construction, emits events consumed by the rest of the model engine, records retry metrics, logs provider failures, and uses the model spec to turn rejected credentials into the project's clearer key-related error.

*Call graph*: calls 1 internal fn (_chat_kwargs); called by 1 (complete); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


##### `OpenAIClient._complete_responses`  (lines 505–694)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a streaming Responses API request and converts its richer event stream into UFO model events. It supports text, tool calls, refusals, truncation, usage, and replayable reasoning items.

**Data flow**: It receives a ModelRequest, adjusts its reasoning setting according to the model spec, builds a Responses request, and opens a provider stream. Text deltas become TextDelta events. Function-call events become ToolCallStart and ToolCallDelta events. Completed reasoning items are collected and yielded only after the stream finishes, just before usage, so abandoned retry attempts do not leak reasoning into the conversation. Completed responses provide token usage; incomplete, failed, refused, or truncated responses become clear exceptions. Retryable transport and status failures are retried only before visible output has been yielded.

**Call relations**: OpenAIClient.complete sends Responses-surface models here. It calls responses_request to prepare the provider request, then translates provider-specific streaming events into the common ModelEvent language used by the rest of the system. It mirrors _complete_chat's retry and usage contract while adding Responses-only reasoning support.

*Call graph*: calls 1 internal fn (responses_request); called by 1 (complete); 12 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep, model_copy (+2 more)).


### OpenRouter extension
Adds OpenRouter as an optional OpenAI-like model provider and exposes media generation tools with workspace output and cost tracking.

### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `model calls and tool execution`

OpenRouter is a service that routes one request to many possible AI model providers. This file is the bridge between that service and the rest of the project. Without it, the system could not use the OpenRouter text models listed here, and agents would not have the OpenRouter-backed generate_image and generate_video tools.

For text chat, the file wraps OpenRouter’s streaming chat API in the project’s normal model interface. It translates the project’s request format into OpenAI-style messages, streams back text and tool-call pieces as they arrive, records token use, retries temporary provider failures, and avoids an upstream provider that returned an empty answer.

For images and videos, the file works more like a workshop order form. It first checks that the requested model, size, duration, and aspect ratio are allowed. Then it sends the job to OpenRouter, receives or downloads the generated media, saves it under generated-images/ or generated-videos/, and reports the saved paths back to the agent. It also calculates cost in micro-dollars and meters it, unless the workspace supplied its own OpenRouter key and is billed directly by OpenRouter.

The manifest at the end advertises all of this to the host system: which models exist, which tools exist, and what credential slot is needed.

#### Function details

##### `openrouter_slug`  (lines 250–260)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: Converts the project’s model name into the provider/model name format OpenRouter expects. It adds a provider prefix for familiar bare OpenAI or Anthropic names, while leaving already-prefixed names alone.

**Data flow**: It receives a model string. If the string already contains a slash, it returns it unchanged; if it looks like an OpenAI or Claude model, it adds the matching provider prefix; otherwise it passes the string through. The result is the model name sent over the OpenRouter API.

**Call relations**: When OpenRouterModelClient._create_kwargs builds the outgoing chat request, it calls this helper so the request names the model in OpenRouter’s preferred form.

*Call graph*: called by 1 (_create_kwargs).


##### `_chunk_provider`  (lines 263–268)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: Extracts which upstream provider OpenRouter used for a streamed response chunk. This matters because a provider that returns an empty completion can be excluded on the next try.

**Data flow**: It receives one streaming chat chunk from OpenRouter. It looks in the chunk’s extra metadata for a provider field and returns it as text if present; otherwise it returns nothing.

**Call relations**: OpenRouterModelClient.complete calls this while reading the stream. If the response ends up empty, the client can use the provider name gathered here to ask OpenRouter not to use that same upstream provider on a retry.

*Call graph*: called by 1 (complete).


##### `_usage_of`  (lines 271–280)

```
def _usage_of(usage: CompletionUsage) -> Usage
```

**Purpose**: Turns OpenAI-style token usage information into the project’s standard Usage object. It also separates cached prompt tokens from newly billed input tokens.

**Data flow**: It receives a CompletionUsage object from the API. It reads prompt tokens, completion tokens, and cached prompt-token details, checks that cached tokens are not greater than total prompt tokens, and returns a Usage record with input, output, and cache-read token counts.

**Call relations**: OpenRouterModelClient.complete calls this when OpenRouter sends final usage data in the stream, then yields the resulting Usage object to the rest of the model pipeline.

*Call graph*: called by 1 (complete); 1 external calls (__init__).


##### `OpenRouterModelClient.complete`  (lines 297–396)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one streaming chat completion through OpenRouter and yields the project’s normal stream events. It is the main text-model adapter in this file.

**Data flow**: It receives a ModelRequest containing the model, messages, tools, token limit, and reasoning preference. It builds the OpenRouter request, opens a streaming API call, converts incoming chunks into stream-start, text, tool-call, and usage events, retries temporary errors before any visible output, and raises a clear error if the model was cut off by the token limit. Its output is an asynchronous stream of model events.

**Call relations**: The broader model system calls this when an agent uses an OpenRouter-backed model. Inside, it hands request-building to OpenRouterModelClient._create_kwargs, provider extraction to _chunk_provider, and token accounting conversion to _usage_of.

*Call graph*: calls 3 internal fn (_create_kwargs, _chunk_provider, _usage_of); 8 external calls (__init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


##### `OpenRouterModelClient._create_kwargs`  (lines 398–427)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: Builds the exact keyword arguments sent to the OpenAI-compatible SDK for an OpenRouter chat request. It centralizes the translation from the project’s request shape to OpenRouter’s wire format.

**Data flow**: It receives a ModelRequest and a set of upstream providers to ignore. It converts messages, chooses the OpenRouter model slug, adds streaming and usage options, adds reasoning settings when needed, and includes tool schemas if tools are available. It returns a dictionary ready to pass to the SDK call.

**Call relations**: OpenRouterModelClient.complete calls this right before making each API attempt. This helper in turn calls openrouter_slug and openai_messages so the outbound request matches OpenRouter’s OpenAI-like API.

*Call graph*: calls 1 internal fn (openrouter_slug); called by 1 (complete); 1 external calls (openai_messages).


##### `_model_client`  (lines 430–434)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: Creates an OpenRouterModelClient for one model spec and API key. It is the factory used by model registrations.

**Data flow**: It receives a ModelSpec and a secret key. It creates an OpenAI SDK client pointed at OpenRouter’s base URL, wraps it with the spec in an OpenRouterModelClient, and returns that client.

**Call relations**: _openrouter stores this as the client factory for each registered OpenRouter text model, so later model calls can construct the right backend client when credentials are available.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 437–454)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: Builds a ModelSpec for one OpenRouter text model. A ModelSpec is the project’s catalog entry describing price, context size, provider, credential needs, and capabilities.

**Data flow**: It receives a model id, price, knowledge cutoff date, and optional context-window size. It fills in OpenRouter-specific defaults such as provider name, API key slot, reasoning support, and chat API surface, then returns a ModelSpec.

**Call relations**: The module uses this helper while defining OPENROUTER_MODEL_SPECS. Those specs are later exposed by manifest so the rest of the system can offer these models like any built-in model.

*Call graph*: 1 external calls (__init__).


##### `GenerateImageInput._within_model_limits`  (lines 519–543)

```
def _within_model_limits(self) -> 'GenerateImageInput'
```

**Purpose**: Checks that an image-generation request is possible for the chosen model. It catches bad combinations early, before OpenRouter rejects them later.

**Data flow**: It reads the already-parsed image request: model, number of images, resolution, and aspect ratio. It compares those choices with the allowlisted limits for that model, raises a clear validation error for unsupported choices, and fills in the default resolution when the chosen model uses resolution tiers. The validated input object comes out ready to send.

**Call relations**: This runs automatically as part of Pydantic input validation for the generate_image tool. By the time OpenRouterImages.generate receives the args, the request has already been narrowed to what the selected model can serve.


##### `_reported_cost_micro_usd`  (lines 551–563)

```
def _reported_cost_micro_usd(usage: object) -> int | None
```

**Purpose**: Reads a cost reported by OpenRouter and converts it into micro-USD, where one dollar is represented as one million units. It understands both normal OpenRouter billing and bring-your-own-key upstream billing reports.

**Data flow**: It receives a usage-like object, usually from an API response. It looks for a positive cost field, then for a positive upstream_inference_cost field, converts the dollar amount to micro-USD, and returns it. If no positive cost is found, it returns nothing so callers can fall back to list pricing.

**Call relations**: OpenRouterImages._charge uses this for image costs, and OpenRouterVideos._job uses it when reading a video job’s reported usage. This keeps cost parsing consistent across media tools.

*Call graph*: called by 2 (_charge, _job).


##### `OpenRouterImages.generate`  (lines 596–633)

```
async def generate(self, ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Performs one complete image-generation tool call: send the request, save the returned images, calculate cost, meter the workspace when appropriate, and return usable results to the agent.

**Data flow**: It receives a ToolContext and validated GenerateImageInput. It gets the OpenRouter key, posts the prompt and model settings to the image API, turns provider errors into tool errors, decodes returned images, writes them into the workspace, calculates the charge, optionally records image usage, and returns a ToolResult containing JSON file information plus the image data.

**Call relations**: The generate_image tool reaches this through _generate_image. During the flow it delegates error wording to OpenRouterImages._refusal, image decoding to OpenRouterImages._images, file writing to OpenRouterImages._save, and pricing to OpenRouterImages._charge.

*Call graph*: calls 5 internal fn (meter_images, _charge, _images, _refusal, _save); 6 external calls (__init__, __init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterImages._refusal`  (lines 635–650)

```
def _refusal(self, args: GenerateImageInput, response: httpx.Response) -> str
```

**Purpose**: Turns an OpenRouter image API error response into a short message the agent can understand and react to. This might describe a rejected prompt, bad parameter, or billing problem.

**Data flow**: It receives the original image arguments and the HTTP response. It tries to read a JSON error message, falls back to the raw response text, trims it to a safe length, and returns a sentence saying no image was generated.

**Call relations**: OpenRouterImages.generate calls this when the image API returns an error status. The returned text becomes the content of an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterImages._images`  (lines 652–682)

```
def _images(self, args: GenerateImageInput, body: object) -> tuple[GeneratedImage, ...]
```

**Purpose**: Extracts real image files from OpenRouter’s response and rejects unusable results. It prevents missing or oversized image data from being written into the workspace.

**Data flow**: It receives the image request and the decoded response body. It walks through the response data list, base64-decodes each image, assigns a media type when missing, checks the byte-size limit, and returns GeneratedImage records. If no image data is found or an image is too large, it raises an OpenRouterImageError.

**Call relations**: OpenRouterImages.generate calls this after a successful image API response. The generated image records are then passed to OpenRouterImages._save and also included in the tool result as ImageContent.

*Call graph*: called by 1 (generate); 3 external calls (__init__, __init__, b64decode).


##### `OpenRouterImages._save`  (lines 684–691)

```
async def _save(self, ctx: ToolContext, args: GenerateImageInput, index: int, image: GeneratedImage) -> str
```

**Purpose**: Writes one generated image into the workspace with the right file extension. This turns the API response into a file the user can later share or inspect.

**Data flow**: It receives the tool context, original image arguments, an image number, and a GeneratedImage. It chooses a suffix from the media type, builds a path under generated-images/, writes the raw bytes through the sandbox, and returns the saved path.

**Call relations**: OpenRouterImages.generate calls this once for each decoded image. The returned paths are placed in the JSON summary sent back to the agent.

*Call graph*: called by 1 (generate).


##### `OpenRouterImages._charge`  (lines 693–700)

```
def _charge(self, body: object, args: GenerateImageInput, images: int) -> int
```

**Purpose**: Calculates the cost of an image generation in micro-USD. It prefers the actual cost reported by OpenRouter and falls back to the model’s listed per-image price.

**Data flow**: It receives the response body, image arguments, and number of images. It reads usage information, asks _reported_cost_micro_usd to extract a reported charge, and returns that if present. Otherwise it multiplies the selected model’s list price by the number of images.

**Call relations**: OpenRouterImages.generate calls this after images have been successfully decoded and saved. Its result is used both in the returned JSON and, when appropriate, in ToolContext.meter_images.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 1 (generate).


##### `_generate_image`  (lines 703–708)

```
async def _generate_image(ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Connects the registered generate_image tool to the OpenRouterImages implementation. It is the thin handler the tool system calls.

**Data flow**: It receives a ToolContext and validated image arguments. It checks that extension context is available, builds an OpenRouterImages helper using the extension credentials and configured test transport, then returns that helper’s generate result.

**Call relations**: GENERATE_IMAGE_TOOL names this as its handler. When an agent invokes generate_image, the tool framework calls this function, which hands the real work to OpenRouterImages.generate.

*Call graph*: 1 external calls (__init__).


##### `GenerateVideoInput._within_model_limits`  (lines 765–789)

```
def _within_model_limits(self) -> 'GenerateVideoInput'
```

**Purpose**: Checks that a video-generation request fits the selected model’s limits. It also chooses a default resolution so billing and generation agree on what was requested.

**Data flow**: It reads the parsed video request: model, duration, resolution, and aspect ratio. It checks duration and aspect ratio against the model’s allowed range, fills in the model’s default resolution if none was provided, and rejects unsupported resolution choices. The validated object is returned for use by the video tool.

**Call relations**: This runs automatically during Pydantic validation for the generate_video tool. It means OpenRouterVideos.generate receives requests that should already match the allowlisted model capabilities.


##### `OpenRouterVideos.generate`  (lines 831–872)

```
async def generate(self, ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Performs one complete video-generation tool call. Because video generation takes time, it starts a job, waits for it to finish, downloads the MP4, saves it, calculates cost, and reports the saved file.

**Data flow**: It receives a ToolContext and validated GenerateVideoInput. It gets the OpenRouter key, posts the video request, turns immediate API errors into tool errors, reads the returned job, polls until the job settles, reports failed jobs as tool errors, downloads the completed video, saves it into the workspace, calculates cost, optionally records video usage, and returns a ToolResult with file and cost information.

**Call relations**: _generate_video calls this when the generate_video tool is invoked. The function coordinates OpenRouterVideos._job, OpenRouterVideos._settled, OpenRouterVideos._download, OpenRouterVideos._save, OpenRouterVideos._charge, and the error-message helpers.

*Call graph*: calls 8 internal fn (meter_videos, _charge, _download, _failure, _job, _refusal, _save, _settled); 5 external calls (__init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterVideos._refusal`  (lines 874–889)

```
def _refusal(self, args: GenerateVideoInput, response: httpx.Response) -> str
```

**Purpose**: Turns an immediate OpenRouter video API error into a short message for the agent. This covers cases where no video job was started.

**Data flow**: It receives the video arguments and HTTP response. It tries to extract a JSON error message, otherwise uses the response text, trims it, and returns a message saying the model generated no video.

**Call relations**: OpenRouterVideos.generate calls this if the initial POST to the video API fails. Its message becomes the content of an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterVideos._job`  (lines 891–906)

```
def _job(self, body: object) -> VideoJob
```

**Purpose**: Reads OpenRouter’s description of a video job into a small VideoJob record. It makes sure the response contains enough information to poll or report the job.

**Data flow**: It receives a decoded response body from either job creation or polling. It extracts the job id, status, optional error message, and optional reported cost; if id or status is missing, it raises OpenRouterVideoError. It returns a VideoJob object.

**Call relations**: OpenRouterVideos.generate calls this after creating the job, and OpenRouterVideos._settled calls it after each poll response. It also uses _reported_cost_micro_usd so completed jobs can carry accurate reported cost.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 2 (_settled, generate); 2 external calls (__init__, __init__).


##### `OpenRouterVideos._settled`  (lines 908–929)

```
async def _settled(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> VideoJob
```

**Purpose**: Polls a video job until it is no longer pending or in progress. It prevents the tool call from waiting forever on an external provider job.

**Data flow**: It receives the video args, an HTTP client, and the current VideoJob. While the job is still pending or running, it checks a timeout, sleeps for the polling interval, requests the latest job state, and converts that response with OpenRouterVideos._job. It returns the final job state or raises an OpenRouterVideoError on timeout or failed polling.

**Call relations**: OpenRouterVideos.generate calls this after the initial video job is accepted. Once this returns, generate decides whether to report a provider failure or download the finished video.

*Call graph*: calls 1 internal fn (_job); called by 1 (generate); 4 external calls (__init__, sleep, get, monotonic).


##### `OpenRouterVideos._failure`  (lines 931–935)

```
def _failure(self, args: GenerateVideoInput, job: VideoJob) -> str
```

**Purpose**: Builds a clear message for a video job that finished without producing a video. It uses the provider’s own error text when available.

**Data flow**: It receives the original video args and the final VideoJob. It chooses the job’s error message if present, otherwise describes the final status, trims the detail, and returns a no-video message.

**Call relations**: OpenRouterVideos.generate calls this when polling ends in a status other than completed. The returned text is sent back as an error ToolResult so the agent can revise the request.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._download`  (lines 937–956)

```
async def _download(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> bytes
```

**Purpose**: Downloads the completed MP4 file and checks that it is present and not too large. This protects the workspace from empty or oversized provider output.

**Data flow**: It receives the video args, HTTP client, and completed VideoJob. It requests the job’s content, raises OpenRouterVideoError for download failures, empty content, or files above the size limit, and returns the raw video bytes.

**Call relations**: OpenRouterVideos.generate calls this only after OpenRouterVideos._settled returns a completed job. The resulting bytes are then passed to OpenRouterVideos._save.

*Call graph*: called by 1 (generate); 2 external calls (__init__, get).


##### `OpenRouterVideos._save`  (lines 958–962)

```
async def _save(self, ctx: ToolContext, args: GenerateVideoInput, video: bytes) -> str
```

**Purpose**: Writes the finished video into the workspace as an MP4 file. This creates the file path that can be returned to the agent and shared with a user.

**Data flow**: It receives the tool context, original video arguments, and raw video bytes. It builds a path under generated-videos/ using the requested name, writes the bytes through the sandbox, and returns the saved path.

**Call relations**: OpenRouterVideos.generate calls this after downloading a completed video. The saved path is included in the JSON summary returned by the tool.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._charge`  (lines 964–972)

```
def _charge(self, args: GenerateVideoInput, job: VideoJob) -> int
```

**Purpose**: Calculates the cost of a video generation in micro-USD. It uses the provider-reported cost when available, otherwise it computes a fallback from model, resolution, and duration.

**Data flow**: It receives validated video args and the final VideoJob. If the job contains a reported cost, it returns that. Otherwise it looks up the per-second rate for the chosen model and resolution and multiplies it by the requested duration.

**Call relations**: OpenRouterVideos.generate calls this after the video has been saved. The result is included in the tool output and, when appropriate, passed to ToolContext.meter_videos.

*Call graph*: called by 1 (generate).


##### `_generate_video`  (lines 975–980)

```
async def _generate_video(ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Connects the registered generate_video tool to the OpenRouterVideos implementation. It is a small adapter between the tool framework and the video helper class.

**Data flow**: It receives a ToolContext and validated video arguments. It verifies that extension context exists, creates an OpenRouterVideos helper with credentials and optional test transport, and returns the helper’s generate result.

**Call relations**: GENERATE_VIDEO_TOOL uses this as its handler. When an agent calls generate_video, the tool framework enters here and the actual workflow continues in OpenRouterVideos.generate.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 992–1008)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It announces the extension name and version, the OpenRouter text models, the image and video tools, and the credential slot needed for the API key.

**Data flow**: It takes no input. It builds a Manifest containing model specs, tool definitions, and a CredentialSlot explaining the OpenRouter API key, then returns that manifest to the extension loader.

**Call relations**: The extension system calls this when discovering or loading the OpenRouter extension. The returned manifest is how the rest of the project learns what this file provides.

*Call graph*: 2 external calls (__init__, __init__).

## 📊 State Registers Touched

- `reg-model-catalog` — The lookup table of available AI models, providers, routing details, capabilities, and pricing metadata.
- `reg-tool-catalog` — The runtime menu of tools the agent may call, including built-ins and extension-provided tools.
- `reg-conversation-transcript` — The durable history of conversations, messages, speakers, titles, context, and results.
- `reg-accounting-ledger` — Usage, cost, spend limits, prepaid balances, billing exports, and price versions for workspace spending.
- `reg-observability-context` — Trace IDs, metrics, logs, and sanitized operational events used to understand work across services and turns.
- `reg-self-improvement-state` — Offline replay, failure-analysis, prompt-experiment, and governed update-proposal state used by self-improvement jobs.
- `reg-prompt-version-state` — Prompt-render metadata such as system-prompt digests, contributed sections, cutoff/version information, and replay identifiers attached to turns for change detection and evaluation.
- `reg-provider-client-pools` — Per-process reusable transport/client state for external providers such as model APIs, search and embedding services, connector brokers, browser providers, billing services, and related retry or throttle windows.
