const READ_DEFAULT_LIMIT = 2000;
const LINE_CHAR_CAP = 2000;
const BINARY_SNIFF_BYTES = 8192;
const IMAGE_MAX_BYTES = 5 * 1024 * 1024;
const REPLACEMENT = 0xfffd;
const UNIT_CHUNK = 4096;

const IMAGE_MEDIA_TYPES = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
};

const IMAGE_MAGIC = [
  ["image/png", [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]],
  ["image/jpeg", [0xff, 0xd8, 0xff]],
  ["image/gif", [0x47, 0x49, 0x46, 0x38, 0x37, 0x61]],
  ["image/gif", [0x47, 0x49, 0x46, 0x38, 0x39, 0x61]],
];

const BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

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

function capLine(line) {
  if (!over(line, LINE_CHAR_CAP)) return line;
  return capped(line, LINE_CHAR_CAP) + "... [+" + (points(line).length - LINE_CHAR_CAP) + " chars]";
}

function numberLines(lines, start, width) {
  return lines.map(
    (line, index) => String(start + index).padStart(width) + "\t" + capLine(line)
  );
}

function base64(bytes) {
  const parts = [];
  let index = 0;
  for (; index + 2 < bytes.length; index += 3) {
    const word = (bytes[index] << 16) | (bytes[index + 1] << 8) | bytes[index + 2];
    parts.push(
      BASE64_ALPHABET[word >> 18],
      BASE64_ALPHABET[(word >> 12) & 63],
      BASE64_ALPHABET[(word >> 6) & 63],
      BASE64_ALPHABET[word & 63]
    );
  }
  if (index + 1 === bytes.length) {
    const word = bytes[index] << 16;
    parts.push(BASE64_ALPHABET[word >> 18], BASE64_ALPHABET[(word >> 12) & 63], "=", "=");
  } else if (index + 2 === bytes.length) {
    const word = (bytes[index] << 16) | (bytes[index + 1] << 8);
    parts.push(
      BASE64_ALPHABET[word >> 18],
      BASE64_ALPHABET[(word >> 12) & 63],
      BASE64_ALPHABET[(word >> 6) & 63],
      "="
    );
  }
  return parts.join("");
}

function pythonBytesRepr(bytes) {
  let single = false;
  let double = false;
  const parts = [];
  for (const byte of bytes) {
    if (byte === 0x27) single = true;
    if (byte === 0x22) double = true;
  }
  const quote = single && !double ? '"' : "'";
  for (const byte of bytes) {
    if (byte === 0x5c) parts.push("\\\\");
    else if (byte === quote.charCodeAt(0)) parts.push("\\" + quote);
    else if (byte === 0x09) parts.push("\\t");
    else if (byte === 0x0a) parts.push("\\n");
    else if (byte === 0x0d) parts.push("\\r");
    else if (byte >= 0x20 && byte < 0x7f) parts.push(String.fromCharCode(byte));
    else parts.push("\\x" + byte.toString(16).padStart(2, "0"));
  }
  return "b" + quote + parts.join("") + quote;
}

function imageMediaType(bytes) {
  for (const entry of IMAGE_MAGIC) {
    if (entry[1].every((byte, index) => bytes[index] === byte)) return entry[0];
  }
  const riff = [0x52, 0x49, 0x46, 0x46].every((byte, index) => bytes[index] === byte);
  const webp = [0x57, 0x45, 0x42, 0x50].every((byte, index) => bytes[index + 8] === byte);
  if (riff && webp && bytes.length >= 12) return "image/webp";
  return null;
}

function readImage(path) {
  const bytes = readFile(path, "binary");
  if (bytes.length > IMAGE_MAX_BYTES) {
    throw refusal(
      path + " is " + bytes.length + " bytes; over the " + IMAGE_MAX_BYTES +
        "-byte image read cap. Resize it (e.g. with a bash tool) before reading."
    );
  }
  const mediaType = imageMediaType(bytes);
  if (mediaType === null) {
    throw refusal(
      path + " has an image extension but its bytes are not png/jpeg/gif/webp (they start with " +
        pythonBytesRepr(bytes.subarray(0, 8)) + "). Re-export it as a real image, then read it again."
    );
  }
  return {
    path: path,
    type: "image",
    media_type: mediaType,
    data: base64(bytes),
    size_bytes: bytes.length,
  };
}

function windowed(params) {
  const path = params.path;
  const offset = Math.max(Math.trunc(Number(params.offset || 1)), 1);
  const limit = Math.trunc(Number(params.limit || READ_DEFAULT_LIMIT));
  if (statMode(path) === null) throw refusal(path + " not found");
  const kind = suffix(path);
  if (kind in IMAGE_MEDIA_TYPES) return readImage(path);
  if (kind === ".pdf") throw refusal(path + " is a pdf; a pdf read runs on the deploy");
  if (kind === ".pptx") throw refusal(path + " is a pptx; a pptx read runs on the deploy");
  const bytes = readFile(path, "binary");
  if (bytes.length === 0) {
    return {
      path: path,
      content: "",
      total_lines: 0,
      start_line: offset,
      lines_returned: 0,
      remaining_lines: 0,
      next_offset: null,
      truncated: false,
      is_empty: true,
    };
  }
  if (BINARY_EXTENSIONS.has(kind)) throw refusal(path + " is a binary file");
  const sniff = Math.min(bytes.length, BINARY_SNIFF_BYTES);
  for (let index = 0; index < sniff; index++) {
    if (bytes[index] === 0) throw refusal(path + " is a binary file");
  }
  const lines = decodeUtf8(bytes).split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  const total = lines.length;
  const window = lines.slice(offset - 1, offset - 1 + limit);
  const returned = window.length;
  const consumed = offset - 1 + returned;
  const remaining = Math.max(total - consumed, 0);
  const width = String(total).length;
  return {
    path: path,
    content: numberLines(window, offset, width).join("\n"),
    total_lines: total,
    start_line: offset,
    lines_returned: returned,
    remaining_lines: remaining,
    next_offset: remaining > 0 ? consumed + 1 : null,
    truncated: remaining > 0,
    is_empty: false,
  };
}

function main(params) {
  try {
    print(JSON.stringify(windowed(params)));
  } catch (error) {
    if (!error || !error.refused) throw error;
    print(JSON.stringify({ error: error.message }));
  }
}
