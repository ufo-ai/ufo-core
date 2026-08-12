const LINE_CHAR_CAP = 2000;
const EDIT_SNIPPET_CONTEXT = 4;
const EDIT_SNIPPET_MAX_CHARS = 2000;
const REPLACEMENT = 0xfffd;
const UNIT_CHUNK = 4096;

const BASE64_VALUES = (() => {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const values = {};
  for (let index = 0; index < alphabet.length; index++) values[alphabet[index]] = index;
  values["-"] = 62;
  values["_"] = 63;
  return values;
})();

function refusal(message) {
  const error = new Error(message);
  error.refused = true;
  return error;
}

function pushCodePoint(units, point) {
  if (point < 0x10000) {
    units.push(point);
    return;
  }
  const rest = point - 0x10000;
  units.push(0xd800 + (rest >> 10), 0xdc00 + (rest & 0x3ff));
}

function continues(byte, low, high) {
  return byte !== undefined && byte >= low && byte <= high;
}

function sequence(bytes, index) {
  const lead = bytes[index];
  if (lead >= 0xc2 && lead <= 0xdf) {
    if (!continues(bytes[index + 1], 0x80, 0xbf)) return [null, 1];
    return [((lead & 0x1f) << 6) | (bytes[index + 1] & 0x3f), 2];
  }
  if (lead >= 0xe0 && lead <= 0xef) {
    const low = lead === 0xe0 ? 0xa0 : 0x80;
    const high = lead === 0xed ? 0x9f : 0xbf;
    if (!continues(bytes[index + 1], low, high)) return [null, 1];
    if (!continues(bytes[index + 2], 0x80, 0xbf)) return [null, 2];
    return [((lead & 0x0f) << 12) | ((bytes[index + 1] & 0x3f) << 6) | (bytes[index + 2] & 0x3f), 3];
  }
  if (lead >= 0xf0 && lead <= 0xf4) {
    const low = lead === 0xf0 ? 0x90 : 0x80;
    const high = lead === 0xf4 ? 0x8f : 0xbf;
    if (!continues(bytes[index + 1], low, high)) return [null, 1];
    if (!continues(bytes[index + 2], 0x80, 0xbf)) return [null, 2];
    if (!continues(bytes[index + 3], 0x80, 0xbf)) return [null, 3];
    const point =
      ((lead & 0x07) << 18) |
      ((bytes[index + 1] & 0x3f) << 12) |
      ((bytes[index + 2] & 0x3f) << 6) |
      (bytes[index + 3] & 0x3f);
    return [point, 4];
  }
  return [null, 1];
}

function decodeUtf8(bytes) {
  const parts = [];
  let units = [];
  let index = 0;
  while (index < bytes.length) {
    const lead = bytes[index];
    if (lead < 0x80) {
      units.push(lead);
      index += 1;
    } else {
      const found = sequence(bytes, index);
      if (found[0] === null) units.push(REPLACEMENT);
      else pushCodePoint(units, found[0]);
      index += found[1];
    }
    if (units.length >= UNIT_CHUNK) {
      parts.push(String.fromCharCode.apply(null, units));
      units = [];
    }
  }
  if (units.length) parts.push(String.fromCharCode.apply(null, units));
  return parts.join("");
}

function encodeUtf8(text) {
  const bytes = [];
  for (const character of text) {
    let point = character.codePointAt(0);
    if (point >= 0xd800 && point <= 0xdfff) point = REPLACEMENT;
    if (point < 0x80) {
      bytes.push(point);
    } else if (point < 0x800) {
      bytes.push(0xc0 | (point >> 6), 0x80 | (point & 0x3f));
    } else if (point < 0x10000) {
      bytes.push(0xe0 | (point >> 12), 0x80 | ((point >> 6) & 0x3f), 0x80 | (point & 0x3f));
    } else {
      bytes.push(
        0xf0 | (point >> 18),
        0x80 | ((point >> 12) & 0x3f),
        0x80 | ((point >> 6) & 0x3f),
        0x80 | (point & 0x3f)
      );
    }
  }
  return new Uint8Array(bytes);
}

function points(text) {
  return Array.from(text);
}

function over(text, cap) {
  return text.length > cap && points(text).length > cap;
}

function capped(text, cap) {
  return text.length <= cap ? text : points(text).slice(0, cap).join("");
}

function capLine(line) {
  if (!over(line, LINE_CHAR_CAP)) return line;
  return capped(line, LINE_CHAR_CAP) + "... [+" + (points(line).length - LINE_CHAR_CAP) + " chars]";
}

function numberLines(lines, start, width) {
  return lines.map((line, index) => String(start + index).padStart(width) + "\t" + capLine(line));
}

function decodeText(encoded) {
  if (typeof encoded !== "string" || encoded.length % 4 !== 0) {
    throw refusal("text must be base64 encoded");
  }
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const body = encoded.slice(0, encoded.length - padding);
  const bytes = [];
  let word = 0;
  let held = 0;
  for (const character of body) {
    const value = BASE64_VALUES[character];
    if (value === undefined) throw refusal("text must be base64 encoded");
    word = (word << 6) | value;
    held += 6;
    if (held >= 8) {
      held -= 8;
      bytes.push((word >> held) & 0xff);
    }
  }
  if (padding && body.length % 4 === 0) throw refusal("text must be base64 encoded");
  return decodeUtf8(new Uint8Array(bytes));
}

function editList(params) {
  const edits = params.edits;
  if (!Array.isArray(edits) || !edits.length) throw refusal("edits must be a non-empty list");
  return edits.map((edit) => ({
    old_string: decodeText(edit.old_string_b64),
    new_string: decodeText(edit.new_string_b64),
    replace_all: edit.replace_all === true || Boolean(edit.replace_all),
  }));
}

function occurrences(text, old) {
  if (old === "") return points(text).length + 1;
  let count = 0;
  let from = 0;
  while (true) {
    const found = text.indexOf(old, from);
    if (found < 0) return count;
    count += 1;
    from = found + old.length;
  }
}

function replaceAll(text, old, replacement) {
  if (old === "") {
    if (text === "") return replacement;
    return replacement + points(text).join(replacement) + replacement;
  }
  return text.split(old).join(replacement);
}

function replaceFirst(text, old, replacement) {
  if (old === "") return replacement + text;
  const found = text.indexOf(old);
  return text.slice(0, found) + replacement + text.slice(found + old.length);
}

function applyEdit(text, edit) {
  const count = occurrences(text, edit.old_string);
  if (count === 0) throw refusal("old_string not found");
  if (count > 1 && !edit.replace_all) {
    throw refusal(
      "search text found " + count + " times. Provide more context or use replace_all=true."
    );
  }
  if (edit.replace_all) return [replaceAll(text, edit.old_string, edit.new_string), count];
  return [replaceFirst(text, edit.old_string, edit.new_string), 1];
}

function editSnippet(text, marker) {
  const lines = text.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  const index = text.indexOf(marker);
  let hitLine = 0;
  let span = 0;
  if (index >= 0) {
    hitLine = text.slice(0, index).split("\n").length - 1;
    span = marker.split("\n").length - 1;
  }
  const start = Math.max(hitLine - EDIT_SNIPPET_CONTEXT, 0);
  const end = Math.min(hitLine + span + EDIT_SNIPPET_CONTEXT + 1, lines.length);
  const width = String(Math.max(end, 1)).length;
  return numberLines(lines.slice(start, end), start + 1, width).join("\n");
}

function edited(params) {
  const path = params.path;
  if (statMode(path) === null) throw refusal(path + " not found");
  const edits = editList(params);
  let text = decodeUtf8(readFile(path, "binary"));
  let total = 0;
  for (const edit of edits) {
    const applied = applyEdit(text, edit);
    text = applied[0];
    total += applied[1];
  }
  writeFile(path, encodeUtf8(text));
  return {
    path: path,
    message: path + ": " + total + " replacements",
    replacements: total,
    snippet: capped(editSnippet(text, edits[0].new_string), EDIT_SNIPPET_MAX_CHARS),
  };
}

function main(params) {
  try {
    print(JSON.stringify(edited(params)));
  } catch (error) {
    if (!error || !error.refused) throw error;
    print(JSON.stringify({ error: error.message }));
  }
}
