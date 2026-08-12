const OUTPUT_MODES = ["content", "files_with_matches", "count"];
const GREP_DEFAULT_HEAD = 100;
const GREP_LINE_CHAR_CAP = 2000;
const BINARY_SNIFF_BYTES = 8192;
const REPLACEMENT = 0xfffd;
const UNIT_CHUNK = 4096;
const FIELD_SEPARATOR = "\u0000";
const REGEX_LITERAL = /[.*+?^${}()|[\]\\/]/g;

const TEXT_EXTENSIONS = new Set([
  ".bash", ".bat", ".c", ".cc", ".cfg", ".cjs", ".clj", ".cljs", ".conf", ".cpp", ".cs", ".css",
  ".csv", ".cxx", ".dart", ".dockerfile", ".editorconfig", ".env", ".erl", ".ex", ".exs", ".fish",
  ".fs", ".gitattributes", ".gitignore", ".go", ".gql", ".gradle", ".graphql", ".h", ".hh", ".hpp",
  ".hs", ".htm", ".html", ".ini", ".ipynb", ".java", ".jl", ".js", ".json", ".jsonl", ".jsx", ".kt",
  ".kts", ".less", ".lock", ".log", ".lua", ".m", ".makefile", ".markdown", ".md", ".mjs", ".mk",
  ".ml", ".mm", ".php", ".pl", ".pm", ".properties", ".proto", ".ps1", ".py", ".pyi", ".r", ".rb",
  ".rs", ".rst", ".sass", ".scala", ".scss", ".sh", ".sql", ".swift", ".tf", ".tfvars", ".toml",
  ".ts", ".tsv", ".tsx", ".txt", ".xml", ".yaml", ".yml", ".zsh",
]);

const BINARY_EXTENSIONS = new Set([
  ".7z", ".a", ".avi", ".bin", ".bmp", ".bz2", ".class", ".dat", ".db", ".dll", ".dylib", ".eot",
  ".exe", ".flac", ".gif", ".gz", ".heic", ".ico", ".jar", ".jpeg", ".jpg", ".m4a", ".mkv", ".mov",
  ".mp3", ".mp4", ".o", ".obj", ".ogg", ".otf", ".pdf", ".png", ".pyc", ".pyo", ".rar", ".so",
  ".sqlite", ".sqlite3", ".svgz", ".tar", ".tiff", ".ttf", ".war", ".wasm", ".wav", ".webm",
  ".webp", ".woff", ".woff2", ".xz", ".zip",
]);

const TYPE_EXTENSIONS = {
  py: [".py", ".pyi"],
  js: [".js", ".jsx", ".mjs", ".cjs"],
  ts: [".ts", ".tsx"],
  rust: [".rs"],
  go: [".go"],
  java: [".java"],
  c: [".c", ".h"],
  cpp: [".cc", ".cpp", ".cxx", ".hpp", ".hh", ".h"],
  md: [".md", ".markdown"],
  json: [".json", ".jsonl"],
  yaml: [".yaml", ".yml"],
  html: [".html", ".htm"],
  css: [".css", ".scss", ".sass", ".less"],
  sh: [".sh", ".bash", ".zsh"],
};

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

function basename(path) {
  return path.slice(path.lastIndexOf("/") + 1);
}

function parent(path) {
  const cut = path.lastIndexOf("/");
  return cut <= 0 ? "/" : path.slice(0, cut);
}

function suffix(path) {
  const name = basename(path);
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || dot >= name.length - 1) return "";
  return name.slice(dot).toLowerCase();
}

function compare(left, right) {
  if (left === right) return 0;
  return left < right ? -1 : 1;
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

function listing(name) {
  const items = decodeUtf8(readFile(WORKDIR + "/" + name, "binary")).split(FIELD_SEPARATOR);
  if (items.length && items[items.length - 1] === "") items.pop();
  return items;
}

function containedText(path) {
  if (BINARY_EXTENSIONS.has(suffix(path))) return null;
  let bytes;
  try {
    bytes = readFile(path, "binary");
  } catch (error) {
    return null;
  }
  const limit = Math.min(bytes.length, BINARY_SNIFF_BYTES);
  for (let index = 0; index < limit; index++) if (bytes[index] === 0) return null;
  return decodeUtf8(bytes);
}

function admitted(entries, p) {
  if (p.glob) {
    const glob = fnmatch(p.glob);
    return entries.filter((path) => glob.test(basename(path)) || glob.test(path));
  }
  if (p.type) {
    const wanted = new Set(TYPE_EXTENSIONS[p.type] || []);
    return entries.filter((path) => wanted.has(suffix(path)));
  }
  return entries.filter((path) => TEXT_EXTENSIONS.has(suffix(path)));
}

function candidates(p) {
  const entries = listing(p.enum);
  const position = new Map();
  entries.forEach((path, index) => {
    if (!position.has(path)) position.set(path, index);
  });
  const ranked = admitted(entries, p).map((path) => ({
    path: path,
    dir: position.has(parent(path)) ? position.get(parent(path)) : -1,
    name: basename(path),
  }));
  ranked.sort((left, right) => left.dir - right.dir || compare(left.name, right.name));
  return ranked.map((entry) => entry.path);
}

function grepRegex(p, extra) {
  let flags = extra;
  if (p.multiline) flags += "sm";
  if (p.ignore_case) flags += "i";
  try {
    return new RegExp(p.pattern, flags);
  } catch (error) {
    throw refusal("invalid regex: " + error.message);
  }
}

function matchLine(line) {
  if (!over(line, GREP_LINE_CHAR_CAP)) return line;
  const extra = points(line).length - GREP_LINE_CHAR_CAP;
  return capped(line, GREP_LINE_CHAR_CAP) + "... [+" + extra + " chars]";
}

function scan(p) {
  const mode = p.output_mode || "files_with_matches";
  if (OUTPUT_MODES.indexOf(mode) < 0) throw refusal("invalid output_mode: " + mode);
  const limit = p.head_limit || GREP_DEFAULT_HEAD;
  const context = p.context || 0;
  const before = (p.before_context || 0) || context;
  const after = (p.after_context || 0) || context;
  const regex = grepRegex(p, "");
  const files = candidates(p);

  if (mode === "files_with_matches") {
    const matched = [];
    for (const path of files) {
      const text = containedText(path);
      if (text === null) continue;
      const hit = p.multiline
        ? regex.test(text)
        : text.split("\n").some((line) => regex.test(line));
      if (!hit) continue;
      matched.push(path);
      if (matched.length >= limit) {
        return { files: matched, count: matched.length, truncated: true };
      }
    }
    return { files: matched, count: matched.length, truncated: false };
  }

  if (mode === "count") {
    const counting = p.multiline ? grepRegex(p, "g") : regex;
    const counts = [];
    let total = 0;
    for (const path of files) {
      const text = containedText(path);
      if (text === null) continue;
      const hit = p.multiline
        ? Array.from(text.matchAll(counting)).length
        : text.split("\n").filter((line) => regex.test(line)).length;
      if (!hit) continue;
      counts.push({ file: path, count: hit });
      total += hit;
    }
    return { counts: counts, total_matches: total };
  }

  const matches = [];
  for (const path of files) {
    const text = containedText(path);
    if (text === null) continue;
    const lines = text.split("\n");
    for (let index = 0; index < lines.length; index++) {
      if (!regex.test(lines[index])) continue;
      const entry = { file: path, line: index + 1, content: matchLine(lines[index]) };
      const rows = [];
      for (let row = Math.max(0, index - before); row < index; row++) {
        rows.push({ line: row + 1, content: matchLine(lines[row]) });
      }
      for (let row = index + 1; row < Math.min(lines.length, index + 1 + after); row++) {
        rows.push({ line: row + 1, content: matchLine(lines[row]) });
      }
      if (rows.length) entry.context = rows;
      matches.push(entry);
      if (matches.length >= limit) {
        return { matches: matches, count: matches.length, truncated: true };
      }
    }
  }
  return { matches: matches, count: matches.length, truncated: false };
}

function main(p) {
  try {
    print(JSON.stringify(scan(p)));
  } catch (error) {
    if (!error || !error.refused) throw error;
    print(JSON.stringify({ error: error.message }));
  }
}
