const CHANGES_INPUT_MAX_CHARS = 20000;
const CHANGES_INPUT_MAX_LINES = 2000;
const CHANGES_PATCH_MAX_CHARS = 10000;
const CHANGES_MAX_REPOSITORIES = 10;
const CHANGES_MAX_FILES = 100;
const CHANGES_TOTAL_MAX_CHARS = 250000;
const UNTRACKED_STATUS = "??";
const STATUS_ENTRY_MIN = 3;
const REPOSITORY_FIELD = "R";
const DIFF_FIELD = "D";
const DIFF_SECTION = "\ndiff --git ";
const OLD_HEADER = "--- ";
const NEW_HEADER = "+++ ";
const NO_DEVICE = "/dev/null";
const NO_NEWLINE = "\n\\ No newline at end of file\n";
const REPLACEMENT = 0xfffd;
const UNIT_CHUNK = 4096;
const FIELD_SEPARATOR = "\u0000";
const LINE_BOUNDARY = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/g;
const GIT_ESCAPES = { a: 7, b: 8, f: 12, n: 10, r: 13, t: 9, v: 11, "\\": 92, '"': 34 };

const BINARY_EXTENSIONS = new Set([
  ".7z", ".a", ".avi", ".bin", ".bmp", ".bz2", ".class", ".dat", ".db", ".dll", ".dylib", ".eot",
  ".exe", ".flac", ".gif", ".gz", ".heic", ".ico", ".jar", ".jpeg", ".jpg", ".m4a", ".mkv", ".mov",
  ".mp3", ".mp4", ".o", ".obj", ".ogg", ".otf", ".pdf", ".png", ".pyc", ".pyo", ".rar", ".so",
  ".sqlite", ".sqlite3", ".svgz", ".tar", ".tiff", ".ttf", ".war", ".wasm", ".wav", ".webm",
  ".webp", ".woff", ".woff2", ".xz", ".zip",
]);

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

function points(text) {
  return Array.from(text);
}

function over(text, cap) {
  return text.length > cap && points(text).length > cap;
}

function capped(text, cap) {
  return text.length <= cap ? text : points(text).slice(0, cap).join("");
}

function suffix(path) {
  const name = path.slice(path.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || dot >= name.length - 1) return "";
  return name.slice(dot).toLowerCase();
}

function relativeTo(path, root) {
  return path.slice(root.length).replace(/^\//, "");
}

function joined(base, name) {
  const components = (base + "/" + name).split("/").filter((part) => part && part !== ".");
  return (base.slice(0, 1) === "/" ? "/" : "") + components.join("/");
}

function splitLines(text) {
  const lines = [];
  let start = 0;
  let found;
  LINE_BOUNDARY.lastIndex = 0;
  while ((found = LINE_BOUNDARY.exec(text)) !== null) {
    lines.push(text.slice(start, found.index + found[0].length));
    start = found.index + found[0].length;
  }
  if (start < text.length) lines.push(text.slice(start));
  return lines;
}

function listing(name) {
  const items = decodeUtf8(readFile(WORKDIR + "/" + name, "binary")).split(FIELD_SEPARATOR);
  if (items.length && items[items.length - 1] === "") items.pop();
  return items;
}

function checkouts(items) {
  const found = [];
  let current = null;
  for (let index = 0; index < items.length; index++) {
    if (items[index] === REPOSITORY_FIELD) {
      current = { path: items[index + 1], entries: [], diff: "" };
      found.push(current);
      index += 1;
    } else if (items[index] === DIFF_FIELD) {
      current.diff = items[index + 1];
      index += 1;
    } else if (items[index].length > STATUS_ENTRY_MIN) {
      current.entries.push(items[index]);
    }
  }
  const kept = [];
  for (const repository of found) {
    const inside = found.some(
      (outer) => outer !== repository && repository.path.indexOf(outer.path + "/") === 0
    );
    if (inside) continue;
    kept.push(repository);
    if (kept.length >= CHANGES_MAX_REPOSITORIES) break;
  }
  return kept;
}

function unquote(value) {
  const bytes = [];
  let index = 0;
  while (index < value.length) {
    if (value[index] !== "\\") {
      bytes.push(value.charCodeAt(index) & 0xff);
      index += 1;
    } else if (value[index + 1] >= "0" && value[index + 1] <= "7") {
      bytes.push(parseInt(value.slice(index + 1, index + 4), 8) & 0xff);
      index += 4;
    } else {
      const escape = GIT_ESCAPES[value[index + 1]];
      bytes.push(escape === undefined ? value.charCodeAt(index + 1) & 0xff : escape);
      index += 2;
    }
  }
  return decodeUtf8(new Uint8Array(bytes));
}

function diffPath(line) {
  let value = line.slice(4);
  if (value.slice(-1) === "\t") value = value.slice(0, -1);
  if (value === NO_DEVICE) return null;
  if (value.slice(0, 1) === '"' && value.slice(-1) === '"') value = unquote(value.slice(1, -1));
  const prefix = value.slice(0, 2);
  return prefix === "a/" || prefix === "b/" ? value.slice(2) : value;
}

function trackedPatches(diff) {
  const patches = new Map();
  for (const section of diff.split(DIFF_SECTION)) {
    const lines = section.split("\n");
    let header = -1;
    for (let index = 0; index < lines.length; index++) {
      if (lines[index].indexOf(OLD_HEADER) === 0) {
        header = index;
        break;
      }
    }
    if (header < 0 || (lines[header + 1] || "").indexOf(NEW_HEADER) !== 0) continue;
    const body = lines.slice(header).join("\n");
    const patch = body.slice(-1) === "\n" ? body : body + "\n";
    for (const line of lines.slice(header, header + 2)) {
      const name = diffPath(line);
      if (name !== null) patches.set(name, patch);
    }
  }
  return patches;
}

function addedText(path) {
  if (BINARY_EXTENSIONS.has(suffix(path))) return null;
  try {
    const bytes = readFile(path, "binary");
    return decodeUtf8(bytes.subarray(0, CHANGES_INPUT_MAX_CHARS + 1));
  } catch (error) {
    return null;
  }
}

function addedPatch(name, text) {
  let lines = splitLines(capped(text, CHANGES_INPUT_MAX_CHARS));
  let truncated = over(text, CHANGES_INPUT_MAX_CHARS);
  if (lines.length > CHANGES_INPUT_MAX_LINES) {
    lines = lines.slice(0, CHANGES_INPUT_MAX_LINES);
    truncated = true;
  }
  let patch = "";
  if (lines.length) {
    const span = lines.length === 1 ? "1" : "1," + lines.length;
    patch =
      OLD_HEADER + NO_DEVICE + "\n" + NEW_HEADER + "b/" + name + "\n@@ -0,0 +" + span + " @@\n";
    for (const line of lines) {
      patch += "+" + line;
      if (line.slice(-1) !== "\n") patch += NO_NEWLINE;
    }
  }
  return {
    patch: capped(patch, CHANGES_PATCH_MAX_CHARS),
    truncated: truncated || over(patch, CHANGES_PATCH_MAX_CHARS),
  };
}

function repositoryChange(path, status, name, patches) {
  const patch = patches.get(name);
  if (patch !== undefined) {
    return {
      patch: capped(patch, CHANGES_PATCH_MAX_CHARS),
      truncated: over(patch, CHANGES_PATCH_MAX_CHARS),
    };
  }
  const added = status === UNTRACKED_STATUS || status.indexOf("A") === 0;
  const text = added ? addedText(path) : null;
  return text === null ? { patch: "", truncated: false } : addedPatch(name, text);
}

function uncommitted(p) {
  const listed = [];
  let budget = CHANGES_TOTAL_MAX_CHARS;
  for (const repository of checkouts(listing(p.enum))) {
    const prefix = relativeTo(repository.path, p.workspace);
    const patches = trackedPatches(repository.diff);
    for (const entry of repository.entries) {
      if (listed.length >= CHANGES_MAX_FILES || budget <= 0) {
        return { changes: listed, truncated: true };
      }
      const name = entry.slice(3);
      const change = repositoryChange(
        joined(repository.path, name),
        entry.slice(0, 2),
        name,
        patches
      );
      listed.push({
        path: joined(prefix, name),
        patch: change.patch,
        truncated: change.truncated,
      });
      budget -= points(change.patch).length;
    }
  }
  return { changes: listed, truncated: false };
}

function main(p) {
  try {
    print(JSON.stringify(uncommitted(p)));
  } catch (error) {
    if (!error || !error.refused) throw error;
    print(JSON.stringify({ error: error.message }));
  }
}
