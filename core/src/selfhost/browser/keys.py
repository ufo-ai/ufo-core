"""Keyboard synthesis: US layout + mac editing commands vendored from playwright, and the CDP key
events a `type`/`key` action dispatches.

`US_KEYBOARD_LAYOUT` and `MAC_EDITING_COMMANDS` are vendored verbatim from playwright
(packages/playwright-core/src/server/usKeyboardLayout.ts and macEditingCommands.ts; Apache-2.0,
Copyright Microsoft Corporation / Google Inc.). `key_down`/`key_up`/`press_combo`/`type_text` mirror
playwright's input.ts and chromium/crInput.ts so a synthesized keystroke carries the same
key/code/keyCode/modifiers/commands Chrome expects."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from selfhost.browser.wire import Json, JsonDict, ValidationError

CdpCall = tuple[str, JsonDict]

TYPE_CHAR_LIMIT = 32
KEYPAD_LOCATION = 3
MODIFIER_KEYS = frozenset({"Alt", "Control", "Meta", "Shift"})
MODIFIER_BITS = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}


@dataclass(frozen=True)
class KeyDefinition:
    key: str
    key_code: int = 0
    key_code_without_location: int = 0
    shift_key: str = ""
    shift_key_code: int = 0
    text: str = ""
    location: int = 0


US_KEYBOARD_LAYOUT: dict[str, KeyDefinition] = {
    "Escape": KeyDefinition(key="Escape", key_code=27),
    "F1": KeyDefinition(key="F1", key_code=112),
    "F2": KeyDefinition(key="F2", key_code=113),
    "F3": KeyDefinition(key="F3", key_code=114),
    "F4": KeyDefinition(key="F4", key_code=115),
    "F5": KeyDefinition(key="F5", key_code=116),
    "F6": KeyDefinition(key="F6", key_code=117),
    "F7": KeyDefinition(key="F7", key_code=118),
    "F8": KeyDefinition(key="F8", key_code=119),
    "F9": KeyDefinition(key="F9", key_code=120),
    "F10": KeyDefinition(key="F10", key_code=121),
    "F11": KeyDefinition(key="F11", key_code=122),
    "F12": KeyDefinition(key="F12", key_code=123),
    "Backquote": KeyDefinition(key="`", key_code=192, shift_key="~"),
    "Digit1": KeyDefinition(key="1", key_code=49, shift_key="!"),
    "Digit2": KeyDefinition(key="2", key_code=50, shift_key="@"),
    "Digit3": KeyDefinition(key="3", key_code=51, shift_key="#"),
    "Digit4": KeyDefinition(key="4", key_code=52, shift_key="$"),
    "Digit5": KeyDefinition(key="5", key_code=53, shift_key="%"),
    "Digit6": KeyDefinition(key="6", key_code=54, shift_key="^"),
    "Digit7": KeyDefinition(key="7", key_code=55, shift_key="&"),
    "Digit8": KeyDefinition(key="8", key_code=56, shift_key="*"),
    "Digit9": KeyDefinition(key="9", key_code=57, shift_key="("),
    "Digit0": KeyDefinition(key="0", key_code=48, shift_key=")"),
    "Minus": KeyDefinition(key="-", key_code=189, shift_key="_"),
    "Equal": KeyDefinition(key="=", key_code=187, shift_key="+"),
    "Backslash": KeyDefinition(key="\\", key_code=220, shift_key="|"),
    "Backspace": KeyDefinition(key="Backspace", key_code=8),
    "Tab": KeyDefinition(key="Tab", key_code=9),
    "KeyQ": KeyDefinition(key="q", key_code=81, shift_key="Q"),
    "KeyW": KeyDefinition(key="w", key_code=87, shift_key="W"),
    "KeyE": KeyDefinition(key="e", key_code=69, shift_key="E"),
    "KeyR": KeyDefinition(key="r", key_code=82, shift_key="R"),
    "KeyT": KeyDefinition(key="t", key_code=84, shift_key="T"),
    "KeyY": KeyDefinition(key="y", key_code=89, shift_key="Y"),
    "KeyU": KeyDefinition(key="u", key_code=85, shift_key="U"),
    "KeyI": KeyDefinition(key="i", key_code=73, shift_key="I"),
    "KeyO": KeyDefinition(key="o", key_code=79, shift_key="O"),
    "KeyP": KeyDefinition(key="p", key_code=80, shift_key="P"),
    "BracketLeft": KeyDefinition(key="[", key_code=219, shift_key="{"),
    "BracketRight": KeyDefinition(key="]", key_code=221, shift_key="}"),
    "CapsLock": KeyDefinition(key="CapsLock", key_code=20),
    "KeyA": KeyDefinition(key="a", key_code=65, shift_key="A"),
    "KeyS": KeyDefinition(key="s", key_code=83, shift_key="S"),
    "KeyD": KeyDefinition(key="d", key_code=68, shift_key="D"),
    "KeyF": KeyDefinition(key="f", key_code=70, shift_key="F"),
    "KeyG": KeyDefinition(key="g", key_code=71, shift_key="G"),
    "KeyH": KeyDefinition(key="h", key_code=72, shift_key="H"),
    "KeyJ": KeyDefinition(key="j", key_code=74, shift_key="J"),
    "KeyK": KeyDefinition(key="k", key_code=75, shift_key="K"),
    "KeyL": KeyDefinition(key="l", key_code=76, shift_key="L"),
    "Semicolon": KeyDefinition(key=";", key_code=186, shift_key=":"),
    "Quote": KeyDefinition(key="'", key_code=222, shift_key='"'),
    "Enter": KeyDefinition(key="Enter", key_code=13, text="\r"),
    "ShiftLeft": KeyDefinition(key="Shift", key_code=160, key_code_without_location=16, location=1),
    "KeyZ": KeyDefinition(key="z", key_code=90, shift_key="Z"),
    "KeyX": KeyDefinition(key="x", key_code=88, shift_key="X"),
    "KeyC": KeyDefinition(key="c", key_code=67, shift_key="C"),
    "KeyV": KeyDefinition(key="v", key_code=86, shift_key="V"),
    "KeyB": KeyDefinition(key="b", key_code=66, shift_key="B"),
    "KeyN": KeyDefinition(key="n", key_code=78, shift_key="N"),
    "KeyM": KeyDefinition(key="m", key_code=77, shift_key="M"),
    "Comma": KeyDefinition(key=",", key_code=188, shift_key="<"),
    "Period": KeyDefinition(key=".", key_code=190, shift_key=">"),
    "Slash": KeyDefinition(key="/", key_code=191, shift_key="?"),
    "ShiftRight": KeyDefinition(
        key="Shift", key_code=161, key_code_without_location=16, location=2
    ),
    "ControlLeft": KeyDefinition(
        key="Control", key_code=162, key_code_without_location=17, location=1
    ),
    "MetaLeft": KeyDefinition(key="Meta", key_code=91, location=1),
    "AltLeft": KeyDefinition(key="Alt", key_code=164, key_code_without_location=18, location=1),
    "Space": KeyDefinition(key=" ", key_code=32),
    "AltRight": KeyDefinition(key="Alt", key_code=165, key_code_without_location=18, location=2),
    "AltGraph": KeyDefinition(key="AltGraph", key_code=225),
    "MetaRight": KeyDefinition(key="Meta", key_code=92, location=2),
    "ContextMenu": KeyDefinition(key="ContextMenu", key_code=93),
    "ControlRight": KeyDefinition(
        key="Control", key_code=163, key_code_without_location=17, location=2
    ),
    "PrintScreen": KeyDefinition(key="PrintScreen", key_code=44),
    "ScrollLock": KeyDefinition(key="ScrollLock", key_code=145),
    "Pause": KeyDefinition(key="Pause", key_code=19),
    "PageUp": KeyDefinition(key="PageUp", key_code=33),
    "PageDown": KeyDefinition(key="PageDown", key_code=34),
    "Insert": KeyDefinition(key="Insert", key_code=45),
    "Delete": KeyDefinition(key="Delete", key_code=46),
    "Home": KeyDefinition(key="Home", key_code=36),
    "End": KeyDefinition(key="End", key_code=35),
    "ArrowLeft": KeyDefinition(key="ArrowLeft", key_code=37),
    "ArrowUp": KeyDefinition(key="ArrowUp", key_code=38),
    "ArrowRight": KeyDefinition(key="ArrowRight", key_code=39),
    "ArrowDown": KeyDefinition(key="ArrowDown", key_code=40),
    "AudioVolumeMute": KeyDefinition(key="AudioVolumeMute", key_code=173),
    "AudioVolumeDown": KeyDefinition(key="AudioVolumeDown", key_code=174),
    "AudioVolumeUp": KeyDefinition(key="AudioVolumeUp", key_code=175),
    "MediaTrackNext": KeyDefinition(key="MediaTrackNext", key_code=176),
    "MediaTrackPrevious": KeyDefinition(key="MediaTrackPrevious", key_code=177),
    "MediaPlayPause": KeyDefinition(key="MediaPlayPause", key_code=179),
    "NumLock": KeyDefinition(key="NumLock", key_code=144),
    "NumpadDivide": KeyDefinition(key="/", key_code=111, location=3),
    "NumpadMultiply": KeyDefinition(key="*", key_code=106, location=3),
    "NumpadSubtract": KeyDefinition(key="-", key_code=109, location=3),
    "Numpad7": KeyDefinition(
        key="Home", key_code=36, shift_key="7", shift_key_code=103, location=3
    ),
    "Numpad8": KeyDefinition(
        key="ArrowUp", key_code=38, shift_key="8", shift_key_code=104, location=3
    ),
    "Numpad9": KeyDefinition(
        key="PageUp", key_code=33, shift_key="9", shift_key_code=105, location=3
    ),
    "Numpad4": KeyDefinition(
        key="ArrowLeft", key_code=37, shift_key="4", shift_key_code=100, location=3
    ),
    "Numpad5": KeyDefinition(
        key="Clear", key_code=12, shift_key="5", shift_key_code=101, location=3
    ),
    "Numpad6": KeyDefinition(
        key="ArrowRight", key_code=39, shift_key="6", shift_key_code=102, location=3
    ),
    "NumpadAdd": KeyDefinition(key="+", key_code=107, location=3),
    "Numpad1": KeyDefinition(key="End", key_code=35, shift_key="1", shift_key_code=97, location=3),
    "Numpad2": KeyDefinition(
        key="ArrowDown", key_code=40, shift_key="2", shift_key_code=98, location=3
    ),
    "Numpad3": KeyDefinition(
        key="PageDown", key_code=34, shift_key="3", shift_key_code=99, location=3
    ),
    "Numpad0": KeyDefinition(
        key="Insert", key_code=45, shift_key="0", shift_key_code=96, location=3
    ),
    "NumpadDecimal": KeyDefinition(
        key="\u0000", key_code=46, shift_key=".", shift_key_code=110, location=3
    ),
    "NumpadEnter": KeyDefinition(key="Enter", key_code=13, text="\r", location=3),
}

MAC_EDITING_COMMANDS: dict[str, tuple[str, ...]] = {
    "Backspace": ("deleteBackward:",),
    "Enter": ("insertNewline:",),
    "NumpadEnter": ("insertNewline:",),
    "Escape": ("cancelOperation:",),
    "ArrowUp": ("moveUp:",),
    "ArrowDown": ("moveDown:",),
    "ArrowLeft": ("moveLeft:",),
    "ArrowRight": ("moveRight:",),
    "F5": ("complete:",),
    "Delete": ("deleteForward:",),
    "Home": ("scrollToBeginningOfDocument:",),
    "End": ("scrollToEndOfDocument:",),
    "PageUp": ("scrollPageUp:",),
    "PageDown": ("scrollPageDown:",),
    "Shift+Backspace": ("deleteBackward:",),
    "Shift+Enter": ("insertNewline:",),
    "Shift+NumpadEnter": ("insertNewline:",),
    "Shift+Escape": ("cancelOperation:",),
    "Shift+ArrowUp": ("moveUpAndModifySelection:",),
    "Shift+ArrowDown": ("moveDownAndModifySelection:",),
    "Shift+ArrowLeft": ("moveLeftAndModifySelection:",),
    "Shift+ArrowRight": ("moveRightAndModifySelection:",),
    "Shift+F5": ("complete:",),
    "Shift+Delete": ("deleteForward:",),
    "Shift+Home": ("moveToBeginningOfDocumentAndModifySelection:",),
    "Shift+End": ("moveToEndOfDocumentAndModifySelection:",),
    "Shift+PageUp": ("pageUpAndModifySelection:",),
    "Shift+PageDown": ("pageDownAndModifySelection:",),
    "Shift+Numpad5": ("delete:",),
    "Control+Tab": ("selectNextKeyView:",),
    "Control+Enter": ("insertLineBreak:",),
    "Control+NumpadEnter": ("insertLineBreak:",),
    "Control+Quote": ("insertSingleQuoteIgnoringSubstitution:",),
    "Control+KeyA": ("moveToBeginningOfParagraph:",),
    "Control+KeyB": ("moveBackward:",),
    "Control+KeyD": ("deleteForward:",),
    "Control+KeyE": ("moveToEndOfParagraph:",),
    "Control+KeyF": ("moveForward:",),
    "Control+KeyH": ("deleteBackward:",),
    "Control+KeyK": ("deleteToEndOfParagraph:",),
    "Control+KeyL": ("centerSelectionInVisibleArea:",),
    "Control+KeyN": ("moveDown:",),
    "Control+KeyO": ("insertNewlineIgnoringFieldEditor:", "moveBackward:"),
    "Control+KeyP": ("moveUp:",),
    "Control+KeyT": ("transpose:",),
    "Control+KeyV": ("pageDown:",),
    "Control+KeyY": ("yank:",),
    "Control+Backspace": ("deleteBackwardByDecomposingPreviousCharacter:",),
    "Control+ArrowUp": ("scrollPageUp:",),
    "Control+ArrowDown": ("scrollPageDown:",),
    "Control+ArrowLeft": ("moveToLeftEndOfLine:",),
    "Control+ArrowRight": ("moveToRightEndOfLine:",),
    "Shift+Control+Enter": ("insertLineBreak:",),
    "Shift+Control+NumpadEnter": ("insertLineBreak:",),
    "Shift+Control+Tab": ("selectPreviousKeyView:",),
    "Shift+Control+Quote": ("insertDoubleQuoteIgnoringSubstitution:",),
    "Shift+Control+KeyA": ("moveToBeginningOfParagraphAndModifySelection:",),
    "Shift+Control+KeyB": ("moveBackwardAndModifySelection:",),
    "Shift+Control+KeyE": ("moveToEndOfParagraphAndModifySelection:",),
    "Shift+Control+KeyF": ("moveForwardAndModifySelection:",),
    "Shift+Control+KeyN": ("moveDownAndModifySelection:",),
    "Shift+Control+KeyP": ("moveUpAndModifySelection:",),
    "Shift+Control+KeyV": ("pageDownAndModifySelection:",),
    "Shift+Control+Backspace": ("deleteBackwardByDecomposingPreviousCharacter:",),
    "Shift+Control+ArrowUp": ("scrollPageUp:",),
    "Shift+Control+ArrowDown": ("scrollPageDown:",),
    "Shift+Control+ArrowLeft": ("moveToLeftEndOfLineAndModifySelection:",),
    "Shift+Control+ArrowRight": ("moveToRightEndOfLineAndModifySelection:",),
    "Alt+Backspace": ("deleteWordBackward:",),
    "Alt+Enter": ("insertNewlineIgnoringFieldEditor:",),
    "Alt+NumpadEnter": ("insertNewlineIgnoringFieldEditor:",),
    "Alt+Escape": ("complete:",),
    "Alt+ArrowUp": ("moveBackward:", "moveToBeginningOfParagraph:"),
    "Alt+ArrowDown": ("moveForward:", "moveToEndOfParagraph:"),
    "Alt+ArrowLeft": ("moveWordLeft:",),
    "Alt+ArrowRight": ("moveWordRight:",),
    "Alt+Delete": ("deleteWordForward:",),
    "Alt+PageUp": ("pageUp:",),
    "Alt+PageDown": ("pageDown:",),
    "Shift+Alt+Backspace": ("deleteWordBackward:",),
    "Shift+Alt+Enter": ("insertNewlineIgnoringFieldEditor:",),
    "Shift+Alt+NumpadEnter": ("insertNewlineIgnoringFieldEditor:",),
    "Shift+Alt+Escape": ("complete:",),
    "Shift+Alt+ArrowUp": ("moveParagraphBackwardAndModifySelection:",),
    "Shift+Alt+ArrowDown": ("moveParagraphForwardAndModifySelection:",),
    "Shift+Alt+ArrowLeft": ("moveWordLeftAndModifySelection:",),
    "Shift+Alt+ArrowRight": ("moveWordRightAndModifySelection:",),
    "Shift+Alt+Delete": ("deleteWordForward:",),
    "Shift+Alt+PageUp": ("pageUp:",),
    "Shift+Alt+PageDown": ("pageDown:",),
    "Control+Alt+KeyB": ("moveWordBackward:",),
    "Control+Alt+KeyF": ("moveWordForward:",),
    "Control+Alt+Backspace": ("deleteWordBackward:",),
    "Shift+Control+Alt+KeyB": ("moveWordBackwardAndModifySelection:",),
    "Shift+Control+Alt+KeyF": ("moveWordForwardAndModifySelection:",),
    "Shift+Control+Alt+Backspace": ("deleteWordBackward:",),
    "Meta+NumpadSubtract": ("cancel:",),
    "Meta+Backspace": ("deleteToBeginningOfLine:",),
    "Meta+ArrowUp": ("moveToBeginningOfDocument:",),
    "Meta+ArrowDown": ("moveToEndOfDocument:",),
    "Meta+ArrowLeft": ("moveToLeftEndOfLine:",),
    "Meta+ArrowRight": ("moveToRightEndOfLine:",),
    "Shift+Meta+NumpadSubtract": ("cancel:",),
    "Shift+Meta+Backspace": ("deleteToBeginningOfLine:",),
    "Shift+Meta+ArrowUp": ("moveToBeginningOfDocumentAndModifySelection:",),
    "Shift+Meta+ArrowDown": ("moveToEndOfDocumentAndModifySelection:",),
    "Shift+Meta+ArrowLeft": ("moveToLeftEndOfLineAndModifySelection:",),
    "Shift+Meta+ArrowRight": ("moveToRightEndOfLineAndModifySelection:",),
    "Meta+KeyA": ("selectAll:",),
    "Meta+KeyC": ("copy:",),
    "Meta+KeyX": ("cut:",),
    "Meta+KeyV": ("paste:",),
    "Meta+KeyZ": ("undo:",),
    "Shift+Meta+KeyZ": ("redo:",),
}


@dataclass(frozen=True)
class KeyDescription:
    key: str
    key_code: int
    key_code_without_location: int
    code: str
    text: str
    location: int
    shifted: KeyDescription | None = None


KEY_ALIASES = {
    "ShiftLeft": ("Shift",),
    "ControlLeft": ("Control",),
    "AltLeft": ("Alt",),
    "MetaLeft": ("Meta",),
    "Enter": ("\n", "\r"),
}


def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]:
    result: dict[str, KeyDescription] = {}
    for code, definition in layout.items():
        base = KeyDescription(
            key=definition.key,
            key_code=definition.key_code,
            key_code_without_location=definition.key_code_without_location or definition.key_code,
            code=code,
            text=definition.key if len(definition.key) == 1 else definition.text,
            location=definition.location,
        )
        shifted = None
        if definition.shift_key:
            shifted = replace(
                base,
                key=definition.shift_key,
                text=definition.shift_key,
                key_code=definition.shift_key_code or definition.key_code,
            )
        result[code] = replace(base, shifted=shifted)
        for alias in KEY_ALIASES.get(code, ()):
            result[alias] = base
        if definition.location:
            continue
        if len(base.key) == 1:
            result[base.key] = base
        if shifted is not None:
            result[shifted.key] = shifted
    return result


LAYOUT_CLOSURE = _build_layout_closure(US_KEYBOARD_LAYOUT)

CUA_KEY_ALIASES: dict[str, str] = {
    "alt": "Alt",
    "option": "Alt",
    "ctrl": "Control",
    "control": "Control",
    "cmd": "Meta",
    "meta": "Meta",
    "super": "Meta",
    "super_l": "Meta",
    "super_r": "Meta",
    "win": "Meta",
    "shift": "Shift",
    "enter": "Enter",
    "return": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "page_up": "PageUp",
    "pagedown": "PageDown",
    "page_down": "PageDown",
    "arrowup": "ArrowUp",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "space": "Space",
    "capslock": "CapsLock",
    "minus": "Minus",
    "plus": "+",
    "equal": "Equal",
    "comma": "Comma",
    "grave": "Backquote",
    "question": "?",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
}


@dataclass
class KeyboardState:
    pressed_modifiers: set[str] = field(default_factory=set)
    pressed_keys: set[str] = field(default_factory=set)


def modifiers_mask(modifiers: set[str]) -> int:
    return sum(bit for name, bit in MODIFIER_BITS.items() if name in modifiers)


def _description_for(state: KeyboardState, key: str) -> KeyDescription:
    description = LAYOUT_CLOSURE.get(key)
    if description is None:
        raise ValidationError(f"unknown key {key!r}")
    if "Shift" in state.pressed_modifiers and description.shifted is not None:
        description = description.shifted
    if state.pressed_modifiers - {"Shift"}:
        description = replace(description, text="")
    return description


def _mac_commands(code: str, modifiers: set[str]) -> list[str]:
    parts = [name for name in ("Shift", "Control", "Alt", "Meta") if name in modifiers]
    shortcut = "+".join([*parts, code])
    commands = MAC_EDITING_COMMANDS.get(shortcut, ())
    return [command[:-1] for command in commands if not command.startswith("insert")]


def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall:
    description = _description_for(state, key)
    auto_repeat = description.code in state.pressed_keys
    state.pressed_keys.add(description.code)
    if description.key in MODIFIER_KEYS:
        state.pressed_modifiers.add(description.key)
    commands: list[Json] = [
        *(_mac_commands(description.code, state.pressed_modifiers) if is_mac else ())
    ]
    return (
        "Input.dispatchKeyEvent",
        {
            "type": "keyDown" if description.text else "rawKeyDown",
            "modifiers": modifiers_mask(state.pressed_modifiers),
            "windowsVirtualKeyCode": description.key_code_without_location,
            "code": description.code,
            "commands": commands,
            "key": description.key,
            "text": description.text,
            "unmodifiedText": description.text,
            "autoRepeat": auto_repeat,
            "location": description.location,
            "isKeypad": description.location == KEYPAD_LOCATION,
        },
    )


def key_up(state: KeyboardState, key: str) -> CdpCall:
    description = _description_for(state, key)
    state.pressed_modifiers.discard(description.key)
    state.pressed_keys.discard(description.code)
    return (
        "Input.dispatchKeyEvent",
        {
            "type": "keyUp",
            "modifiers": modifiers_mask(state.pressed_modifiers),
            "key": description.key,
            "windowsVirtualKeyCode": description.key_code_without_location,
            "code": description.code,
            "location": description.location,
        },
    )


def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]:
    parts = [part.strip() for part in combo.split("+") if part.strip()]
    keys = [CUA_KEY_ALIASES.get(part.lower(), part) for part in parts]
    if not keys:
        raise ValidationError(f"empty key combo {combo!r}")
    calls = [key_down(state, key, is_mac) for key in keys]
    calls.extend(key_up(state, key) for key in reversed(keys))
    return calls


def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]:
    """Long text pastes in one insertText call; per-char synthesis only stays for short text, where
    key-by-key handlers and autocomplete matter."""
    if len(text) > TYPE_CHAR_LIMIT:
        return [("Input.insertText", {"text": text})]
    calls: list[CdpCall] = []
    for char in text:
        if char in LAYOUT_CLOSURE:
            calls.append(key_down(state, char, is_mac))
            calls.append(key_up(state, char))
        else:
            calls.append(("Input.insertText", {"text": char}))
    return calls
