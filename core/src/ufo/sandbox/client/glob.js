const GLOB_MAX_RESULTS = 1000;
const RECURSIVE = "**";
const REPLACEMENT = 0xfffd;
const UNIT_CHUNK = 4096;
const FIELD_SEPARATOR = "\u0000";
const REGEX_LITERAL = /[.*+?^${}()|[\]\\/]/g;

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

function fnmatch(pattern) {
  let expression = "";
  let index = 0;
  while (index < pattern.length) {
    const character = pattern[index];
    index += 1;
    if (character === "*") {
      expression += ".*";
    } else if (character === "?") {
      expression += ".";
    } else if (character === "[") {
      let end = index;
      if (pattern[end] === "!") end += 1;
      if (pattern[end] === "]") end += 1;
      while (end < pattern.length && pattern[end] !== "]") end += 1;
      if (end >= pattern.length) {
        expression += "\\[";
      } else {
        let inside = pattern.slice(index, end).replace(/\\/g, "\\\\");
        index = end + 1;
        if (inside === "!") {
          expression += ".";
        } else {
          if (inside[0] === "!") inside = "^" + inside.slice(1);
          else if (inside[0] === "^" || inside[0] === "[") inside = "\\" + inside;
          expression += "[" + inside + "]";
        }
      }
    } else {
      expression += character.replace(REGEX_LITERAL, "\\$&");
    }
  }
  return new RegExp("^" + expression + "$", "s");
}

function parts(path) {
  return path.split("/").filter((part) => part && part !== ".");
}

function relativeTo(path, root) {
  return path.slice(root.length).replace(/^\//, "");
}

function inside(path, root) {
  return path.indexOf(root + "/") === 0;
}

function containedPattern(pattern, root) {
  if (parts(pattern).indexOf("..") >= 0) {
    throw refusal("pattern '" + pattern + "' leaves " + root);
  }
  if (pattern.slice(0, 1) !== "/") return pattern;
  if (pattern !== root && pattern.indexOf(root + "/") !== 0) {
    throw refusal("pattern '" + pattern + "' escapes " + root);
  }
  const relative = relativeTo(pattern, root);
  if (!relative) throw refusal("pattern '" + pattern + "' matches a directory root");
  return relative;
}

function matchParts(tests, test, names, name) {
  if (test === tests.length) return name === names.length;
  if (tests[test] === RECURSIVE) {
    for (let skip = name; skip <= names.length; skip++) {
      if (matchParts(tests, test + 1, names, skip)) return true;
    }
    return false;
  }
  if (name >= names.length) return false;
  return tests[test].test(names[name]) && matchParts(tests, test + 1, names, name + 1);
}

function globMatcher(pattern) {
  const components = parts(pattern);
  const tests = components.map((part) => (part === RECURSIVE ? RECURSIVE : fnmatch(part)));
  const directoryOnly = components[components.length - 1] === RECURSIVE;
  return (relative) => {
    if (directoryOnly) return false;
    return matchParts(tests, 0, parts(relative), 0);
  };
}

function excluded(p) {
  const names = p.exclude_names || [];
  for (const name of names) {
    if (typeof name !== "string" || !name || name.indexOf("/") >= 0 || name.indexOf("\\") >= 0) {
      throw refusal("exclude_names must be a list of path component names");
    }
  }
  return new Set(names);
}

function measured(name) {
  const items = decodeUtf8(readFile(WORKDIR + "/" + name, "binary")).split(FIELD_SEPARATOR);
  const cut = items.indexOf("");
  const paths = items.slice(0, cut);
  const lines = items
    .slice(cut + 1)
    .join(FIELD_SEPARATOR)
    .split("\n")
    .filter((line) => line);
  if (lines.length !== paths.length) {
    throw refusal("a file left the walk between the listing and the measurement");
  }
  return paths.map((path, index) => {
    const gap = lines[index].indexOf(" ");
    return {
      path: path,
      size: parseInt(lines[index].slice(0, gap), 10),
      modified: parseFloat(lines[index].slice(gap + 1)),
    };
  });
}

function match(p) {
  const names = excluded(p);
  const pattern = containedPattern(p.pattern, p.workspace);
  const root = p.pattern.slice(0, 1) === "/" || !p.path ? p.workspace : p.path;
  const matcher = globMatcher(pattern);
  const hits = measured(p.enum).filter(
    (hit) =>
      inside(hit.path, root) &&
      matcher(relativeTo(hit.path, root)) &&
      !parts(relativeTo(hit.path, p.workspace)).some((part) => names.has(part))
  );
  hits.sort((left, right) => right.modified - left.modified);
  const truncated = hits.length > GLOB_MAX_RESULTS;
  const files = hits.slice(0, GLOB_MAX_RESULTS).map((hit) => ({
    path: hit.path,
    size: hit.size,
    modified: hit.modified,
  }));
  const result = { files: files, count: files.length, truncated: truncated };
  if (truncated) {
    result.truncated_message =
      "showing " + GLOB_MAX_RESULTS + " of " + hits.length + " matches; refine the pattern";
  }
  return result;
}

function main(p) {
  try {
    print(JSON.stringify(match(p)));
  } catch (error) {
    if (!error || !error.refused) throw error;
    print(JSON.stringify({ error: error.message }));
  }
}
