# Browser BUA Wire-Data Validation  `stage-24.3`

This stage is behind-the-scenes support for the browser automation part of the system. It does not click buttons itself. Instead, it defines the “vocabulary” and safety checks used when the system talks to a real browser.

The actions.py file describes the allowed browser actions in a clear, structured way: click here, type this text, scroll, wait, or take a screenshot. Think of it as a standard order form. Other parts of the system can fill in that form, and the browser worker can read it without guessing what was meant.

The wire.py file checks data coming back from Chrome DevTools Protocol, the browser’s control and inspection channel. That data arrives as JSON, a common text format for nested values like numbers, strings, lists, and objects. wire.py defines the expected shapes of those values and rejects anything surprising early.

Together, these files keep browser commands and browser responses predictable. They make the main browser engine simpler, because it can rely on checked inputs instead of defending against every possible malformed value.

## Files in this stage

### Browser Wire Data Shapes
Defines the validated action and Chrome DevTools Protocol data shapes used by the browser automation engine.

### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is like a menu of actions that a browser-driving assistant is allowed to request. Without it, different parts of the system might describe the same action in different ways, or send incomplete instructions, such as a click with no target.

It defines `ActionType`, a fixed list of action names such as `left_click`, `type`, `scroll`, and `wait`. It also defines `CLICK_ACTIONS`, a small set used to quickly recognize the actions that behave like mouse clicks.

The main pieces are Pydantic models. Pydantic is a library that checks that data has the expected shape before the program uses it. `ScrollParameters` describes how far and in which direction to scroll. For example, it can say “scroll down by one screen” or “jump to the maximum end of the page.” `ComputerAction` describes one complete browser instruction. Depending on the action, it may include a screen coordinate, typed text, a keyboard shortcut, scroll settings, a wait duration, a drag start point, or an element reference found earlier on the page.

The important idea is that this file does not perform the actions itself. It defines the safe, predictable instruction format that another part of the browser automation system can later execute.


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `request handling`

The browser engine talks to Chrome using the Chrome DevTools Protocol, often called CDP. In plain terms, CDP is a JSON-based remote control channel for the browser: commands and browser replies are sent as JSON values. JSON is flexible, which is useful, but it also means a value might not be the kind of thing the engine needs. For example, the engine may expect an object but receive a string, or expect a non-empty string but receive nothing.

This file is the small safety gate at that boundary. It names the allowed JSON shapes with type aliases like Json and JsonDict, then provides simple “narrowing” functions. A narrowing function takes a loose JSON value and proves it is a more specific kind of value, such as a dictionary, string, integer, or list. If the value is missing where an empty object or empty list is acceptable, it supplies that empty value. If the value is wrong, it raises ValidationError.

That matters because the error is reported as a recoverable tool problem instead of letting confusing bad data travel deeper into the system. Like a mailroom that rejects a package with the wrong label before it reaches the wrong desk, this file keeps protocol surprises close to where they enter.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function turns an incoming JSON value into a dictionary-like object when the caller expects an object. It also treats a missing value as an empty object, which is useful when optional object fields are absent.

**Data flow**: It receives a JSON value and a text path that says where that value came from, such as a field name in a browser reply. If the value is None, it returns an empty dictionary. If the value is already a dictionary, it returns it unchanged. If it is anything else, it raises ValidationError with a message naming the bad path.

**Call relations**: Other browser-wire parsing code calls this when it reaches a place in Chrome’s JSON response that must be an object. If the check fails, it hands off to ValidationError so the caller can report a clean boundary error instead of continuing with the wrong kind of data.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function confirms that an incoming JSON value is a usable string. It requires the string to be non-empty, because an empty string would not be meaningful for the places this helper is meant to protect.

**Data flow**: It receives a loose JSON value plus a path describing where the value was found. If the value is a non-empty string, that string comes out. For None, an empty string, or any non-string value, it raises ValidationError explaining that the path must contain a non-empty string.

**Call relations**: Parsing code uses this when it needs a definite text value from Chrome’s JSON. When the value is not acceptable, this function creates a ValidationError, keeping the failure at the protocol boundary rather than letting an invalid string-like value move onward.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function confirms that an incoming JSON value is an integer. Callers use it when a browser reply must contain a whole number, such as an identifier or count.

**Data flow**: It receives a JSON value and a path label. If the value is an integer, it returns that integer unchanged. If the value is missing or has any other shape, it raises ValidationError saying that the named path must be an integer.

**Call relations**: Browser-response parsing code calls this at points where Chrome’s JSON must provide a whole number. On bad input, it hands the problem to ValidationError so the larger engine can treat it as a recoverable validation failure.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function turns an incoming JSON value into a list when the caller expects an array of JSON values. It treats a missing value as an empty list, which fits optional list fields.

**Data flow**: It receives a JSON value and a path string. If the value is a list, it returns that list unchanged. If the value is None, it returns an empty list. For any other kind of value, it raises ValidationError with a message saying the named path must be a list.

**Call relations**: Parsing code calls this when it reads a part of a Chrome response that should be an array. If the response contains something else, this function raises ValidationError immediately, preventing later code from looping over a value that is not actually a list.

*Call graph*: 1 external calls (__init__).
