# Extension-specific protocol shapes  `stage-16.4`

This stage defines the “message shapes” used by specific extensions, rather than by the core system. It is shared behind-the-scenes support: other code relies on these definitions when it starts asking an extension to do work or when it reads events coming back.

The browser actions file is like a form book for browser control. It lists the actions an automated agent may request, such as click, type, scroll, wait, or take a screenshot, and describes what information each request must contain. That lets the system check a request before trying to drive the browser.

The browser errors file defines one special failure case: the AI model pointed to something in the browser that does not really exist, such as an invented button or page element. Naming this error separately helps the system respond to bad model output without confusing it with other problems.

The memory events file gives common event names and limits for memory recall. Together, these files keep extension communication predictable and easy to validate.

## Files in this stage

### Browser action schemas
Browser extension protocol shapes define the actions an automation agent may request and the specialized validation error for impossible browser references.

### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is like a menu of safe, well-described commands for browser automation. Instead of letting the rest of the system pass around loose dictionaries like “click here” or “scroll somehow,” it defines exact action names and the pieces of information each action may need.

The main action list is `ActionType`, which names every supported action: clicks, drag, typing, keyboard shortcuts, scrolling, screenshots, and waits. `CLICK_ACTIONS` groups the click-like actions so other code can quickly ask, “Is this one of the mouse click actions?”

The file uses Pydantic models. Pydantic is a validation library: it checks that incoming data has the expected shape and reasonable values. `ScrollParameters` describes how far and in what direction to scroll. It also allows `"max"`, which means jump as far as possible, useful for long or infinite-scrolling pages.

`ComputerAction` is the central data model. It describes one requested browser action and all possible supporting details: a screen coordinate, typed text, scroll settings, a wait duration, drag start point, or an element reference found earlier on the page. Without this file, browser-control requests would be more ambiguous and easier to misuse, making automation less reliable and harder to validate.


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `request handling`

This file is small, but it names an important failure case. In this browser automation code, an AI model may be asked to choose or describe something on a web page. Sometimes the model can “hallucinate,” meaning it confidently invents something that is not really there. For example, it might return an element reference that does not exist in the current browser page.

The file defines `HallucinationError`, which is a specialized kind of `ValidationError`. A validation error means “the data we received does not pass the rules we require.” By making hallucination its own error type, the system can react more precisely. It can distinguish “the model invented an impossible value” from other problems, such as malformed data or a missing required field.

An everyday analogy is a warehouse picker being told to fetch item shelf Z-99 when the warehouse has no such shelf. The instruction is not just inconvenient; it refers to something impossible. This error gives that situation a clear label so higher-level code can catch it, report it, retry, or steer the model back toward real browser state.


### Memory event constants
Memory extension constants provide shared event names and limits for structured memory recall events.

### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting`

The memory extension can emit a structured event when it tries to recall stored memories before producing a response. This file is the small shared reference point for that event. It defines the event name, `memory.pre_response_recall`, so code that sends the event and code that reads it do not have to each type their own version of the string. That avoids quiet mistakes, like one part saying `pre_response_recall` while another listens for `pre_reponse_recall` with a typo.

It also sets two safety limits for event data. `MAX_RECALLED_MEMORY_IDS` caps how many recalled memory IDs should be included in the event. This keeps the event compact instead of turning it into a long dump of internal details. `MAX_RECALL_ERROR_CLASS_CHARS` limits how much of an error class name should be recorded if recall fails, which keeps logs and telemetry tidy and predictable.

In everyday terms, this file is like a label maker and size guide for one kind of memory-related status note. Without it, event producers and consumers could drift apart, and memory recall logs could become inconsistent or too large.
